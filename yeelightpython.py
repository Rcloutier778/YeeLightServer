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

def main():
    # logger.info(desk.get_properties())
    global bulbs
    global ROOMS
    
    if len(sys.argv) == 1:
        logger.info("No arguments.")
        logger.warning('No arguments.')
        return
    else:
        cmd = sys.argv[1].lower()
        if cmd in allcommands:
            if cmd in commands:
                if 'autoset' not in cmd:
                    logger.info(cmd)
                bulbs = []
                assert all(bulb_ips in set( y for x in room_to_ips.values() for y in x) for bulb_ips in BULB_IPS)
                for roomName, ips in room_to_ips.items():
                    blbs = []
                    for ip in ips:
                        bulb = Bulb(ip, roomName=roomName)
                        bulbs.append(bulb)
                        blbs.append(bulb)
                    ROOMS[roomName] = Room(roomName, [], ENV_STATE)
                if cmd == 'run_server':
                    run_server()
                elif cmd == 'sunrise':
                    sunrise()
                elif cmd == 'sunrise_http':
                    sunrise_http()
                else:
                    globals()['global_action'](cmd)
        elif cmd in ['bright', 'brightness']:
            if type(sys.argv[2]) == int:
                logger.info("Changing brightness to %d" % int(sys.argv[2]))
                for i in bulbs:
                    i.set_brightness(int(sys.argv[1]))
        else:
            logger.info("Command \"%s\" not found" % cmd)


def rebuild_bulbs():
    "Rebuild the bulb list."
    if YEELIGHT_ROOM_HANDLES_REBUILD:
        for room in ROOMS.values():
            room.rebuild_bulbs()
    else:
        global bulbs
        found_bulbs_ip = sorted(bulb['ip'] for bulb in yeelight.discover_bulbs(1))
        current_bulbs_ips = sorted(bulb._ip for bulb in bulbs)
        if current_bulbs_ips != found_bulbs_ip:
            new_ips = set(found_bulbs_ip) - set(current_bulbs_ips)
            missing_ips = set(current_bulbs_ips) - set(found_bulbs_ip)
            for new_ip in new_ips:
                logger.info('Found new bulb at ip addr: %s', new_ip)
            for missing_ip in missing_ips:
                logger.info('Missing bulb at ip addr: %s', missing_ip)
                
            bulbs = [Bulb(found_ip) for found_ip in found_bulbs_ip]
            for room in ROOMS.values():
                room.rebuild_bulbs()


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
        self.ping_res = True
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
        """
        Resolve whatever event woke the main thread
        :return:
        """
        logger.info("Resolving wake")
        if self.bulb_event.is_set():
            logger.info("Resolving bulb event")
            #logger.critical("Skipping bulb wake event")
            rebuild_bulbs()
            self.bulb_event.clear()
        if self.ping_event.is_set():
            logger.info("Resolving ping event")
            self.timer_wake = False
            phoneStatus, pcStatus, self.ping_res = self.ping_pipe.recv()
            self.envState.phoneStatus = phoneStatus
            self.envState.pcStatus = pcStatus
            self.ping_event.clear()
        if self.switch_event.is_set():
            logger.info("Resolving switch event")
            self.switch_room, self.switch_action = self.switch_pipe.recv()
            self.switch_event.clear()
            self.switch_pipe.send(0)
            os.system('''sudo sh -c "echo -e '\a' > /dev/console"''')
            self.websocket_pipe.send((self.switch_room, self.switch_action))
            # Ensure pipe is empty
            while True:
                if self.switch_pipe.poll():
                    logger.warn("Found extra items in pipe!: %s", str( self.switch_pipe.recv() ) )
                else:
                    break

            
            # switch proc did not get the ack from us and has sent a still_alive request
            if self.switch_room == '' and self.switch_action is None:
                self.switch_room = None
        if self.http_event.is_set():
            logger.info("Resolving http event")
            self.http_res = self.http_pipe.recv()
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
                with self.wake_condition:
                    self.wake_condition.wait_for(self.wake_predicate, self.TIMEOUT_INTERVAL)
                    if self.wake_predicate():
                        self.resolve_wake()
                logger.info("Woke up")
                if self.ping_res is not None:
                    if self.ping_res:
                        global_action('on')
                        global_action('autoset', force=True)
                        writeManualOverride()
                        global_action('writeState', 'off', self.envState.pcStatus, self.envState.phoneStatus)
                    else:
                        # Temp fix for PC not having a valid IP address on waking from sleep.
                        sunrise_time = datetime.datetime.strptime(SUNRISE_TIME, '%I:%M:%p')
                        if datetime.datetime.now().time() >= sunrise_time.time() and datetime.datetime.now().time() <= (sunrise_time + datetime.timedelta(hours=1)).time():
                            continue
                        global_action('off', force=True)
                        writeManualOverride(offset=datetime.timedelta(days=30))
                        global_action('writeState', 'off', self.envState.pcStatus, self.envState.phoneStatus)
                elif self.switch_room:
                    if self.switch_room == SWITCH_RESTART_KEYWORD:
                        logger.info("Got restart request from switch handler")

                        self.monitor_switches_proc.kill()
                        self.switch_pipe.close()
                        self.switch_event = mp.Event()
                        self.switch_pipe, switch_child_pipe = mp.Pipe()
                        self.monitor_switches_proc = mp.Process(target=monitor_switches, args=(self.switch_event, self.wake_condition, switch_child_pipe, ))
                        self.switch_room = None
                        self.switch_action = None
                        self.monitor_switches_proc.start()
                        continue
                    if self.switch_room not in ROOMS:
                        logger.error('Received %s from switch_room, which is not in %s', self.switch_room, ', '.join(ROOMS))
                        continue
                    logger.info('Switch in %s hit for %s', self.switch_room, self.switch_action)
                    kwargs = {'autosetDuration': SWITCH_FLOW_DURATION, 'force':True, 'forceLight':True} if self.switch_action == 'autoset' else {}
                    if self.switch_action in COMMANDS_WITH_DURATION:
                        kwargs['duration'] = SWITCH_FLOW_DURATION
                    getattr(ROOMS[self.switch_room], self.switch_action)(**kwargs)
                    writeManualOverride(
                        self.switch_room,
                        datetime.timedelta(hours=2),
                        action='MANUAL_AUTOSET_FORCE_LIGHT' if self.switch_action == 'autoset' else self.switch_action
                    )
                elif self.http_res is not None:
                    logger.info('http')
                    logger.info(self.http_res)
                    if self.http_res['eventType'] == HTTP_EVENT_FROM_PC:
                        global_action('writeState',self.http_res["action"])
                        if not SERVER_ACTS_NOT_CLIENT:
                            logger.info('Manual http event, no further action taken')
                            continue
                    if self.http_res['eventType'] in ('dashboard-action', 'zigbee', 'zigbeeSwitch') or (SERVER_ACTS_NOT_CLIENT and self.http_res['eventType'] == HTTP_EVENT_FROM_PC):
                        if self.http_res['action'] not in bulbCommands:
                            logger.error('Received %s as a command, which is not a valid command!' % self.http_res['action'])
                            continue
                        if self.http_res['action'] in COMMANDS_WITH_DURATION:
                            self.http_res['kwargs']['duration'] = SWITCH_FLOW_DURATION
                        if self.http_res['action'] == 'autoset':
                            self.http_res['kwargs']['force'] = True
                            self.http_res['kwargs']['autosetDuration'] = 3000
                            if self.http_res['eventType'] == 'zigbeeSwitch':
                                self.http_res['kwargs']['forceLight'] = True
                                self.http_res['kwargs']['autosetDuration'] = SWITCH_FLOW_DURATION
                        if self.http_res['room'] == 'global':
                            logger.info('global http')
                            global_action(self.http_res['action'], **self.http_res['kwargs'])
                        else:
                            logger.info('Room level http')
                            getattr(ROOMS[self.http_res['room']], self.http_res['action'])(**self.http_res['kwargs'])

                        if self.http_res['eventType'] in ('dashboard-action', 'zigbeeSwitch') or (SERVER_ACTS_NOT_CLIENT and self.http_res['eventType'] == HTTP_EVENT_FROM_PC):
                            writeManualOverride(
                                self.http_res['room'] if self.http_res['room'] != 'global' else None,
                                datetime.timedelta(hours=2),
                                action=('MANUAL_AUTOSET_FORCE_LIGHT' if self.http_res['action'] == 'autoset' else self.http_res['action'])
                            )
                    elif self.http_res['eventType'] == 'dashboard-query':
                        logger.info('dashboard-query')
                        if self.http_res['query'] == 'getProperty':
                            logger.info('getProperty')
                            tmp_bulbs = ROOMS[self.http_res['room']].bulbs
                            if tmp_bulbs:
                                self.http_pipe.send(tmp_bulbs[0].get_properties([self.http_res['properties']]))
                else:
                    logger.info('Timer wake')
                    if not ( self.envState.phoneStatus and self.envState.pcStatus ):
                        logger.info("Phone(%s) and/or pc(%s) is offline, keeping lights off.", str(self.envState.phoneStatus), str(self.envState.pcStatus))
                        global_action('off', auto=True)#, autoset_auto_var = not self.timer_wake)
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
