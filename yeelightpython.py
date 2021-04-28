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


from http.server import BaseHTTPRequestHandler, HTTPServer

from yeelightLib import *
from room import Room
from handlers.checkPing import checkPingThreaded
from handlers.bulb_events import monitor_advert_bulbs, monitor_bulb_static
from handlers.switches import monitor_switches
from handlers.http_events import http_server


os.chdir(HOMEDIR)

logger = getLogger()

bulbLog = getBulbLogger()

bulbs = []

ROOMS = {}

def main():
    # logger.info(desk.get_properties())
    global bulbs
    global ROOMS
    '''
    import subprocess

    response=subprocess.getstatusoutput('ping -n 2 10.0.0.7')
    if 'time=' not in response[1]: #timeout, phone not present.
        logger.info("Phone not present.")
        logger.warning("Phone not present.")
        off()
        return
    '''
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
                        bulb = yeelight.Bulb(ip)
                        bulbs.append(bulb)
                        blbs.append(bulb)
                    ROOMS[roomName] = Room(roomName, blbs)
                if cmd in ('on','off','autoset'):
                    cmd = 'global_%s'%cmd
                globals()[cmd]()
        elif cmd in ['bright', 'brightness']:
            if type(sys.argv[2]) == int:
                logger.info("Changing brightness to %d" % int(sys.argv[2]))
                for i in bulbs:
                    i.set_brightness(int(sys.argv[1]))
        else:
            logger.info("Command \"%s\" not found" % cmd)


def rebuild_bulbs():
    "Rebuild the bulb list."
    global bulbs
    found_bulbs_ip = sorted(bulb['ip'] for bulb in yeelight.discover_bulbs(0.2))
    current_bulbs_ips = sorted(bulb._ip for bulb in bulbs)
    if current_bulbs_ips != found_bulbs_ip:
        new_ips = set(found_bulbs_ip) - set(current_bulbs_ips)
        missing_ips = set(current_bulbs_ips) - set(found_bulbs_ip)
        for new_ip in new_ips:
            logger.info('Found new bulb at ip addr: %s', new_ip)
        for missing_ip in missing_ips:
            logger.info('Missing bulb at ip addr: %s', missing_ip)
            
        bulbs = [yeelight.Bulb(found_ip) for found_ip in found_bulbs_ip]


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
        setprocname('Lights server')
        set_IRL_sunset()
        for room in ROOMS.values():
            room.resetFromLoggedState()
        self.bulb_event = mp.Event()
        self.wake_condition = mp.Condition()
        self.TIMEOUT_INTERVAL = 5*60 # 5 min
        self.monitor_bulb_advert_proc = mp.Process(target=monitor_advert_bulbs, args=(self.bulb_event, self.wake_condition,))
        self.monitor_bulb_static_proc = mp.Process(target=monitor_bulb_static, args=(self.bulb_event, self.wake_condition,))

        self.ping_event = mp.Event()
        self.ping_pipe, ping_child_pipe = mp.Pipe()
        self.check_ping_proc = mp.Process(target=checkPingThreaded, args=(self.ping_event, ping_child_pipe, self.wake_condition,pcStatus, phoneStatus,))
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

        signal.signal(signal.SIGTERM, self.graceful_shutdown)

    def graceful_shutdown(self, *args, **kwargs):
        """
        Gracefully shut down the server, closing pipes, killing child procs, writing out states.
        :return:
        """
        for room in ROOMS.values():
            room.influx_client.close()
        self.monitor_bulb_static_proc.kill()
        self.monitor_bulb_advert_proc.kill()
        self.check_ping_proc.kill()
        self.monitor_switches_proc.kill() #TODO
        self.http_proc.terminate()
        self.ping_pipe.close()
        self.switch_pipe.close()
        self.http_pipe.close()
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
        global phoneStatus, pcStatus
        logger.info("Resolving wake")
        if self.bulb_event.is_set():
            logger.info("Resolving bulb event")
            rebuild_bulbs()
            self.bulb_event.clear()
        if self.ping_event.is_set():
            logger.info("Resolving ping event")
            self.timer_wake = False
            global phoneStatus
            global pcStatus
            phoneStatus, pcStatus, self.ping_res = self.ping_pipe.recv()
            self.ping_event.clear()
        if self.switch_event.is_set():
            logger.info("Resolving switch event")
            self.switch_room, self.switch_action = self.switch_pipe.recv()
            self.switch_event.clear()
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
        self.monitor_bulb_advert_proc.start()
        self.monitor_bulb_static_proc.start()
        self.check_ping_proc.start()
        self.monitor_switches_proc.start()
        self.http_proc.start()
        systemStartTime = datetime.datetime.utcnow()
        while True:
            try:
                self.timer_wake = True
                self.switch_room, self.switch_action = None, None
                self.http_res = None
                with self.wake_condition:
                    self.wake_condition.wait_for(self.wake_predicate, self.TIMEOUT_INTERVAL)
                    if self.wake_predicate():
                        self.resolve_wake()
                logger.info("Woke up")
                if not self.ping_res:
                    # Temp fix for PC not having a valid IP address on waking from sleep.
                    sunrise_time = datetime.datetime.strptime(SUNRISE_TIME, '%I:%M:%p')
                    if datetime.datetime.now().time() >= sunrise_time.time() and datetime.datetime.now().time() <= (sunrise_time + datetime.timedelta(hours=1)).time():
                        continue
                    global_action('off', True)
                elif self.switch_room:
                    if self.switch_room not in ROOMS:
                        logger.error('Received %s from switch_room, which is not in %s', self.switch_room, ', '.join(ROOMS))
                        continue
                    kwargs = {'force':True} if self.switch_action == 'autoset' else {}
                    getattr(ROOMS[self.switch_room], self.switch_action)(**kwargs)
                    
                elif self.http_res is not None:
                    logger.info('http')
                    if self.http_res['eventType'] == 'manual':
                        global_writeState(self.http_res["action"])
                        continue
                    elif self.http_res['eventType'] == 'dashboard-action':
                        if self.http_res['action'] not in bulbCommands:
                            logger.error('Received %s as a command, which is not a valid command!' % self.http_res['action'])
                            continue
                        if self.http_res['action'] == 'autoset':
                            self.http_res['kwargs']['force'] = True
                        if self.http_res['room'] == 'global':
                            logger.info('global http')
                            global_action(self.http_res['action'], **self.http_res['kwargs'])
                        else:
                            logger.info('Room level http')
                            getattr(ROOMS[self.http_res['room']], self.http_res['action'])(**self.http_res['kwargs'])
                    elif self.http_res['eventType'] == 'dashboard-query':
                        logger.info('dashboard-query')
                        if self.http_res['query'] == 'getProperty':
                            logger.info('getProperty')
                            tmp_bulbs = ROOMS[self.http_res['room']].bulbs
                            if tmp_bulbs:
                                self.http_pipe.send(tmp_bulbs[0].get_properties([self.http_res['properties']]))
                else:
                    logger.info('Timer wake')
                    global_action('autoset', AUTOSET_DURATION if self.timer_wake else 300, autoset_auto_var=not self.timer_wake)
    
                if (systemStartTime + datetime.timedelta(days=3)) < datetime.datetime.utcnow():
                    systemStartTime = datetime.datetime.utcnow()
                    set_IRL_sunset()
            except Exception:
                logger.exception("Exception in server run loop!")
                rebuild_bulbs()


def run_server():
    server = Server()
    server.run()

def global_writeState(newState):
    for room in ROOMS.values():
        room.writeState(newState)
        
def global_action(action, *args, **kwargs):
    if action not in bulbCommands:
        logger.error('%s is not a valid global action!', action)
        return
    for room in ROOMS.values():
        getattr(room, action)(*args, **kwargs)
        
        
def sunrise():
    """
    Simulate a sunrise.
    :return:
    """
    # Prevent autoset from taking over
    writeManualOverride()
    
    # Write the new state
    global_writeState('day')
    
    bulbLog.info('Sunrise start')
    overallDuration = 1200000  # 1200000 == 20 min
    global_action('on')
    
    for i in bulbs:
        i.set_brightness(0)
        i.set_rgb(255, 0, 0)
    time.sleep(1)
    
    transitions = [yeelight.HSVTransition(hue=39, saturation=100,
                                          duration=overallDuration * 0.5, brightness=80),
                   yeelight.TemperatureTransition(degrees=3200,
                                                  duration=overallDuration * 0.5, brightness=80)]
    
    for i in bulbs:
        i.start_flow(yeelight.Flow(count=1, action=yeelight.Flow.actions.stay, transitions=transitions))
        
        


if __name__ == "__main__":
    # Run the system tray app
    # run the python script
    main()
