import datetime as dt
import importlib
import json
import sys
import types

import pytest

import yeelightLib as lib
import room as room_mod


class LoggerStub:
    def __init__(self):
        self.calls = []
    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return method


class Event:
    def __init__(self, set_=False):
        self.value = set_
    def is_set(self): return self.value
    def set(self): self.value = True
    def clear(self): self.value = False


class Pipe:
    def __init__(self, recv_value=None):
        self.recv_value = recv_value
        self.sent = []
        self.closed = False
    def recv(self): return self.recv_value
    def send(self, value): self.sent.append(value)
    def poll(self, *args): return False
    def close(self): self.closed = True


class Proc:
    def __init__(self): self.started = self.killed = self.terminated = False
    def start(self): self.started = True
    def kill(self): self.killed = True
    def terminate(self): self.terminated = True


class FakeRoom:
    def __init__(self, name, bulbs, env):
        self.name = name
        self.bulbs = bulbs
        self.envState = env
        self.calls = []
        self.influx_client = types.SimpleNamespace(close=lambda: self.calls.append(("close",)))
    def resetFromLoggedState(self, *args, **kwargs): self.calls.append(("reset", args, kwargs))
    def rebuild_bulbs(self): self.calls.append(("rebuild",))
    def graceful_kill(self): self.calls.append(("kill",))
    def __getattr__(self, name):
        def action(*args, **kwargs): self.calls.append((name, args, kwargs))
        return action


def import_app(monkeypatch):
    monkeypatch.setattr(lib.os, "system", lambda _: 1)
    monkeypatch.setattr(lib, "getLogger", lambda quiet=False: LoggerStub())
    monkeypatch.setattr(lib, "getBulbLogger", lambda: LoggerStub())
    monkeypatch.setattr(lib, "set_IRL_sunset", lambda: None)
    monkeypatch.setattr(room_mod, "Room", FakeRoom)
    monkeypatch.setattr(room_mod, "getLogger", lambda quiet=False: LoggerStub())
    monkeypatch.setattr(room_mod, "getBulbLogger", lambda: LoggerStub())
    monkeypatch.setattr(room_mod, "getNightRange", lambda: [])
    if "yeelightpython" in sys.modules:
        del sys.modules["yeelightpython"]
    return importlib.import_module("yeelightpython")


def test_main_dispatches_commands_and_invalid(monkeypatch):
    app = import_app(monkeypatch)
    actions = []
    monkeypatch.setattr(app, "run_server", lambda: actions.append("run_server"))
    monkeypatch.setattr(app, "sunrise", lambda: actions.append("sunrise"))
    monkeypatch.setattr(app, "sunrise_http", lambda: actions.append("sunrise_http"))
    monkeypatch.setattr(app, "global_action", lambda cmd: actions.append(cmd))

    monkeypatch.setattr(app.sys, "argv", ["prog"])
    app.main()
    assert actions == []

    monkeypatch.setattr(app.sys, "argv", ["prog", "day"])
    app.main()
    assert "day" in actions

    monkeypatch.setattr(app.sys, "argv", ["prog", "sunrise"])
    app.main()
    monkeypatch.setattr(app.sys, "argv", ["prog", "sunrise_http"])
    app.main()
    monkeypatch.setattr(app.sys, "argv", ["prog", "run_server"])
    app.main()
    assert actions[-3:] == ["sunrise", "sunrise_http", "run_server"]

    monkeypatch.setattr(app.sys, "argv", ["prog", "nonsense"])
    app.main()


def test_rebuild_bulbs_both_modes(monkeypatch):
    app = import_app(monkeypatch)
    app.YEELIGHT_ROOM_HANDLES_REBUILD = True
    app.rebuild_bulbs()
    assert all(any(c[0] == "rebuild" for c in room.calls) for room in app.ROOMS.values())

    app.YEELIGHT_ROOM_HANDLES_REBUILD = False
    app.bulbs = [types.SimpleNamespace(_ip="old")]
    monkeypatch.setattr(app.yeelight, "discover_bulbs", lambda *_: [{"ip": "new"}])
    app.rebuild_bulbs()
    assert [b._ip for b in app.bulbs] == ["new"]


def test_websocket_test_sends_json_and_fallback(monkeypatch):
    app = import_app(monkeypatch)
    sent = []
    class WS:
        def send(self, value): sent.append(value)
    class PipeSeq:
        def __init__(self): self.i = 0
        def recv(self):
            self.i += 1
            if self.i == 1: return {"a": 1}
            raise RuntimeError("stop websocket test")
    ws = WS()
    class Context:
        def __enter__(self):
            class S:
                def serve_forever(self):
                    handler = args_holder[0]
                    handler(ws)
            return S()
        def __exit__(self, *x): return False
    pipe = PipeSeq()
    args_holder = []
    def serve(*args, **kwargs):
        args_holder[:] = list(args)
        return Context()
    ws_pkg = types.ModuleType("websockets"); ws_pkg.__path__ = []
    sync_pkg = types.ModuleType("websockets.sync"); sync_pkg.__path__ = []
    server_mod = types.ModuleType("websockets.sync.server"); server_mod.serve = serve
    ws_pkg.sync = sync_pkg; sync_pkg.server = server_mod
    monkeypatch.setitem(sys.modules, "websockets", ws_pkg)
    monkeypatch.setitem(sys.modules, "websockets.sync", sync_pkg)
    monkeypatch.setitem(sys.modules, "websockets.sync.server", server_mod)
    app.websocketTest(pipe)
    assert sent == [json.dumps({"a": 1})]


def make_server_shell(app):
    s = app.Server.__new__(app.Server)
    s.envState = types.SimpleNamespace(phoneStatus=True, pcStatus=True)
    s.wake_condition = None
    s.ping_event = Event()
    s.bulb_event = Event()
    s.switch_event = Event()
    s.http_event = Event()
    s.ping_pipe = Pipe()
    s.switch_pipe = Pipe()
    s.http_pipe = Pipe()
    s.websocket_pipe = Pipe()
    s.timer_wake = False
    s.switch_room = None
    s.switch_action = None
    s.http_res = None
    s.ping_res = None
    s.monitor_bulb_static_proc = Proc()
    s.monitor_bulb_advert_proc = Proc()
    s.monitor_bulb_ping_proc = Proc()
    s.check_ping_proc = Proc()
    s.monitor_switches_proc = Proc()
    s.http_proc = Proc()
    s.websocket_proc = Proc()
    s.TIMEOUT_INTERVAL = 300
    return s


def test_server_wake_predicate_and_resolve(monkeypatch):
    app = import_app(monkeypatch)
    s = make_server_shell(app)
    s.ping_event.set(); s.ping_pipe.recv_value = (False, True, True)
    assert s.wake_predicate() is True
    s.resolve_wake()
    assert s.envState.phoneStatus is False
    assert s.envState.pcStatus is True
    s.ping_event.clear()

    s.bulb_event.set()
    monkeypatch.setattr(app, "rebuild_bulbs", lambda: setattr(s, "rebuilt", True))
    s.resolve_wake()
    assert s.rebuilt is True

    s.switch_event.set()
    s.switch_pipe.recv_value = ("LivingRoom", "day")
    monkeypatch.setattr(app.os, "system", lambda _: 0)
    s.resolve_wake()
    assert s.switch_room == "LivingRoom"
    assert s.switch_pipe.sent[-1] == 0

    s.switch_event.set(); s.switch_pipe.recv_value = ("", None)
    s.resolve_wake()
    assert s.switch_room is None

    s.http_event.set(); s.http_pipe.recv_value = {"room": "global", "action": "day", "eventType": "dashboard-action"}
    s.resolve_wake()
    assert s.http_res["action"] == "day"


def test_server_init(monkeypatch):
    app = import_app(monkeypatch)
    monkeypatch.setattr(app, "USE_MONITOR_BULB_STATIC", False)
    monkeypatch.setattr(app, "USE_MONITOR_ADVERT_BULBS", True)
    monkeypatch.setattr(app, "USE_MONITOR_BULB_PING", True)
    monkeypatch.setattr(app, "set_IRL_sunset", lambda: None)
    monkeypatch.setattr(app.mp, "Condition", lambda: object())
    monkeypatch.setattr(app.mp, "Event", lambda: Event())
    monkeypatch.setattr(app.mp, "Pipe", lambda: (Pipe(), Pipe()))
    monkeypatch.setattr(app.mp, "Process", lambda target, args, **kwargs: Proc())
    s = app.Server()
    assert s.TIMEOUT_INTERVAL == 300
    assert s.monitor_bulb_advert_proc.started is False
    assert s.websocket_proc.started is False


def test_graceful_shutdown_exits(monkeypatch):
    app = import_app(monkeypatch)
    s = make_server_shell(app)
    monkeypatch.setattr(app, "USE_MONITOR_BULB_STATIC", True)
    monkeypatch.setattr(app, "USE_MONITOR_ADVERT_BULBS", True)
    monkeypatch.setattr(app, "USE_MONITOR_BULB_PING", True)
    for room in app.ROOMS.values():
        room.influx_client.close = lambda: room.calls.append(("closed",))
    monkeypatch.setattr(app.sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        s.graceful_shutdown()
    assert s.ping_pipe.closed and s.websocket_pipe.closed


def run_once(app, monkeypatch, setup):
    s = make_server_shell(app)
    class Condition:
        def __init__(self): self.calls = 0
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def wait_for(self, predicate, timeout):
            self.calls += 1
            if self.calls == 1:
                setup(s)
                return True
            raise KeyboardInterrupt()
    s.wake_condition = Condition()
    for attr in ["check_ping_proc", "monitor_switches_proc", "http_proc", "websocket_proc"]:
        setattr(s, attr, Proc())
    monkeypatch.setattr(app, "global_action", lambda *a, **k: None)
    monkeypatch.setattr(app, "writeManualOverride", lambda *a, **k: None)
    monkeypatch.setattr(app, "rebuild_bulbs", lambda: None)
    monkeypatch.setattr(app, "set_IRL_sunset", lambda: None)
    monkeypatch.setattr(app, "SERVER_ACTS_NOT_CLIENT", True)
    monkeypatch.setattr(app, "USE_MONITOR_ADVERT_BULBS", False)
    monkeypatch.setattr(app, "USE_MONITOR_BULB_STATIC", False)
    monkeypatch.setattr(app, "USE_MONITOR_BULB_PING", False)
    with pytest.raises(KeyboardInterrupt):
        app.Server.run(s)


def test_server_run_ping_switch_http_and_timer_branches(monkeypatch):
    app = import_app(monkeypatch)
    run_once(app, monkeypatch, lambda s: setattr(s, "ping_res", True))
    run_once(app, monkeypatch, lambda s: setattr(s, "ping_res", False))
    run_once(app, monkeypatch, lambda s: (setattr(s, "switch_room", "LivingRoom"), setattr(s, "switch_action", "day")))
    run_once(app, monkeypatch, lambda s: setattr(s, "http_res", {"eventType": "dashboard-action", "room": "LivingRoom", "action": "day", "kwargs": {}}))
    run_once(app, monkeypatch, lambda s: setattr(s, "http_res", {"eventType": "dashboard-query", "room": "LivingRoom", "query": "getProperty", "properties": "power"}))
    run_once(app, monkeypatch, lambda s: None)


def test_run_server_wrappers_and_global_action(monkeypatch):
    app = import_app(monkeypatch)
    class S:
        def run(self): raise RuntimeError("run")
    monkeypatch.setattr(app, "Server", S)
    with pytest.raises(RuntimeError):
        app.run_server()

    class GoodS:
        def run(self): return "done"
    monkeypatch.setattr(app, "Server", GoodS)
    assert app.run_server() is None

    app.ROOMS = {"A": types.SimpleNamespace(name="A", day=lambda *a, **k: None), "B": types.SimpleNamespace(name="B", day=lambda *a, **k: None)}
    app.global_action("day")
    app.global_action("not-a-command")

    attempts = {"n": 0}
    def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 2: raise RuntimeError("bad")
    app.ROOMS = {"A": types.SimpleNamespace(name="A", day=flaky)}
    app.global_action("day")
    assert attempts["n"] == 2


def test_sunrise_and_sunrise_http(monkeypatch):
    app = import_app(monkeypatch)
    monkeypatch.setattr(app, "writeManualOverride", lambda *a, **k: None)
    monkeypatch.setattr(app, "global_action", lambda *a, **k: None)
    bulbs = [types.SimpleNamespace(set_brightness=lambda x: None, set_rgb=lambda *x: None, start_flow=lambda x: None)]
    app.ROOMS = {"A": bulbs}
    app.sunrise()
    called = {}
    monkeypatch.setattr("requests.post", lambda *a, **k: called.update(args=a, kwargs=k))
    app.sunrise_http()
    assert called["kwargs"]["json"]["newState"] == "sunrise"
