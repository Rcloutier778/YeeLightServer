import datetime as dt
import json
import types

import pytest

import room


class LoggerStub:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return method


class Env:
    phoneStatus = True
    pcStatus = True


class FakeInflux:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    def write(self, *args):
        if self.fail:
            raise RuntimeError("influx")
        self.calls.append(args)


class FakeBulb:
    def __init__(self, ip, power="off", bright="20", ct="2500", fail_times=0, **kwargs):
        self._ip = ip
        self.properties = {"power": power, "bright": bright, "ct": ct}
        self.calls = []
        self.fail_times = fail_times
    def get_properties(self, props):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("transient")
        return {p: self.properties[p] for p in props}
    def turn_on(self):
        self.calls.append("on")
        self.properties["power"] = "on"
    def turn_off(self):
        self.calls.append("off")
        self.properties["power"] = "off"
    def set_brightness(self, value):
        self.calls.append(("bright", value))
        self.properties["bright"] = str(value)
    def set_rgb(self, *args):
        self.calls.append(("rgb", args))
    def start_flow(self, flow):
        self.calls.append(("flow", flow))


def make_room(tmp_path, bulbs=None, state="day"):
    obj = room.Room.__new__(room.Room)
    obj.name = "LivingRoom"
    obj.bulbs = bulbs if bulbs is not None else [FakeBulb("10.0.0.15", power="on", bright="80", ct="4000")]
    obj.envState = Env()
    obj.room_dir = str(tmp_path / "room")
    obj.roomStatePath = str(tmp_path / "room" / "state")
    obj.state = state
    obj.influx_writer = FakeInflux()
    return obj


@pytest.fixture(autouse=True)
def room_globals(monkeypatch):
    room.logger = LoggerStub()
    room.bulbLog = LoggerStub()
    monkeypatch.setattr(room.time, "sleep", lambda _: None)


def test_catcherr_handles_socket_errors_and_reraises_other():
    @room.catchErr
    def socket_error():
        raise room.yeelight.BulbException("A socket error occurred when sending the command")
    assert socket_error() is None

    @room.catchErr
    def other_error():
        raise room.yeelight.BulbException("other")
    with pytest.raises(room.yeelight.BulbException):
        other_error()


def test_init_creates_room_state_and_rebuilds(monkeypatch, tmp_path):
    monkeypatch.setattr(room, "getLogger", lambda: LoggerStub())
    monkeypatch.setattr(room, "getBulbLogger", lambda: LoggerStub())
    monkeypatch.setattr(room, "ROOM_STATES_DIR", str(tmp_path / "states"))
    monkeypatch.setattr(room, "ROOM_DIR", str(tmp_path / "states" / "{room}"))
    monkeypatch.setattr(room, "ROOM_STATES_DIR", str(tmp_path / "states"))
    monkeypatch.setattr(room.yeelight, "discover_bulbs", lambda *_: [{"ip": "10.0.0.15"}])
    monkeypatch.setattr(room, "YEELIGHT_USE_SAFE_BULBS", False)
    monkeypatch.setattr(room, "rebuild_bulbs", lambda *a, **k: None, raising=False)
    fake_client = types.SimpleNamespace(write_api=lambda **kwargs: FakeInflux())
    monkeypatch.setattr(room, "InfluxDBClient", lambda **kwargs: fake_client)
    real_open = open
    monkeypatch.setattr(room.platform, "platform", lambda: "Linux-test")

    import builtins
    def fake_open(path, *args, **kwargs):
        if path == "/home/richard/influx.secret":
            return types.SimpleNamespace(read=lambda: "token", __enter__=lambda self: self, __exit__=lambda *x: None)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", fake_open)

    # Patch methods that otherwise depend on live bulbs during construction.
    monkeypatch.setattr(room.Room, "_getLastState", lambda self: {"state": "day", "phoneStatus": True, "pcStatus": True})
    monkeypatch.setattr(room.Room, "writeState", lambda self, *a, **k: setattr(self, "state", a[0]))
    monkeypatch.setattr(room.Room, "rebuild_bulbs", lambda self: None)
    obj = room.Room("LivingRoom", [], Env())
    assert obj.name == "LivingRoom"
    assert obj.state == "day"


def test_rebuild_bulbs_dynamic_and_static(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("old")])
    obj.resetFromLoggedState = lambda include_IP_states=False: None
    monkeypatch.setattr(room.yeelight, "discover_bulbs", lambda *_: [{"ip": "new"}])
    monkeypatch.setattr(room, "room_to_ips", {"LivingRoom": ["new", "safe"]})
    monkeypatch.setattr(room, "safe_room_to_ips", {"LivingRoom": {"safe"}})
    monkeypatch.setattr(room, "Bulb", FakeBulb)
    monkeypatch.setattr(room, "YEELIGHT_USE_SAFE_BULBS", True)
    monkeypatch.setattr(room, "YEELIGHT_STATIC_REBUILD", False)
    obj.rebuild_bulbs()
    assert sorted(x._ip for x in obj.bulbs) == ["new", "safe"]

    obj.bulbs = [FakeBulb("old")]
    monkeypatch.setattr(room, "YEELIGHT_STATIC_REBUILD", True)
    obj.rebuild_bulbs()
    assert sorted(x._ip for x in obj.bulbs) == ["new", "safe"]


def test_write_state_persists_and_skips_hidden_and_duplicate(monkeypatch, tmp_path):
    obj = make_room(tmp_path)
    obj.room_dir = str(tmp_path / "room")
    obj.roomStatePath = str(tmp_path / "room" / "state")
    obj.influx_writer = FakeInflux()
    monkeypatch.setattr(room.inspect, "stack", lambda: [None, None, types.SimpleNamespace(function="test")])
    monkeypatch.setattr(room, "getLogger", lambda quiet=False: room.logger)

    obj.writeState("day")
    saved = json.loads((tmp_path / "room" / "state").read_text())
    assert saved["state"] == "day"
    assert obj.influx_writer.calls
    count = len(obj.influx_writer.calls)
    obj.writeState("day")
    assert len(obj.influx_writer.calls) == count
    obj.writeState("rebuild_bulbs")
    assert obj.state == "day"


def test_write_state_custom_and_onoff(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("1", power="on", bright="40", ct="3000")])
    obj.room_dir = str(tmp_path / "room")
    obj.roomStatePath = str(tmp_path / "room" / "state")
    obj.writeState("day")
    obj.writeState("custom:3100:55")
    assert obj.state == "custom:3100:55"
    assert json.loads((tmp_path / "room" / "state").read_text())["state"] == "custom:3100:55"

    obj.writeState("on")
    assert obj.state == "on"
    obj.bulbs[0].properties.update(power="on", ct="3100", bright="55")
    obj.writeState("on")
    assert obj.influx_writer.calls


def test_get_last_state_creates_default_and_normalizes_invalid(monkeypatch, tmp_path):
    obj = make_room(tmp_path)
    obj.room_dir = str(tmp_path / "room")
    obj.roomStatePath = str(tmp_path / "room" / "state")
    monkeypatch.setattr(obj, "writeState", lambda state: (tmp_path / "room").mkdir(exist_ok=True))
    (tmp_path / "room").mkdir()
    (tmp_path / "room" / "state").write_text(json.dumps({"state": "garbage", "phoneStatus": True, "pcStatus": True}))
    assert obj._getLastState()["state"] == "off"

    (tmp_path / "room" / "state").unlink()
    monkeypatch.setattr(obj, "writeState", lambda state: (tmp_path / "room" / "state").write_text(json.dumps({"state": state, "phoneStatus": True, "pcStatus": True})))
    assert obj._getLastState()["state"] == "day"


def test_reset_from_logged_state_all_state_actions(monkeypatch, tmp_path):
    state_to_method = {
        "off": "off", "on": "on", "day": "day", "dusk": "dusk", "night": "night", "sleep": "sleep",
        "color": None, "autoset": "autoset", "custom:3000:50": "customTempFlow",
    }
    for state, method in state_to_method.items():
        obj = make_room(tmp_path, [FakeBulb("1", power="off", bright="1", ct="1")])
        obj.room_dir = str(tmp_path / f"{state.replace(':', '_')}")
        obj.roomStatePath = str(tmp_path / f"{state.replace(':', '_')}" / "state")
        (tmp_path / state.replace(':', '_')).mkdir(exist_ok=True)
        (tmp_path / state.replace(':', '_') / "state").write_text(json.dumps({"state": state, "phoneStatus": False, "pcStatus": True}))
        called = []
        for name in ["off", "on", "day", "dusk", "night", "sleep", "autoset", "customTempFlow"]:
            monkeypatch.setattr(obj, name, lambda *a, _name=name, **k: called.append((_name, a, k)))
        monkeypatch.setattr(obj, "applyFuncAndRebuild", lambda f, selectBulbIps=[]: [{"power": "off", "ct": "1", "bright": "1"}])
        obj.resetFromLoggedState(include_IP_states=False)
        if method and state != "off":
            assert called


def test_apply_func_and_rebuild_success_selective_and_retries(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("a"), FakeBulb("b")])
    monkeypatch.setattr(room, "Bulb", lambda ip, roomName="": FakeBulb(ip))
    assert obj.applyFuncAndRebuild(lambda b: b._ip.upper()) == ["A", "B"]
    assert obj.applyFuncAndRebuild(lambda b: b._ip, selectBulbIps=["b"]) == ["b"]

    attempts = {"a": 0}
    def flaky(b):
        if b._ip == "a" and attempts["a"] == 0:
            attempts["a"] += 1
            raise RuntimeError("fail")
        return b._ip
    result = obj.applyFuncAndRebuild(flaky)
    assert "a" in result and "b" in result

    always = FakeBulb("z")
    always.fail_times = 99
    obj.bulbs = [always]
    with pytest.raises(RuntimeError, match="Failed to rebuild"):
        obj.applyFuncAndRebuild(lambda b: (_ for _ in ()).throw(RuntimeError("x")))


def test_simple_light_commands(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("1", power="off")])
    monkeypatch.setattr(obj, "autoset", lambda **kwargs: 0)
    monkeypatch.setattr(obj, "writeState", lambda state: setattr(obj, "state", state))
    monkeypatch.setattr(obj, "colorTempFlow", lambda *args: None)

    obj.brightness(25)
    assert obj.bulbs[0].properties["bright"] == "25"
    obj.day(); obj.dusk(); obj.night(); obj.sleep(); obj.customTempFlow(3100, brightness=45)
    assert obj.state == "on"


def test_onoff_off_on_toggle_and_rgb(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("1", power="off")])
    monkeypatch.setattr(obj, "writeState", lambda state: setattr(obj, "state", state))
    monkeypatch.setattr(obj, "autoset", lambda **kwargs: 0)
    monkeypatch.setattr(room, "readManualOverride", lambda *a, **k: dt.datetime.utcnow() - dt.timedelta(hours=2))

    obj.off()
    assert obj.bulbs[0].properties["power"] == "off"
    obj.on()
    assert obj.bulbs[0].properties["power"] == "on"
    obj.bulbs[0].properties["power"] = "off"
    obj.toggle()
    assert obj.bulbs[0].properties["power"] == "on"
    obj.toggle()
    assert obj.bulbs[0].properties["power"] == "off"
    obj.rgb("1", "2", "3")
    assert any(call[0] == "flow" for call in obj.bulbs[0].calls)


def test_threaded_color_temp_flow_uses_rgb_for_gu10(monkeypatch, tmp_path):
    gu = FakeBulb("10.0.0.36")
    normal = FakeBulb("normal")
    obj = make_room(tmp_path, [gu, normal])
    monkeypatch.setattr(room, "ct_to_rgb", lambda _: [1, 2, 3])
    obj.threadedColorTempFlow(3200, 3000, 80)
    assert len(gu.calls) == 1
    assert len(normal.calls) == 1


def test_threaded_flow_logs_failure_and_color_temp_flow_delegates(monkeypatch, tmp_path):
    bad = FakeBulb("bad")
    failures = {"n": 0}
    def start(flow):
        failures["n"] += 1
        raise RuntimeError("bad")
    bad.start_flow = start
    obj = make_room(tmp_path, [bad])
    with pytest.raises(RuntimeError):
        obj._threadedColorTempFlow(bad, types.SimpleNamespace())
    monkeypatch.setattr(obj, "threadedColorTempFlow", lambda *args: failures.setdefault("delegated", args))
    obj.colorTempFlow(3000, 1, 2)
    assert "delegated" in failures


def test_autoset_off_when_all_bulbs_off(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("1", power="off")])
    monkeypatch.setattr(room, "readManualOverride", lambda *a, **k: dt.datetime.utcnow() - dt.timedelta(days=2))
    assert obj.autoset(force=False) == -1


def test_autoset_day_and_night_and_dnd(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("1", power="on", bright="1", ct="1")])
    now = dt.datetime(2026, 9, 20, 12, 0, 0)
    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None): return cls.fromtimestamp(now.timestamp(), tz=tz) if tz else cls(*now.timetuple()[:6])
        @classmethod
        def utcnow(cls): return cls(*now.timetuple()[:6])
    monkeypatch.setattr(room, "datetime", types.SimpleNamespace(datetime=FrozenDateTime, time=dt.time, date=dt.date, timedelta=dt.timedelta))
    monkeypatch.setattr(room.time, "localtime", lambda: types.SimpleNamespace(tm_wday=6))
    monkeypatch.setattr(room, "SUNRISE_TIME", "06:50:AM")
    monkeypatch.setattr(room, "WEEKEND_SUNRISE_TIME", "08:00:AM")
    monkeypatch.setattr(room, "SUNSET_TIME", "05:30:PM")
    monkeypatch.setattr(__import__("yeelightLib"), "SUNSET_TIME", "05:30:PM")
    monkeypatch.setattr(room, "SLEEP_TIME", "10:30:PM")
    monkeypatch.setattr(room, "getNightRange", lambda: [])
    monkeypatch.setattr(room, "getCalcTimes", lambda: {"sunsetTime": "05:30:PM"})
    monkeypatch.setattr(room, "AUTOSET_TIMER_CHECK_BEFORE_EXEC", True)
    monkeypatch.setattr(room, "readManualOverride", lambda *a, **k: {"time": now - dt.timedelta(days=2), "action": ""} if k.get("returnDict") else now - dt.timedelta(days=2))
    calls = []
    obj.day = lambda *a, **k: calls.append(("day", a, k))
    obj.off = lambda *a, **k: calls.append(("off", a, k))
    obj.sleep = lambda *a, **k: calls.append(("sleep", a, k))
    obj.customTempFlow = lambda *a, **k: calls.append(("custom", a, k))
    obj.writeState = lambda *a, **k: calls.append(("write", a, k))
    obj.applyFuncAndRebuild = lambda f, selectBulbIps=[]: [(obj.bulbs[0], {"power": "off", "ct": "1", "bright": "1"})]
    assert obj.autoset(force=True) == 0
    assert calls[0][0] == "day"

    night_now = dt.datetime(2026, 9, 20, 18, 0)
    class FrozenNightDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None): return cls.fromtimestamp(night_now.timestamp(), tz=tz) if tz else cls(*night_now.timetuple()[:6])
        @classmethod
        def utcnow(cls): return cls(*night_now.timetuple()[:6])
    monkeypatch.setattr(room, "datetime", types.SimpleNamespace(datetime=FrozenNightDateTime, time=dt.time, date=dt.date, timedelta=dt.timedelta))
    monkeypatch.setattr(room, "getNightRange", lambda: [(dt.time(17, 30), dt.time(19, 0), 3000, 60)])
    calls.clear()
    assert obj.autoset(force=True) == 0
    assert any(x[0] == "custom" for x in calls)

    dnd_now = dt.datetime(2026, 9, 20, 23, 0)
    class FrozenDndDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None): return cls.fromtimestamp(dnd_now.timestamp(), tz=tz) if tz else cls(*dnd_now.timetuple()[:6])
        @classmethod
        def utcnow(cls): return cls(*dnd_now.timetuple()[:6])
    monkeypatch.setattr(room, "datetime", types.SimpleNamespace(datetime=FrozenDndDateTime, time=dt.time, date=dt.date, timedelta=dt.timedelta))
    monkeypatch.setattr(room, "getNightRange", lambda: [])
    calls.clear()
    assert obj.autoset(force=True, forceLight=True) == 0
    assert calls[-1][0] == "sleep"
    calls.clear()
    assert obj.autoset(force=True, forceLight=False) == 0
    assert calls[-1][0] == "off"


def test_autoset_manual_override_cancellation_and_phone_requirement(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("1", power="on")])
    now = dt.datetime(2026, 9, 20, 12, 0)
    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None): return cls.fromtimestamp(now.timestamp(), tz=tz) if tz else cls(*now.timetuple()[:6])
        @classmethod
        def utcnow(cls): return cls(*now.timetuple()[:6])
    dtmod = types.SimpleNamespace(datetime=FrozenDateTime, time=dt.time, date=dt.date, timedelta=dt.timedelta)
    monkeypatch.setattr(room, "datetime", dtmod)
    monkeypatch.setattr(lib_for_room := __import__("yeelightLib"), "SUNSET_TIME", "05:30:PM")
    monkeypatch.setattr(room.time, "localtime", lambda: types.SimpleNamespace(tm_wday=0))
    monkeypatch.setattr(room, "SUNRISE_TIME", "06:50:AM")
    monkeypatch.setattr(room, "SLEEP_TIME", "10:30:PM")
    monkeypatch.setattr(room, "getNightRange", lambda: [])
    monkeypatch.setattr(room, "getCalcTimes", lambda: {"sunsetTime": "05:30:PM"})
    monkeypatch.setattr(room, "AUTOSET_TIMER_CHECK_BEFORE_EXEC", False)
    monkeypatch.setattr(room, "AUTOSET_PHONE_REQUIRED", False)
    monkeypatch.setattr(room, "readManualOverride", lambda *a, **k: {"time": now, "action": "off"} if k.get("returnDict") else now)
    assert obj.autoset(force=False, autoset_auto_var=False) == -1

    obj.envState.phoneStatus = False
    monkeypatch.setattr(room, "AUTOSET_PHONE_REQUIRED", True)
    monkeypatch.setattr(room, "readManualOverride", lambda *a, **k: now - dt.timedelta(days=2))
    obj.off = lambda auto=False: setattr(obj, "state", "off")
    auto_now = dt.datetime(2026, 9, 20, 23, 0)
    class FrozenAutoDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None): return cls.fromtimestamp(auto_now.timestamp(), tz=tz) if tz else cls(*auto_now.timetuple()[:6])
        @classmethod
        def utcnow(cls): return cls(*auto_now.timetuple()[:6])
    monkeypatch.setattr(room, "datetime", types.SimpleNamespace(datetime=FrozenAutoDateTime, time=dt.time, date=dt.date, timedelta=dt.timedelta))
    assert obj.autoset(force=False, autoset_auto_var=True) == -1


def test_sunrise_and_return_from_away(monkeypatch, tmp_path):
    obj = make_room(tmp_path, [FakeBulb("1"), FakeBulb("2")])
    monkeypatch.setattr(room, "writeManualOverride", lambda **kwargs: None)
    monkeypatch.setattr(obj, "writeState", lambda state: setattr(obj, "state", state))
    monkeypatch.setattr(obj, "on", lambda **kwargs: [b.turn_on() for b in obj.bulbs])
    obj.sunrise()
    assert all(any(c == ("rgb", (255, 0, 0)) for c in b.calls) for b in obj.bulbs)
    assert any(call[0] == "flow" for call in obj.bulbs[0].calls)

    obj._getLastState = lambda: {"state": "off", "phoneStatus": False}
    obj.autoset = lambda **kwargs: setattr(obj, "state", "autoset")
    obj.returnFromAway()
    assert obj.state == "autoset"

    obj._getLastState = lambda: {"state": "on", "phoneStatus": False}
    before = obj.state
    obj.returnFromAway()
    assert obj.state == before
