import datetime as dt
import io
import json
import logging
import os
import pickle
import types

import pytest

import yeelightLib as lib


class LoggerStub:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return method


def test_ct_to_rgb_low_mid_high_boundaries():
    assert lib.ct_to_rgb(1900) == [255, 131, 0]
    assert lib.ct_to_rgb(6600)[0] == 255
    value = lib.ct_to_rgb(8000)
    assert all(0 <= x <= 255 for x in value)
    with pytest.raises(ValueError):
        lib.ct_to_rgb(0)


def test_get_logger_and_bulb_logger_are_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, "HOMEDIR", str(tmp_path))
    lib.actualLoggers.clear()
    first = lib.getLogger(quiet=True)
    second = lib.getLogger(quiet=True)
    assert first is second
    assert first.name == "log"

    lib.actualLoggers.clear()
    first = lib.getBulbLogger()
    second = lib.getBulbLogger()
    assert first is second
    assert first.name == "bulbLog"

    # Avoid leaking handlers between tests/processes.
    for logger in (first, second):
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_calc_and_night_ranges(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, "HOMEDIR", str(tmp_path))
    calc = {"sunsetTime": "06:00:PM", "other": 1}
    ranges = [[dt.time(18, 0), dt.time(22, 0), 3300, 80]]
    (tmp_path / "calcTimes.pickle").write_bytes(pickle.dumps(calc))
    (tmp_path / "nightTimeRange.pickle").write_bytes(pickle.dumps(ranges))
    lib.SUNSET_TIME_DYNAMIC_SET = False
    assert lib.getCalcTimes() == calc
    assert lib.SUNSET_TIME == "06:00:PM"
    assert lib.SUNSET_TIME_DYNAMIC_SET is True
    assert lib.getNightRange() == ranges


def test_manual_override_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, "ROOM_DIR", str(tmp_path / "{room}"))
    monkeypatch.setattr(lib, "MANUAL_OVERRIDE_PATH", os.path.join(str(tmp_path), "{room}", "manualOverride.json"))
    monkeypatch.setattr(lib, "room_to_ips", {"Room1": ["1.2.3.4"], "Room2": ["5.6.7.8"]})
    monkeypatch.setattr(lib, "getLogger", lambda quiet=False: LoggerStub())
    (tmp_path / "Room1").mkdir()
    (tmp_path / "Room2").mkdir()

    lib.writeManualOverride("Room1", dt.timedelta(hours=1), "on")
    result = lib.readManualOverride("Room1", returnDict=True)
    assert result["action"] == "on"
    assert isinstance(result["time"], dt.datetime)
    assert lib.readManualOverride("Room1") == result["time"]

    lib.writeManualOverride(offset=dt.timedelta(), action="off")
    assert lib.readManualOverride("Room2", returnDict=True)["action"] == "off"
    with pytest.raises(AssertionError):
        lib.writeManualOverride("Unknown")


def test_read_manual_override_creates_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, "MANUAL_OVERRIDE_PATH", str(tmp_path / "{room}" / "manualOverride.json"))
    monkeypatch.setattr(lib, "room_to_ips", {"Room1": ["1"]})
    monkeypatch.setattr(lib, "getLogger", lambda quiet=False: LoggerStub())
    monkeypatch.setattr(lib, "writeManualOverride", lambda room=None, offset=None, action="": (tmp_path / room).mkdir(exist_ok=True))

    # Replace the helper with a real writer after proving the missing-file fallback path.
    calls = []
    def writer(room=None, offset=None, action=""):
        calls.append(room)
        path = tmp_path / room
        path.mkdir(exist_ok=True)
        payload = {"time": "2026-09-20 12:00:00", "action": action}
        (path / "manualOverride.json").write_text(json.dumps(payload))
    monkeypatch.setattr(lib, "writeManualOverride", writer)
    assert lib.readManualOverride("Room1").year == 2026
    assert calls == ["Room1"]


def test_set_irl_sunset_success_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, "HOMEDIR", str(tmp_path))
    logger = LoggerStub()
    monkeypatch.setattr(lib, "getLogger", lambda quiet=False: logger)
    monkeypatch.setattr(lib, "SUNSET_TIME", "05:30:PM")
    monkeypatch.setattr(lib, "SLEEP_TIME", "10:30:PM")

    class Response:
        status_code = 200
        text = json.dumps({"results": {
            "sunset": "2026-09-20T22:00:00+00:00",
            "civil_twilight_end": "2026-09-20T22:30:00+00:00",
            "irrelevant": 7,
        }})

    monkeypatch.setattr("requests.post", lambda *a, **k: Response())
    monkeypatch.setattr(lib, "DUSK_COLOR", 3300)
    monkeypatch.setattr(lib, "SLEEP_COLOR", 1500)
    lib.set_IRL_sunset()
    assert (tmp_path / "nightTimeRange.pickle").exists()
    assert (tmp_path / "calcTimes.pickle").exists()
    assert lib.SUNSET_TIME

    monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert lib.set_IRL_sunset() is None


def test_bulb_repr_and_cached_properties(monkeypatch):
    bulb = lib.Bulb("10.0.0.1", roomName="Kitchen")
    bulb._last_properties = {"power": "on", "bright": "50", "ct": "4000"}
    assert repr(bulb) == "Bulb(10.0.0.1, room=Kitchen)"
    monkeypatch.setattr(lib, "USE_GET_PROPERTIES", False)
    assert bulb.get_properties(["power", "ct"]) == {"power": "on", "ct": "4000"}
    bulb._last_properties = {}
    bulb.properties = {"power": "off", "bright": "10", "ct": "2500"}
    assert bulb.get_properties(["power"]) == {"power": "off"}


def test_retry_success_after_failure(monkeypatch):
    logger = LoggerStub()
    monkeypatch.setattr(lib, "getLogger", lambda quiet=False: logger)
    monkeypatch.setattr(lib.time, "sleep", lambda _: None)
    calls = []

    @lib.retry(max_attempts=3)
    def operation():
        calls.append(1)
        if len(calls) < 2:
            raise lib.yeelight.BulbException("temporary")
        return 42

    assert operation() == 42
    assert len(calls) == 2


def test_retry_exhausts_generic_exception(monkeypatch):
    monkeypatch.setattr(lib, "getLogger", lambda quiet=False: LoggerStub())
    monkeypatch.setattr(lib.time, "sleep", lambda _: None)

    @lib.retry(max_attempts=2)
    def operation():
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        operation()


def test_apply_func_to_bulbs_success_and_error(monkeypatch):
    monkeypatch.setattr(lib, "getLogger", lambda quiet=False: LoggerStub())
    bulbs = [types.SimpleNamespace(_ip="a"), types.SimpleNamespace(_ip="b")]
    assert lib.applyFuncToBulbs(bulbs, lambda b: b._ip.upper()) == ["A", "B"]
    with pytest.raises(ValueError):
        lib.applyFuncToBulbs(bulbs, lambda b: (_ for _ in ()).throw(ValueError("bad")))


def test_env_state(monkeypatch):
    results = iter([0, 1])
    monkeypatch.setattr(lib.os, "system", lambda _: next(results))
    state = lib.EnvState()
    assert state.phoneStatus is True
    assert state.pcStatus is False
