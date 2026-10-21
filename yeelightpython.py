import atexit
import datetime
from functools import wraps
from itertools import count
import json
import logging
import multiprocessing as mp
import os
import pickle
import platform
import queue
import signal
import sys
import threading
import time

import yeelight
import yeelight.enums
import yeelight.transitions
import yeelight.aio

from http.server import BaseHTTPRequestHandler, HTTPServer

from yeelightLib import *
from room import Room
from handlers.checkPing import checkPingThreaded
from handlers.bulb_events import monitor_advert_bulbs, monitor_bulb_static, monitor_bulb_ping, USE_MONITOR_ADVERT_BULBS, USE_MONITOR_BULB_STATIC, USE_MONITOR_BULB_PING
from handlers.switches import monitor_switches
from handlers.http_events import http_server


os.chdir(HOMEDIR)

logger = getLogger()

bulbLog = getBulbLogger()

bulbs = []

ENV_STATE = EnvState() # Don't call this directly except for init of Server and ROOMS

ROOMS = {roomName : Room(roomName, [Bulb(ip, roomName=roomName) for ip in ips], ENV_STATE) for roomName, ips in room_to_ips.items()}


# Should the server execute the command or should the client?
SERVER_ACTS_NOT_CLIENT = True

# Does the room class handle the rebuild?
YEELIGHT_ROOM_HANDLES_REBUILD = True

def _initialize_cli_bulbs():
    """Initialize the configured bulbs and room objects used by CLI commands."""
    global bulbs
    global ROOMS

    bulbs = []
    configured_ips = {ip for ips in room_to_ips.values() for ip in ips}
    assert all(bulb_ip in configured_ips for bulb_ip in BULB_IPS)

    for roomName, ips in room_to_ips.items():
        blbs = [Bulb(ip, roomName=roomName) for ip in ips]
        bulbs.extend(blbs)
        # Keep the bulbs we just created; constructing an empty Room silently
        # makes every CLI room appear to have no bulbs.
        ROOMS[roomName] = Room(roomName, blbs, ENV_STATE)


def main():
    # logger.info(desk.get_properties())
    global bulbs

    if len(sys.argv) == 1:
        logger.info("No arguments.")
        logger.warning('No arguments.')
        return

    cmd = sys.argv[1].lower()

    if cmd in commands:
        if 'autoset' not in cmd:
            logger.info(cmd)

        _initialize_cli_bulbs()

        if cmd == 'run_server':
            run_server()
        elif cmd == 'sunrise':
            sunrise()
        elif cmd == 'sunrise_http':
            sunrise_http()
        else:
            globals()['global_action'](cmd)

    elif cmd in ['bright', 'brightness']:
        if len(sys.argv) < 3:
            logger.error('Brightness requires a value, e.g. "%s 50"', cmd)
            return
        try:
            brightness = int(sys.argv[2])
        except (TypeError, ValueError):
            logger.error('Brightness must be an integer, got %r', sys.argv[2])
            return

        _initialize_cli_bulbs()
        logger.info("Changing brightness to %d", brightness)
        for bulb in bulbs:
            bulb.set_brightness(brightness)

    else:
        logger.info("Command \"%s\" not found" % cmd)


def rebuild_bulbs(discovered_bulb_ips=None):
    """Reconcile all rooms against one discovery result.

    Pass ``discovered_bulb_ips`` when discovery has already run on the
    background discovery worker; this function then performs no scan itself.
    """
    if discovered_bulb_ips is None:
        discovered_bulb_ips = [
            bulb['ip'] for bulb in yeelight.discover_bulbs(3)
            if bulb.get('ip') in BULB_IPS
        ]
    discovered = sorted(set(discovered_bulb_ips) & set(BULB_IPS))

    if YEELIGHT_ROOM_HANDLES_REBUILD:
        for room in ROOMS.values():
            room.rebuild_bulbs(discovered_bulb_ips=discovered)
    else:
        global bulbs
        current_bulbs_ips = sorted(bulb._ip for bulb in bulbs)
        if current_bulbs_ips != discovered:
            new_ips = set(discovered) - set(current_bulbs_ips)
            missing_ips = set(current_bulbs_ips) - set(discovered)
            for new_ip in sorted(new_ips):
                logger.info('Found new bulb at ip addr: %s', new_ip)
            for missing_ip in sorted(missing_ips):
                logger.info('Missing bulb at ip addr: %s', missing_ip)

            bulbs = [Bulb(found_ip) for found_ip in discovered]
            for room in ROOMS.values():
                room.rebuild_bulbs(discovered_bulb_ips=discovered)


def websocketTest(pipe):
    def websocketHandler(ws):
        while True:
            contents = pipe.recv()
            logger.info(f'In websocket, got {contents}')
            try:
                contents = json.dumps(contents)
            except:
                contents = str(contents)
            ws.send(contents)

    try:
        import websockets.sync.server
        with websockets.sync.server.serve(websocketHandler, '10.0.0.18', 9002) as server:
            server.serve_forever()
    except Exception:
        logger.exception('Got exception for websocket')


class Server(object):
    """
    Acts as a server.
    Waits on different events to trigger
        Bulbs appearing (power switch turned on)
        Bulbs disappearing (power switch turned off)
        Timeout interval (5 min)
        PC or Phone disappearing from network (turned off or me leaving the apartment)
        
    :return:
    """
    
    def __init__(self):
        global ENV_STATE
        setprocname('Yeelight Lights server')
        set_IRL_sunset()
        self.envState = ENV_STATE
        for room in ROOMS.values():
            try:
                room.resetFromLoggedState()
            except Exception as e:
                logger.error(e)
        self.wake_condition = mp.Condition()
        self.TIMEOUT_INTERVAL = 5*60 # 5 min


        self.bulb_event = mp.Event()
        #self.bulb_pipe, bulb_child_pipe = mp.Pipe()
        if USE_MONITOR_ADVERT_BULBS:
            self.monitor_bulb_advert_proc = mp.Process(target=monitor_advert_bulbs, args=(self.bulb_event, self.wake_condition,))
        if USE_MONITOR_BULB_STATIC:
            self.monitor_bulb_static_proc = mp.Process(target=monitor_bulb_static, args=(self.bulb_event, self.wake_condition,))
        if USE_MONITOR_BULB_PING:
            self.monitor_bulb_ping_proc = mp.Process(target=monitor_bulb_ping, args=(self.bulb_event, self.wake_condition,))


        self.ping_event = mp.Event()
        self.ping_pipe, ping_child_pipe = mp.Pipe()
        self.check_ping_proc = mp.Process(target=checkPingThreaded, args=(self.ping_event, ping_child_pipe, self.wake_condition, self.envState.pcStatus, self.envState.phoneStatus,))
        self.ping_res = None
        self.ping_results = []
        self.switch_requests = []
        self.http_requests = []
        self.bulb_wake = False
        self.timer_wake = False


        self.switch_event = mp.Event()
        self.switch_pipe, switch_child_pipe = mp.Pipe()
        self.monitor_switches_proc = mp.Process(target=monitor_switches, args=(self.switch_event, self.wake_condition, switch_child_pipe, ))
        self.switch_room = None
        self.switch_action = None


        self.http_event = mp.Event()
        self.http_pipe, http_child_pipe = mp.Pipe()
        # The dedicated push queue is initialized before the HTTP child process.
        self.status_push_queue = mp.Queue(maxsize=16)
        self.http_proc = mp.Process(
            target=http_server,
            args=(self.http_event, self.wake_condition, http_child_pipe, self.status_push_queue),
        )
        self.http_res = None


        self.websocket_pipe, websocket_child_pipe = mp.Pipe()
        self.websocket_proc = mp.Process(target=websocketTest, args=(websocket_child_pipe,), daemon=True)

        # All runtime bulb I/O and room recovery are serialized on a daemon
        # worker, so a failed bulb cannot block the event loop from collecting
        # switches/HTTP events or answering status requests from cached state.
        self.control_queue = queue.PriorityQueue()
        self.control_sequence = count()
        self.control_stop = threading.Event()
        self.control_thread = threading.Thread(
            target=self._control_worker, name='yeelight-control-worker', daemon=True
        )

        # SSDP discovery runs separately from room recovery and normal control.
        # A boolean pending flag coalesces multiple triggers received during a scan.
        self.discovery_request_event = threading.Event()
        self.discovery_stop = threading.Event()
        self.discovery_lock = threading.Lock()
        self.discovery_pending = False
        self.discovery_thread = threading.Thread(
            target=self._discovery_worker, name='yeelight-discovery-worker', daemon=True
        )

        self.status_stop = threading.Event()
        # The HTTP process receives snapshots through this separate channel;
        # the watcher only checks Room-owned dictionaries and performs no bulb I/O.
        self.status_push_lock = threading.Lock()
        self.last_pushed_snapshot = None
        self.status_push_thread = threading.Thread(
            target=self._status_push_refresh_scheduler,
            name='yeelight-status-push-scheduler', daemon=True,
        )
        signal.signal(signal.SIGTERM, self.graceful_shutdown)

    def graceful_shutdown(self, *args, **kwargs):
        """
        Gracefully shut down the server, closing pipes, killing child procs, writing out states.
        :return:
        """
        logger.info('Gracefully shutting down lights server')
        self.discovery_stop.set()
        self.discovery_request_event.set()
        self.control_stop.set()
        self.status_stop.set()
        for room in ROOMS.values():
            if room.influx_client is not None:
                room.influx_client.close()
        if USE_MONITOR_BULB_STATIC:
            self.monitor_bulb_static_proc.kill()
        if USE_MONITOR_ADVERT_BULBS:
            self.monitor_bulb_advert_proc.kill()
        if USE_MONITOR_BULB_PING:
            self.monitor_bulb_ping_proc.kill()
        self.check_ping_proc.kill()
        self.monitor_switches_proc.kill() #TODO
        self.http_proc.terminate()
        self.websocket_proc.kill()
        self.ping_pipe.close()
        self.switch_pipe.close()
        self.http_pipe.close()
        self.websocket_pipe.close()
        for room in ROOMS.values():
            room.graceful_kill()
            
        sys.exit(0)

    def _submit_control(self, label, func, *args, **kwargs):
        """Queue one serialized control/recovery task without blocking the loop.

        Lower priority numbers run first; an insertion counter preserves FIFO order
        within a priority. User commands outrank maintenance and inventory recovery.
        """
        priority = kwargs.pop('_priority', 2)
        self.control_queue.put((priority, next(self.control_sequence), label, func, args, kwargs))

    def _control_worker(self):
        while not self.control_stop.is_set():
            try:
                task = self.control_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if task is None:
                self.control_queue.task_done()
                return
            _, _, label, func, args, kwargs = task
            try:
                func(*args, **kwargs)
            except Exception:
                logger.exception('Background control task failed: %s', label)
            finally:
                self.control_queue.task_done()

    def _request_bulb_discovery(self, reason='bulb event'):
        with self.discovery_lock:
            self.discovery_pending = True
        self.discovery_request_event.set()
        logger.debug('Queued background bulb discovery (%s)', reason)

    def _discovery_worker(self):
        while not self.discovery_stop.is_set():
            self.discovery_request_event.wait()
            self.discovery_request_event.clear()
            if self.discovery_stop.is_set():
                break

            while not self.discovery_stop.is_set():
                with self.discovery_lock:
                    if not self.discovery_pending:
                        break
                    self.discovery_pending = False
                try:
                    discovered = sorted({
                        bulb['ip']
                        for bulb in yeelight.discover_bulbs(3)
                        if bulb.get('ip') in BULB_IPS
                    })
                    logger.info(
                        'Background bulb discovery completed; configured bulbs found: %s',
                        ', '.join(discovered) if discovered else '<none>',
                    )
                    # Recovery can involve multiple property reads and retries;
                    # enqueue it on the control worker rather than doing it here
                    # or in the event loop.
                    self._submit_control(
                        'reconcile discovered bulb inventory',
                        rebuild_bulbs,
                        discovered,
                        _priority=10,
                    )
                except Exception:
                    logger.exception('Background bulb discovery failed')

    def _cached_properties(self, room_name, properties):
        """Return the Room-owned state snapshot; do not query a bulb."""
        room = ROOMS.get(room_name)
        if room is None:
            return {}
        return room.get_dashboard_properties(properties)

    def _status_push_refresh_scheduler(self):
        """Watch Room snapshots and push changes, without querying any bulbs."""
        while not self.status_stop.is_set():
            try:
                self._publish_status_snapshot()
            except Exception:
                logger.exception('Could not inspect Room dashboard snapshots for SSE')
            # This is an in-memory dictionary check only; no socket or IPC operation.
            if self.status_stop.wait(0.2):
                break

    def _publish_status_snapshot(self):
        """Publish changed Room-owned snapshots through the separate HTTP queue."""
        snapshot = {}
        for room_name, room in ROOMS.items():
            if room is None:
                continue
            values = room.get_dashboard_properties(
                ('power', 'bright', 'ct', 'current_brightness')
            )
            # Don't publish an incomplete room as if it were valid UI status.
            if any(values.get(key) is None for key in ('power', 'bright', 'ct')):
                continue
            current_brightness = values.get('current_brightness')
            if current_brightness is None:
                current_brightness = values.get('bright')
            snapshot[room_name] = {
                'power': str(values['power']),
                'bright': str(values['bright']),
                'ct': str(values['ct']),
                'current_brightness': str(current_brightness),
            }

        if not snapshot:
            return
        with self.status_push_lock:
            if snapshot == self.last_pushed_snapshot:
                return
            try:
                self.status_push_queue.put_nowait(snapshot)
            except queue.Full:
                # A slow event relay should receive the latest snapshot, not build a
                # backlog of stale UI states.
                try:
                    self.status_push_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.status_push_queue.put_nowait(snapshot)
                except queue.Full:
                    logger.warning('SSE status queue is full; dropping one status snapshot')
                    return
            except (OSError, ValueError):
                logger.exception('Could not enqueue SSE status snapshot')
                return
            self.last_pushed_snapshot = snapshot

    def _handle_dashboard_query(self, http_res):
        if http_res.get('query') != 'getProperty':
            return
        logger.debug('dashboard-query getProperty (Room snapshot)')
        room_name = http_res.get('room')
        properties = list(http_res.get('properties', ()))
        if room_name == 'global':
            result = {
                name: self._cached_properties(name, properties)
                for name in ROOMS
            }
        elif room_name in ROOMS:
            result = self._cached_properties(room_name, properties)
        else:
            logger.error('Unknown room in dashboard query: %s', room_name)
            result = {}
        # This reply is made entirely from Room dictionaries. No bulb access and no
        # background refresh task is scheduled by a dashboard GET.
        self.http_pipe.send(result)

    def _process_ping_result(self, phone_status, pc_status, ping_res):
        self.envState.phoneStatus = phone_status
        self.envState.pcStatus = pc_status
        self.ping_res = ping_res
        if ping_res:
            global_action('on')
            global_action('autoset', force=True)
            writeManualOverride()
            global_action('writeState', 'off', self.envState.pcStatus, self.envState.phoneStatus)
        else:
            # Temp fix for PC not having a valid IP address on waking from sleep.
            sunrise_time = datetime.datetime.strptime(SUNRISE_TIME, '%I:%M:%p').time()
            now_time = datetime.datetime.now().time()
            sunrise_end = (
                datetime.datetime.combine(datetime.date.today(), sunrise_time)
                + datetime.timedelta(hours=1)
            ).time()
            if not (sunrise_time <= now_time <= sunrise_end):
                global_action('off', force=True)
                writeManualOverride(offset=datetime.timedelta(days=30))
                global_action('writeState', 'off', self.envState.pcStatus, self.envState.phoneStatus)

    def _process_switch_request(self, switch_room, switch_action):
        logger.info('Switch in %s hit for %s', switch_room, switch_action)
        kwargs = (
            {'autosetDuration': SWITCH_FLOW_DURATION, 'force': True, 'forceLight': True}
            if switch_action == 'autoset' else {}
        )
        if switch_action in COMMANDS_WITH_DURATION:
            kwargs['duration'] = SWITCH_FLOW_DURATION
        getattr(ROOMS[switch_room], switch_action)(**kwargs)
        writeManualOverride(
            switch_room,
            datetime.timedelta(hours=2),
            action=('MANUAL_AUTOSET_FORCE_LIGHT' if switch_action == 'autoset' else switch_action),
        )

    def _process_http_request(self, http_res):
        logger.info('http')
        logger.info(http_res)
        if http_res['eventType'] == HTTP_EVENT_FROM_PC:
            global_action('writeState', http_res['action'])
            if not SERVER_ACTS_NOT_CLIENT:
                logger.info('Manual http event, no further action taken')
                return

        if http_res['eventType'] in ('dashboard-action', 'zigbee', 'zigbeeSwitch') or (
            SERVER_ACTS_NOT_CLIENT and http_res['eventType'] == HTTP_EVENT_FROM_PC
        ):
            action = http_res['action']
            if action not in bulbCommands:
                logger.error('Received %s as a command, which is not a valid command!', action)
                return
            kwargs = dict(http_res.get('kwargs', {}))
            if action in COMMANDS_WITH_DURATION:
                kwargs['duration'] = SWITCH_FLOW_DURATION
            if action == 'autoset':
                kwargs['force'] = True
                kwargs['autosetDuration'] = 3000
                if http_res['eventType'] == 'zigbeeSwitch':
                    kwargs['forceLight'] = True
                    kwargs['autosetDuration'] = SWITCH_FLOW_DURATION

            room_name = http_res['room']
            if room_name == 'global':
                logger.info('global http')
                global_action(action, **kwargs)
            elif room_name in ROOMS:
                logger.info('Room level http')
                getattr(ROOMS[room_name], action)(**kwargs)
            else:
                logger.error('Received unknown HTTP room %s', room_name)
                return

            if http_res['eventType'] in ('dashboard-action', 'zigbeeSwitch') or (
                SERVER_ACTS_NOT_CLIENT and http_res['eventType'] == HTTP_EVENT_FROM_PC
            ):
                writeManualOverride(
                    room_name if room_name != 'global' else None,
                    datetime.timedelta(hours=2),
                    action=('MANUAL_AUTOSET_FORCE_LIGHT' if action == 'autoset' else action),
                )

    def _process_timer_wake(self, timer_wake):
        logger.info('Timer wake')
        if not (self.envState.phoneStatus and self.envState.pcStatus):
            logger.info(
                'Phone(%s) and/or pc(%s) is offline, keeping lights off.',
                str(self.envState.phoneStatus), str(self.envState.pcStatus),
            )
            global_action('off', auto=True)
        else:
            global_action(
                'autoset',
                AUTOSET_DURATION if timer_wake else 300,
                autoset_auto_var=not timer_wake,
            )

    def wake_predicate(self):
        """
        The wake condition for the main thread
        :return:
        """
        return self.ping_event.is_set() or self.bulb_event.is_set() or self.switch_event.is_set() or self.http_event.is_set()
    
    def _drain_event_pipe(self, event, pipe, label, acknowledge=False, max_messages=256):
        """Drain a signaled child pipe without allowing EOF to spin the main loop.

        Call this while holding ``wake_condition``. Producers also send and set
        their events while holding that condition, so clearing the event before
        draining cannot lose a concurrent notification.
        """
        event.clear()
        messages = []
        pipe_closed = False

        for _ in range(max_messages):
            try:
                if not pipe.poll():
                    break
                message = pipe.recv()
            except (EOFError, OSError) as exc:
                # multiprocessing.Connection.poll() can be true at EOF. If recv()
                # then raises and the event is left set, run() repeatedly retries
                # this same path without ever reaching its timed wait.
                logger.warning('%s event pipe closed while draining: %s', label, exc)
                pipe_closed = True
                break

            messages.append(message)
            if acknowledge:
                try:
                    pipe.send(0)
                except (EOFError, OSError) as exc:
                    logger.warning('%s event pipe could not be acknowledged: %s', label, exc)
                    pipe_closed = True
                    break
        else:
            # Bound each drain so a producer that floods events cannot starve
            # the rest of the server. Leave a wake pending if more messages remain.
            try:
                if not pipe_closed and pipe.poll():
                    logger.warning('%s event pipe has more than %d queued messages; continuing next loop', label, max_messages)
                    event.set()
            except (EOFError, OSError):
                pipe_closed = True

        return messages

    def resolve_wake(self):
        """Collect pending payloads and consume each wake signal safely."""
        logger.info("Resolving wake")
        self.ping_results = []
        self.switch_requests = []
        self.http_requests = []
        self.bulb_wake = self.bulb_event.is_set()

        if self.bulb_wake:
            self.bulb_event.clear()
            logger.info("Resolving bulb event")

        if self.ping_event.is_set():
            logger.info("Resolving ping event")
            self.ping_results = self._drain_event_pipe(
                self.ping_event, self.ping_pipe, 'ping'
            )

        if self.switch_event.is_set():
            logger.info("Resolving switch event")
            # The switch child waits for one acknowledgement per request.
            self.switch_requests = self._drain_event_pipe(
                self.switch_event, self.switch_pipe, 'switch', acknowledge=True
            )

            # Run the shell/console notification off the event-loop thread.
            if self.switch_requests:
                self._submit_control(
                    'switch notification beep',
                    os.system,
                    "sudo sh -c \"echo -e '\\a' > /dev/console\"",
                    _priority=9,
                )
            for switch_room, switch_action in self.switch_requests:
                try:
                    self.websocket_pipe.send((switch_room, switch_action))
                except (EOFError, OSError):
                    logger.exception('Could not forward switch request to websocket process')
                    break

        if self.http_event.is_set():
            logger.info("Resolving http event")
            self.http_requests = self._drain_event_pipe(
                self.http_event, self.http_pipe, 'http'
            )

    def run(self):
        """Run the event loop; slow discovery and bulb work run on daemon workers."""
        logger.error('Booting server')
        self.control_thread.start()
        self.discovery_thread.start()
        self.status_push_thread.start()

        if USE_MONITOR_ADVERT_BULBS:
            self.monitor_bulb_advert_proc.start()
        if USE_MONITOR_BULB_STATIC:
            self.monitor_bulb_static_proc.start()
        if USE_MONITOR_BULB_PING:
            self.monitor_bulb_ping_proc.start()
        self.check_ping_proc.start()
        self.monitor_switches_proc.start()
        self.http_proc.start()
        self.websocket_proc.start()

        systemStartTime = datetime.datetime.utcnow()
        self._submit_control('initial autoset', global_action, 'autoset', force=True, _priority=0)

        rapid_loop_count = 0
        last_spin_warning = 0.0
        while True:
            loop_started = time.monotonic()
            try:
                self.timer_wake = True
                self.switch_room, self.switch_action = None, None
                self.http_res = None
                self.ping_res = None
                self.ping_results = []
                self.switch_requests = []
                self.http_requests = []
                self.bulb_wake = False
                with self.wake_condition:
                    self.wake_condition.wait_for(self.wake_predicate, self.TIMEOUT_INTERVAL)
                    if self.wake_predicate():
                        self.resolve_wake()

                logger.info('Woke up')
                event_processed = bool(
                    self.bulb_wake or self.ping_results or self.switch_requests or self.http_requests
                )

                if self.bulb_wake:
                    # No UDP discovery or bulb recovery on the event-loop thread.
                    self._request_bulb_discovery('monitor event')

                for phone_status, pc_status, ping_res in self.ping_results:
                    self.timer_wake = False
                    self._submit_control(
                        'process presence change',
                        self._process_ping_result,
                        phone_status, pc_status, ping_res,
                        _priority=0,
                    )

                for switch_room, switch_action in self.switch_requests:
                    if switch_room == SWITCH_RESTART_KEYWORD:
                        logger.info('Got restart request from switch handler')
                        self.monitor_switches_proc.kill()
                        self.switch_pipe.close()
                        self.switch_event = mp.Event()
                        self.switch_pipe, switch_child_pipe = mp.Pipe()
                        self.monitor_switches_proc = mp.Process(
                            target=monitor_switches,
                            args=(self.switch_event, self.wake_condition, switch_child_pipe),
                        )
                        self.monitor_switches_proc.start()
                        continue
                    if switch_room == '' and switch_action is None:
                        continue
                    if switch_room not in ROOMS:
                        logger.error(
                            'Received %s from switch_room, which is not in %s',
                            switch_room, ', '.join(ROOMS),
                        )
                        continue
                    # Queue the action in arrival order. Pipe acknowledgements are
                    # already sent during drain, so the switch monitor is not held
                    # open while the bulbs are being controlled or recovered.
                    self._submit_control(
                        'switch command %s/%s' % (switch_room, switch_action),
                        self._process_switch_request,
                        switch_room, switch_action,
                        _priority=0,
                    )

                for http_res in self.http_requests:
                    if http_res.get('eventType') == 'dashboard-query':
                        # Queries return cached values immediately and refresh them
                        # asynchronously through the same serialized bulb worker.
                        self._handle_dashboard_query(http_res)
                    else:
                        # Copy nested kwargs because the handler applies defaults.
                        queued_http = dict(http_res)
                        queued_http['kwargs'] = dict(http_res.get('kwargs', {}))
                        self._submit_control(
                            'HTTP command %s/%s' % (queued_http.get('room'), queued_http.get('action')),
                            self._process_http_request,
                            queued_http,
                            _priority=0,
                        )

                if not event_processed:
                    self.ping_res = None
                    self.switch_room, self.switch_action = None, None
                    self.http_res = None
                    self._submit_control(
                        'timer autoset/off', self._process_timer_wake, self.timer_wake,
                        _priority=2,
                    )

                if (systemStartTime + datetime.timedelta(days=3)) < datetime.datetime.utcnow():
                    systemStartTime = datetime.datetime.utcnow()
                    set_IRL_sunset()

            except Exception:
                logger.exception('Exception in server run loop!')
                # Error recovery is also asynchronous; don't perform a three-second
                # discovery and bulb state restoration inside this loop.
                self._request_bulb_discovery('event-loop exception')
                time.sleep(0.05)
            finally:
                elapsed = time.monotonic() - loop_started
                if elapsed < 0.01:
                    rapid_loop_count += 1
                else:
                    rapid_loop_count = 0

                if rapid_loop_count >= 20:
                    now_monotonic = time.monotonic()
                    if now_monotonic - last_spin_warning >= 60:
                        logger.error(
                            'Main loop spin guard activated: 20 consecutive iterations '
                            'completed in under 10ms (ping=%s bulb=%s switch=%s http=%s)',
                            self.ping_event.is_set(), self.bulb_event.is_set(),
                            self.switch_event.is_set(), self.http_event.is_set(),
                        )
                        last_spin_warning = now_monotonic
                    time.sleep(0.05)
                    rapid_loop_count = 0


def run_server():
    try:
        server = Server()
    except Exception:
        logger.exception("Exception when setting up server")
        raise
    try:
        server.run()
    except Exception:
        logger.exception("Unrecoverable error encountered when running server!")
        raise

def global_action(action, *args, **kwargs):
    if action not in bulbCommands + ['writeState']:
        logger.error('%s is not a valid global action!', action)
        return
    ex = None
    for roomName, room in ROOMS.items():
        for attempt in range(3):
            logger.info("Global action for %s", roomName)
            try:
                getattr(room, action)(*args, **kwargs)
                break
            except Exception as e:
                ex = e
                logger.exception('Failed to execute %s on try %d for %s\n%s\nargs:%s\nkwargs:%s', action, attempt+1, room.name, str(e), ', '.join(str(x) for x in args), kwargs)
        else:
            raise ex
        
        
def sunrise():
    """
    Simulate a sunrise.
    :return:
    """
    # Prevent autoset from taking over
    writeManualOverride(offset=datetime.timedelta(hours=2))
    
    # Write the new state, prevent timing collisions
    global_action('writeState','day')
    
    bulbLog.info('Sunrise start')
    overallDuration = 1200000  # 1200000 == 20 min
    global_action('on')
    try:
        blbs = [blb for room_blbs in ROOMS.values() for blb in room_blbs]
        for bulb in blbs:
            bulb.set_brightness(0)
            bulb.set_rgb(255, 0, 0)

        time.sleep(1)

        transitions = [yeelight.HSVTransition(hue=39, saturation=100,
                                              duration=overallDuration * 0.5, brightness=80),
                       yeelight.TemperatureTransition(degrees=3200,
                                                      duration=overallDuration * 0.5, brightness=80)]

        for bulb in blbs:
            bulb.start_flow(yeelight.Flow(count=1, action=yeelight.Flow.actions.stay, transitions=transitions))

    except Exception:
        logger.exception('Got exception during sunrise')

def sunrise_http():
    """
    http call for sunrise
    """
    import requests
    requests.post('http://10.0.0.18:%d' % REST_SERVER_PORT_NUMBER, json={'newState':'sunrise', 'eventType':'dashboard'}, timeout=60)


if __name__ == "__main__":
    # Run the system tray app
    # run the python script
    main()
