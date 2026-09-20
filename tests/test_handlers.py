import json
import types
import threading
from time import sleep as real_sleep

import pytest

from handlers import bulb_events, checkPing, http_events, switches


class LoggerStub:
    def __init__(self): self.calls=[]
    def __getattr__(self, name):
        def f(*args, **kwargs): self.calls.append((name,args,kwargs))
        return f


class Event:
    def __init__(self): self.value=False
    def set(self): self.value=True
    def clear(self): self.value=False
    def is_set(self): return self.value


class Cond:
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def notify(self): pass


def test_check_ping_paths(monkeypatch):
    monkeypatch.setattr(checkPing, "logger", LoggerStub())
    monkeypatch.setattr(checkPing.time, "sleep", lambda _: None)
    monkeypatch.setattr(checkPing, "phoneIP", "phone")
    monkeypatch.setattr(checkPing, "pcIP", "pc")

    # phone disappears after the retry threshold
    monkeypatch.setattr(checkPing.os, "system", lambda _: 1)
    changed, pc, phone = checkPing.checkPing(True, True)
    assert changed is False and pc is False and phone is False

    # phone reappears
    calls = iter([0, 0])
    monkeypatch.setattr(checkPing.os, "system", lambda _: next(calls))
    changed, pc, phone = checkPing.checkPing(True, False)
    assert changed is True and pc is True and phone is True

    # pc turns on
    calls = iter([0, 0])
    monkeypatch.setattr(checkPing.os, "system", lambda _: next(calls))
    changed, pc, phone = checkPing.checkPing(False, True)
    assert changed is True and pc is True and phone is True

    # pc turns off after threshold
    calls = iter([0, 1] * 3)
    monkeypatch.setattr(checkPing.os, "system", lambda _: next(calls))
    changed, pc, phone = checkPing.checkPing(True, True)
    assert changed is False and pc is False and phone is True


def test_check_ping_threaded_success(monkeypatch):
    event = Event(); cond = Cond()
    started = threading.Event(); blocked = threading.Event()
    class Pipe:
        def __init__(self): self.sent=[]
        def send(self, x):
            self.sent.append(x)
            started.set()
            blocked.wait(2)
    monkeypatch.setattr(checkPing, "setprocname", lambda x: None)
    monkeypatch.setattr(checkPing, "checkPing", lambda pc, phone: (True, False, True))
    pipe = Pipe()
    thread = threading.Thread(target=checkPing.checkPingThreaded, args=(event, pipe, cond, True, False), daemon=True)
    thread.start()
    assert started.wait(1)
    assert pipe.sent == [[True, False, True]]



def _run_monitor_until_block(monkeypatch, target, setup, event):
    blocked = threading.Event()
    setup(blocked)
    thread = threading.Thread(target=target, args=(event, Cond()), daemon=True)
    thread.start()
    real_sleep(0.05)
    blocked.set()
    thread.join(1)
    assert event.value


def test_monitor_advert_bulbs(monkeypatch):
    event=Event()
    class Sock:
        def __init__(self, blocked): self.i=0; self.blocked=blocked
        def setsockopt(self,*a): pass
        def bind(self,*a): pass
        def recv(self,*a):
            self.i += 1
            if self.i == 1: return b"NOT DISCOVER"
            self.blocked.wait()
            return b"ssdp:discover"
    monkeypatch.setattr(bulb_events, "setprocname", lambda x: None)
    monkeypatch.setattr(bulb_events.socket, "socket", lambda *a: Sock(blocked_holder[0]))
    monkeypatch.setattr(bulb_events.struct, "pack", lambda *a: b"")
    monkeypatch.setattr(bulb_events, "logger", LoggerStub())
    blocked_holder=[threading.Event()]
    def runner(): return bulb_events.monitor_advert_bulbs(event, Cond())
    thread=threading.Thread(target=runner, daemon=True); thread.start()
    real_sleep(0.05)
    assert event.value


def test_monitor_bulb_static_detects_change(monkeypatch):
    event=Event(); blocked=threading.Event()
    seq = [[{"ip":"a"}], [{"ip":"b"}], [{"ip":"b"}], [{"ip":"b"}], [{"ip":"b"}]]
    monkeypatch.setattr(bulb_events.yeelight, "discover_bulbs", lambda *args: seq.pop(0) if seq else [{"ip":"b"}])
    monkeypatch.setattr(bulb_events.time, "sleep", lambda _: blocked.wait())
    monkeypatch.setattr(bulb_events, "setprocname", lambda x: None)
    monkeypatch.setattr(bulb_events, "logger", LoggerStub())
    thread=threading.Thread(target=bulb_events.monitor_bulb_static, args=(event, Cond()), daemon=True); thread.start()
    real_sleep(0.05)
    assert event.value


def test_monitor_ping_and_bulb_ping(monkeypatch):
    event=Event(); blocked=threading.Event()
    procs=[]
    class P:
        def __init__(self, rc=None): self.rc=rc
        def poll(self): return self.rc
        def communicate(self): pass
    monkeypatch.setattr(bulb_events, "setprocname", lambda x: None)
    monkeypatch.setattr(bulb_events, "BULB_IPS", ["a", "b"])
    monkeypatch.setattr(bulb_events.subprocess, "Popen", lambda *a, **k: P(0))
    monkeypatch.setattr(bulb_events.time, "sleep", lambda _: blocked.wait())
    thread=threading.Thread(target=bulb_events.monitor_ping, args=(event, Cond()), daemon=True); thread.start()
    real_sleep(0.05)
    assert event.value


def test_monitor_bulb_ping_detects_missing(monkeypatch):
    event=Event(); blocked=threading.Event()
    monkeypatch.setattr(bulb_events, "setprocname", lambda x: None)
    monkeypatch.setattr(bulb_events, "BULB_IPS", ["a", "b"])
    # initial scan: both online; next loop finds only a and all three checks for b fail
    results=iter([0, 1, 1, 1, 1])
    monkeypatch.setattr(bulb_events.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=next(results)))
    monkeypatch.setattr(bulb_events.time, "sleep", lambda _: blocked.wait())
    thread=threading.Thread(target=bulb_events.monitor_bulb_ping, args=(event, Cond()), daemon=True); thread.start()
    real_sleep(0.05)
    assert event.value


def make_handler(monkeypatch, module, path="/property/LivingRoom/power", body=None):
    sent=[]
    class Handler:
        def __init__(self):
            self.path=path
            self.wfile=types.SimpleNamespace(write=lambda b: sent.append(b))
            self.headers={"Content-Length": str(len(body or b""))}
            self.rfile=types.SimpleNamespace(read=lambda n: body or b"")
        def send_response(self, code): sent.append(("status", code))
        def send_header(self, k, v): sent.append((k,v))
        def end_headers(self): sent.append(("end",))
    return Handler(), sent


def attach_http_methods(handler, cls):
    for name in ["respond", "handle_http", "do_HEAD", "do_GET", "do_POST", "_set_headers"]:
        if hasattr(cls, name):
            setattr(handler, name, getattr(cls, name).__get__(handler, type(handler)))


def test_http_handler_get_head_post(monkeypatch):
    event=Event(); cond=Cond(); pipe=types.SimpleNamespace(sent=[], send=lambda x: pipe.sent.append(x), poll=lambda timeout: True, recv=lambda: {"power":"on"})
    cls = http_events.YeelightHTTP(event, cond, pipe)
    monkeypatch.setattr(http_events, "logger", LoggerStub())
    monkeypatch.setattr(http_events, "setprocname", lambda x: None)
    h, sent = make_handler(monkeypatch, http_events)
    class BadBytes:
        def __bytes__(self): raise TypeError("no bytes")
    attach_http_methods(h, cls)
    h.respond(200, BadBytes())
    assert b"ERROR converting content" in sent[-1]

    h, sent = make_handler(monkeypatch, http_events, body=json.dumps({"eventType":"dashboard","newState":"day","room":"LivingRoom","foo":1}).encode())
    attach_http_methods(h, cls)
    h.do_POST()
    assert any(x[0]=="status" and x[1]==200 for x in sent if isinstance(x, tuple))
    assert pipe.sent[-1]["eventType"] == "dashboard-action"

    h, sent = make_handler(monkeypatch, http_events, body=json.dumps({"eventType":"invalid","newState":"day"}).encode())
    attach_http_methods(h, cls)
    h.do_POST()
    assert any(x[0]=="status" and x[1]==500 for x in sent if isinstance(x, tuple))

    h, sent = make_handler(monkeypatch, http_events, path="/bad")
    attach_http_methods(h, cls)
    h.do_GET()
    assert any(x[0]=="status" and x[1]==500 for x in sent if isinstance(x, tuple))

    h, sent = make_handler(monkeypatch, http_events, path="/property/LivingRoom/power")
    attach_http_methods(h, cls)
    h.do_GET()
    assert any(x[0]=="status" and x[1]==200 for x in sent if isinstance(x, tuple))
    assert pipe.sent[-1]["query"] == "getProperty"

    h, sent = make_handler(monkeypatch, http_events)
    attach_http_methods(h, cls)
    h.do_HEAD()
    assert sent[0] == ("status", 200)


def test_http_handler_get_timeout_and_response_variants(monkeypatch):
    event=Event(); cond=Cond(); pipe=types.SimpleNamespace(send=lambda x: None, poll=lambda timeout: False)
    cls=http_events.YeelightHTTP(event, cond, pipe)
    monkeypatch.setattr(http_events, "logger", LoggerStub())
    h, sent=make_handler(monkeypatch, http_events, path="/property/LivingRoom")
    attach_http_methods(h, cls)
    h.do_GET()
    assert any(x[0]=="status" and x[1]==500 for x in sent if isinstance(x, tuple))

    h, sent=make_handler(monkeypatch, http_events)
    attach_http_methods(h, cls)
    h.respond(204, b"raw", "app/x")
    assert b"raw" in sent
    h.handle_http(200, "/", "text/plain")


def test_switch_monitor_processes_valid_and_duplicate(monkeypatch):
    event=Event(); cond=Cond()
    class Pipe:
        def __init__(self): self.sent=[]
        def send(self,x): self.sent.append(x)
        def poll(self,*a): return True
        def recv(self): return 0
    pipe=Pipe()
    lines = ["" for _ in range(10)] + [json.dumps({"model":"switch","len":25,"data":"aeb82f8","time":"2099-09-20 10:00:00"}), json.dumps({"model":"switch","len":25,"data":"aeb82f8","time":"2099-09-20 10:00:01"})]
    class Stdout:
        def readline(self):
            if lines: return lines.pop(0)
            raise KeyboardInterrupt()
    class Proc:
        def __init__(self): self.stdout=Stdout(); self.killed=False
        def poll(self): return 0
        def kill(self): self.killed=True
        def communicate(self, timeout=None): return (b"", b"")
    proc=Proc()
    monkeypatch.setattr(switches, "logger", LoggerStub())
    monkeypatch.setattr(switches, "setprocname", lambda x: None)
    monkeypatch.setattr(switches.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(switches.time, "sleep", lambda _: None)
    monkeypatch.setattr(switches.atexit, "register", lambda f: None)
    with pytest.raises(KeyboardInterrupt):
        switches.monitor_switches(event, cond, pipe)
    assert ["Bedroom", "day"] in pipe.sent


def test_switch_monitor_ignores_non_switch_payload(monkeypatch):
    event=Event(); cond=Cond()
    class Pipe:
        def send(self, x): pass
        def poll(self, *a): return True
        def recv(self): return 0
    lines = [json.dumps({"model":"other","len":25,"data":"aeb82f8","time":"2099-09-20 10:00:00"})]
    class Stdout:
        def readline(self):
            if lines: return lines.pop(0)
            raise KeyboardInterrupt()
    class Proc:
        stdout=Stdout()
        def poll(self): return 0
        def kill(self): pass
        def communicate(self, timeout=None): return (b"", b"")
    monkeypatch.setattr(switches, "logger", LoggerStub())
    monkeypatch.setattr(switches, "setprocname", lambda x: None)
    monkeypatch.setattr(switches.subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(switches.atexit, "register", lambda f: None)
    with pytest.raises(KeyboardInterrupt):
        switches.monitor_switches(event, cond, Pipe())
