import atexit
import datetime
from functools import wraps
import json
import logging
import multiprocessing as mp
import os
import pickle
import platform
import signal
import sys
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


def rebuild_bulbs():
    """Discover configured Yeelights once, then share the result with every room."""
    discovered = sorted({
        bulb['ip']
        for bulb in yeelight.discover_bulbs(3)
        if bulb.get('ip') in BULB_IPS
    })

    if YEELIGHT_ROOM_HANDLES_REBUILD:
        for room in ROOMS.values():
            room.rebuild_bulbs(discovered_bulb_ips=discovered)
    else:
        global bulbs
        current_bulbs_ips = sorted(bulb._ip for bulb in bulbs)
        if current_bulbs_ips != discovered:
            new_ips = set(discovered) - set(current_bulbs_ips)
            missing_ips = set(current_bulbs_ips) - set(discovered)
            for new_ip in new_ips:
                logger.info('Found new bulb at ip addr: %s', new_ip)
            for missing_ip in missing_ips:
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
        self.http_proc = mp.Process(target=http_server, args=(self.http_event, self.wake_condition, http_child_pipe, ))
        self.http_res = None


        self.websocket_pipe, websocket_child_pipe = mp.Pipe()
        self.websocket_proc = mp.Process(target=websocketTest, args=(websocket_child_pipe,), daemon=True)
        signal.signal(signal.SIGTERM, self.graceful_shutdown)

    def graceful_shutdown(self, *args, **kwargs):
        """
        Gracefully shut down the server, closing pipes, killing child procs, writing out states.
        :return:
        """
        logger.info('Gracefully shutting down lights server')
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

    def wake_predicate(self):
        """
        The wake condition for the main thread
        :return:
        """
        return self.ping_event.is_set() or self.bulb_event.is_set() or self.switch_event.is_set() or self.http_event.is_set()
    
    def resolve_wake(self):
        """Collect all currently pending event payloads without dropping bursts."""
        logger.info("Resolving wake")
        self.ping_results = []
        self.switch_requests = []
        self.http_requests = []
        self.bulb_wake = self.bulb_event.is_set()

        if self.bulb_wake:
            logger.info("Resolving bulb event")
            self.bulb_event.clear()

        if self.ping_event.is_set():
            logger.info("Resolving ping event")
            while self.ping_pipe.poll():
                self.ping_results.append(self.ping_pipe.recv())
            self.ping_event.clear()

        if self.switch_event.is_set():
            logger.info("Resolving switch event")
            # The switch child waits for an acknowledgement before sending the next
            # request. Acknowledge each request rather than discarding later payloads.
            while self.switch_pipe.poll():
                request = self.switch_pipe.recv()
                self.switch_requests.append(request)
                self.switch_pipe.send(0)
            self.switch_event.clear()
            
            # Have server beep
            os.system("sudo sh -c \"echo -e '\\a' > /dev/console\"")
            for switch_room, switch_action in self.switch_requests:
                self.websocket_pipe.send((switch_room, switch_action))

        if self.http_event.is_set():
            logger.info("Resolving http event")
            while self.http_pipe.poll():
                self.http_requests.append(self.http_pipe.recv())
            self.http_event.clear()

    def run(self):
        """
        Runs the server
        :return:
        """
        logger.error("Booting server")
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
        try:
            global_action('autoset', force=True)
        except Exception:
            logger.exception("Got exception when doing run_server first autoset, ignoring...")
        while True:
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
                logger.info("Woke up")
                event_processed = bool(
                    self.bulb_wake or self.ping_results or self.switch_requests or self.http_requests
                )

                if self.bulb_wake:
                    try:
                        rebuild_bulbs()
                    except Exception:
                        logger.exception('Exception while rebuilding bulbs from wake event')

                for phoneStatus, pcStatus, ping_res in self.ping_results:
                    self.timer_wake = False
                    self.envState.phoneStatus = phoneStatus
                    self.envState.pcStatus = pcStatus
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

                for switch_room, switch_action in self.switch_requests:
                    if switch_room == SWITCH_RESTART_KEYWORD:
                        logger.info("Got restart request from switch handler")
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
                            switch_room, ', '.join(ROOMS)
                        )
                        continue

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
                        action=('MANUAL_AUTOSET_FORCE_LIGHT' if switch_action == 'autoset' else switch_action)
                    )

                for http_res in self.http_requests:
                    logger.info('http')
                    logger.info(http_res)
                    if http_res['eventType'] == HTTP_EVENT_FROM_PC:
                        global_action('writeState', http_res["action"])
                        if not SERVER_ACTS_NOT_CLIENT:
                            logger.info('Manual http event, no further action taken')
                            continue
                    if http_res['eventType'] in ('dashboard-action', 'zigbee', 'zigbeeSwitch') or (SERVER_ACTS_NOT_CLIENT and http_res['eventType'] == HTTP_EVENT_FROM_PC):
                        if http_res['action'] not in bulbCommands:
                            logger.error('Received %s as a command, which is not a valid command!', http_res['action'])
                            continue
                        if http_res['action'] in COMMANDS_WITH_DURATION:
                            http_res['kwargs']['duration'] = SWITCH_FLOW_DURATION
                        if http_res['action'] == 'autoset':
                            http_res['kwargs']['force'] = True
                            http_res['kwargs']['autosetDuration'] = 3000
                            if http_res['eventType'] == 'zigbeeSwitch':
                                http_res['kwargs']['forceLight'] = True
                                http_res['kwargs']['autosetDuration'] = SWITCH_FLOW_DURATION
                        if http_res['room'] == 'global':
                            logger.info('global http')
                            global_action(http_res['action'], **http_res['kwargs'])
                        elif http_res['room'] in ROOMS:
                            logger.info('Room level http')
                            getattr(ROOMS[http_res['room']], http_res['action'])(**http_res['kwargs'])
                        else:
                            logger.error('Received unknown HTTP room %s', http_res['room'])
                            continue

                        if http_res['eventType'] in ('dashboard-action', 'zigbeeSwitch') or (SERVER_ACTS_NOT_CLIENT and http_res['eventType'] == HTTP_EVENT_FROM_PC):
                            writeManualOverride(
                                http_res['room'] if http_res['room'] != 'global' else None,
                                datetime.timedelta(hours=2),
                                action=('MANUAL_AUTOSET_FORCE_LIGHT' if http_res['action'] == 'autoset' else http_res['action'])
                            )
                    elif http_res['eventType'] == 'dashboard-query':
                        logger.info('dashboard-query')
                        if http_res['query'] == 'getProperty':
                            logger.info('getProperty')
                            room_name = http_res['room']
                            if room_name not in ROOMS:
                                logger.error('Unknown room: %s', room_name)
                                self.http_pipe.send({})
                                continue

                            tmp_bulbs = ROOMS[room_name].bulbs
                            if tmp_bulbs:
                                properties = list(http_res.get('properties', ()))
                                self.http_pipe.send(tmp_bulbs[0].get_properties(properties))
                            else:
                                self.http_pipe.send({})

                if not event_processed:
                    self.ping_res = None
                    self.switch_room, self.switch_action = None, None
                    self.http_res = None
                    logger.info('Timer wake')
                    if not (self.envState.phoneStatus and self.envState.pcStatus):
                        logger.info("Phone(%s) and/or pc(%s) is offline, keeping lights off.", str(self.envState.phoneStatus), str(self.envState.pcStatus))
                        global_action('off', auto=True)
                    else:
                        global_action('autoset', AUTOSET_DURATION if self.timer_wake else 300, autoset_auto_var=not self.timer_wake)

                if (systemStartTime + datetime.timedelta(days=3)) < datetime.datetime.utcnow():
                    systemStartTime = datetime.datetime.utcnow()
                    set_IRL_sunset()



            except Exception:
                logger.exception("Exception in server run loop!")
                rebuild_bulbs()


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
                logger.exception('Failed to execute %s on try %d for %s\n%s\nargs:%s\nkwargs:%s', action, attempt+1, room.name, ' '.join(e.args), ', '.join(str(x) for x in args), kwargs)
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
