import datetime
import json
import logging
import multiprocessing as mp
import os
import pickle
import signal
import sys
import time

import yeelight
import yeelight.enums
import yeelight.transitions


HOMEDIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(HOMEDIR)
ROOM_STATES_DIR = os.path.join(HOMEDIR, 'roomStates')

formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

logger = logging.getLogger('log')
logger.setLevel(logging.INFO)
fh = logging.FileHandler(os.path.join(HOMEDIR, 'log.log'), 'a+')
fh.setLevel(logging.INFO)
fh.setFormatter(formatter)
logger.addHandler(fh)

bulbLog = logging.getLogger('bulbLog')
bulbLog.setLevel(logging.DEBUG)
fh = logging.FileHandler(os.path.join(HOMEDIR, 'bulbLog.log'), 'a+')
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)
bulbLog.addHandler(fh)

richard_bulb_ips = ["10.0.0.5", "10.0.0.10", "10.0.0.15"]
bulbs = []

room_to_ips = {'Bedroom': ["10.0.0.5", "10.0.0.10", "10.0.0.15"],
         'LivingRoom': []} #TODO

ROOMS = {}

phoneIP = None
pcIP = None

phoneStatus = True
pcStatus = True

MANUAL_OVERRIDE_PATH = os.path.join(os.getcwd(), 'manualOverride.txt')

bulbCommands = ['dusk', 'day', 'night', 'sleep', 'off', 'on', 'toggle', 'sunrise', 'autoset']
commands = bulbCommands + ['run_server']
allcommands = commands + ['bright', 'brightness', 'rgb']

DAY_COLOR = 4000
DUSK_COLOR = 3300
NIGHT_COLOR = 2500
SLEEP_COLOR = 1500
SUNRISE_TIME = '6:50:AM'
WEEKEND_SUNRISE_TIME = '8:00:AM'
SUNSET_TIME = '5:30:PM'
SLEEP_TIME = '10:30:PM'

AUTOSET_DURATION = 300000
# TODO
"""
1) autoset on wakeup from lan
2) cortana integration
3) hourly light temp color updates
4) Brightness slider in system tray
"""


def main():
    # logger.info(desk.get_properties())
    global bulbs
    global ROOMS
    global phoneIP
    global pcIP
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
                assert all(bulb_ips in set( y for x in room_to_ips.values() for y in x) for bulb_ips in richard_bulb_ips)
                for roomName, ips in room_to_ips.items():
                    
                    blbs = []
                    for ip in ips:
                        bulb = yeelight.Bulb(ip)
                        bulbs.append(bulb)
                        blbs.append(bulb)
                    ROOMS[roomName] = Room(roomName, blbs)
                phoneIP = "10.0.0.7"
                pcIP = "10.0.0.2"

                globals()[cmd]()
        elif cmd in ['bright', 'brightness']:
            if type(sys.argv[2]) == int:
                logger.info("Changing brightness to %d" % int(sys.argv[2]))
                for i in bulbs:
                    i.set_brightness(int(sys.argv[1]))
        else:
            logger.info("Command \"%s\" not found" % cmd)


class Room:
    def __init__(self, name, bulbs):
        self.bulbs = bulbs
        assert name in room_to_ips
        self.name = name
        
        self.roomStatePath = os.path.join(ROOM_STATES_DIR, self.name)
        self.state = None


    def translate(self, command):
        " str --> func"
        if command in bulbCommands:
            allcommands
            globals()[command]()

    def getRoomStatePath(self):
        return

    def writeState(self, newState):
        "Write out the state of the bulbs in the room"
        bulbLog.info('%s = %s', self.name, newState)
        self.state = newState
        if not os.path.exists(ROOM_STATES_DIR):
            os.mkdir(ROOM_STATES_DIR)
        
        prev_state_dict = {'state': None, 'pcStatus': None, 'phoneStatus': None}
        if os.path.exists(self.roomStatePath):
            with open(self.roomStatePath, 'r') as f:
                prev_state_dict = json.load(f)
        
        
        if not (newState == prev_state_dict['state'] \
            and pcStatus == prev_state_dict['pcStatus'] \
            and phoneStatus == prev_state_dict['phoneStatus']):
            
            new_state_dict = {'state': newState, 'pcStatus': pcStatus, 'phoneStatus': phoneStatus}
            with open(self.roomStatePath, 'w+') as f:
                json.dump(new_state_dict, f)
                f.truncate()

    def _getLastState(self):
        "Get the last written state of the bulbs in a room"
        validStates = ['day', 'dusk', 'night', 'off', 'sleep', 'on', 'color']
        
        if not os.path.exists(ROOM_STATES_DIR):
            os.mkdir(ROOM_STATES_DIR)
        roomStatePath = os.path.join(ROOM_STATES_DIR, self.name)
        if not os.path.exists(roomStatePath):
            self.writeState('day')
            
        with open(roomStatePath) as f:
            jdict = json.load(f)
            if jdict['state'] not in validStates and 'custom:' not in jdict['state']:
                jdict['state'] = 'off'
        return jdict

    def resetFromLoggedState(self):
        """
        Crash recovery. Reset light and color values from their last saved state.
        :return:
        """
        global phoneStatus
        global pcStatus
    
        jdict = self._getLastState()
        state = jdict['state']
        self.state = state
        phoneStatus = jdict['phoneStatus']
        pcStatus = jdict['pcStatus']
    
        if state == 'day':
            self.day()
        elif state == 'dusk':
            self.dusk()
        elif state == 'night':
            self.night()
        elif state == 'sleep':
            self.sleep()
        elif state == 'off':
            self.off()
        elif state == 'on':
            self.on()
        elif state == 'color':
            pass  # Color is being manually manipulated, don't touch
        elif 'custom:' in state:
            temperature, brightness = state.split(':')[1:]
            self.customTempFlow(int(temperature), brightness=int(brightness))
    
    def graceful_kill(self):
        logger.info('In shutdown')
        self.writeState(self.state)

    def brightness(self, val):
        bulbLog.info('Brightness = %d', val)
        for i in self.bulbs:
            i.set_brightness(val)
    
    
    def day(self, duration=3000, auto=False):
        self.writeState('day')
        if not auto:
            self.on()
        # 3200
        self.colorTempFlow(DAY_COLOR, duration, 80)
    
    
    def dusk(self, duration=3000, auto=False):
        self.writeState('dusk')
        if not auto:
            self.on()
        # 3000
        self.colorTempFlow(DUSK_COLOR, duration, 80)
    
    
    def night(self, duration=3000, auto=False):
        self.writeState('night')
        if not auto:
            self.on()
        self.colorTempFlow(NIGHT_COLOR, duration, 80)
    
    
    def sleep(self, duration=3000, auto=False):
        self.writeState('sleep')
        if not auto:
            self.on()
        self.colorTempFlow(SLEEP_COLOR, duration, 20)
    
    
    def customTempFlow(self, temperature, duration=3000, auto=False, brightness=80):
        self.writeState('custom:%d:%d' % (temperature, brightness,))
        if not auto:
            self.on()
        self.colorTempFlow(temperature, duration, brightness)
    
    
    def off(self, auto=False):
        if auto:
            # Check if system tray has been used recently to override autoset
            ld = readManualOverride()
            if ld + datetime.timedelta(hours=1) > datetime.datetime.utcnow():
                bulbLog.info("SystemTray used recently, canceling autoset")
                return -1
            logger.info('autoset_auto off')
        
        self.writeState('off')
        while True:
            for i in [x for x in self.bulbs if x.get_properties()['power'] == 'on']:
                i.turn_off()
            # time.sleep(0.2)
            if all(x.get_properties()['power'] == 'off' for x in self.bulbs):
                break
    
    
    def on(self):
        self.writeState('on')
        while True:
            for i in [x for x in self.bulbs if x.get_properties()['power'] == 'off']:
                i.turn_on()
            # time.sleep(0.2)
            if all(x.get_properties()['power'] == 'on' for x in self.bulbs):
                break
    
    
    def toggle(self):
        """
        Doesn't use the built in toggle command in yeelight as it sometimes fails to toggle one of the lights.
        """
        oldPower = self.bulbs[0].get_properties()['power']
        if oldPower == 'off':
            self.on()
        else:
            self.off()
    
    
    def colorTempFlow(self, temperature=3200, duration=3000, brightness=80):
        # control all lights at once
        # makes things look more condensed
        transition = yeelight.TemperatureTransition(degrees=temperature, duration=duration, brightness=brightness)
        for i in self.bulbs:
            i.start_flow(yeelight.Flow(count=1,
                                       action=yeelight.Flow.actions.stay,
                                       transitions=[transition]))
    
    
    def autoset(self, autosetDuration=AUTOSET_DURATION, autoset_auto_var=False):
        if all(x.get_properties()['power'] == 'off' for x in self.bulbs):
            logger.info('Power is off, cancelling autoset')
            return -1
        
        # If what called autoset is not a checkping event
        if not autoset_auto_var:
            # Check if system tray has been used recently to override autoset
            ld = readManualOverride()
            if ld + datetime.timedelta(hours=1) > datetime.datetime.utcnow():
                logger.info("SystemTray used recently, canceling autoset")
                return -1
        getCalcTimes()
        
        # set light level when computer is woken up, based on time of day
        rn = datetime.datetime.now()  # If there is ever a problem here, just use time.localtime()
        now = datetime.time(rn.hour, rn.minute, 0)
        
        # logger.info(['autoset: ',now])
        dayrange = [SUNRISE_TIME, SUNSET_TIME]
        if time.localtime().tm_wday in [5, 6]:  # weekend
            dayrange[0] = WEEKEND_SUNRISE_TIME
        
        nightrange = [dayrange[1], SLEEP_TIME]
        DNDrange = [nightrange[1], dayrange[0]]
        
        autosetNightRange = getNightRange()
        
        timeranges = [dayrange, nightrange, DNDrange]
        for r in timeranges:
            for rr in range(0, 2):
                t = datetime.datetime.strptime(r[rr], "%I:%M:%p")
                r[rr] = datetime.time(t.hour, t.minute, 0)
        if dayrange[0] <= now < dayrange[1]:
            logger.info("Autoset: Day")
            self.day(autosetDuration, True)
        elif nightrange[0] <= now and now < nightrange[1]:
            for (startTime, endTime, temperature, brightness) in autosetNightRange:
                if startTime <= now and now < endTime:
                    logger.info("Autoset: temperature: %d brightness %d" % (temperature, brightness))
                    self.customTempFlow(temperature, duration=autosetDuration, auto=True, brightness=brightness)
                    return 0
            else:
                logger.warning("Didn't find applicable range!!!")
                logger.warning(dayrange)
                logger.warning(nightrange)
                logger.warning(DNDrange)
                logger.warning(now)
                logger.warning(SUNSET_TIME)
                logger.warning(SLEEP_TIME)
        elif DNDrange[0] <= now or now < DNDrange[1]:
            logger.info("Autoset: dnd")
            self.off()
        return 0

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

def monitor_switches(event, cond, pipe):
    """
    Monitors 433MHz radio for switch event.
    :param event:
    :param cond:
    :return:
    """
    import platform
    
    # This makes use of RPI gpio pins and a 433MHz radio receiver.
    if 'Windows' in platform.platform():
        while True:
            time.sleep(9999)
            
    import atexit
    #import RPi.GPIO as GPIO
    from rpi_rf import RFDevice
    import rtlsdr
    
    class RTLSDR(rtlsdr.RtlSdr):
        def __init__(self):
            super(rtlsdr.RtlSdr, self).__init__()
        def __enter__(self):
            pass
        
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.close()
    
    
    
    
    
    
    
    
    # TODO
    # { Room : [ (pin, action) ] }
    gpio_pins = {'Bedroom': [], 'LivingRoom': []}
    
    class RFSwitch(RFDevice):
        def __init__(self, room, gpio, action):
            assert room in room_to_ips
            super(RFDevice, self).__init__(gpio)
            self.room = room
            self.enable_rx()
            self.lastTimestamp = time.time()
            self.action = action
            
        def check(self):
            if self.rx_code_timestamp != self.lastTimestamp:
                logger.info(self.rx_code)
                tmp_tmstmp = self.lastTimestamp
                self.lastTimestamp = self.rx_code_timestamp
                # Prevent bursts from rapidly toggling lights
                return self.rx_code_timestamp - tmp_tmstmp > 2 #TODO check if seconds or ms


    rf_switches = []
    for room, pins in gpio_pins.items():
        for pin in pins:
            rf_switches.append(RFSwitch(room, pin))

    def cleanup():
        for switch in rf_switches:
            switch.cleanup()
            
    atexit.register(cleanup)
    
    while True:
        for switch in rf_switches:
            if switch.check():
                # TODO dimming
                pipe.send((switch.room, switch.action))
        
                event.set()
                with cond:
                    logger.info("433 MHz received")
                    cond.notify()
        time.sleep(1)

     
def monitor_advert_bulbs(event, cond):
    """
    Monitors Yeelights default multicast host and port
    Yeelight bulbs will advertise their presence on startup and every 60 minutes afterwards
    :param pipe:
    :return:
    """
    import socket
    import struct
    
    # Default yeelight multicast group and port
    MCAST_GRP = '239.255.255.250'
    MCAST_PORT = 1982
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # on this port, receives ALL multicast groups
    sock.bind(('', MCAST_PORT))

    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    
    while True:
        res = sock.recv(10240)
        # ssdb:discover is a hallmark of yeelight.discover_bulbs(), which is what we send in static search.
        if b'ssdp:discover' not in res:
            event.set()
            with cond:
                logger.info("Advertising bulb wake")
                cond.notify()
    
def monitor_bulb_static(event, cond):
    """
    Monitors for bulb connection/disconnections. Mainly for disconnections.
    :param event:
    :return:
    """
    current_bulbs_ips = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs()))
    
    while True:
        found_bulbs_ip = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs(1)))
        if current_bulbs_ips != found_bulbs_ip:
            # Retry 3 times. Sometimes a bulb doesn't respond for whatever reason.
            for _ in range(3):
                tmp_found_bulbs_ip = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs(0.2)))
                if tmp_found_bulbs_ip == current_bulbs_ips:
                    break
            else:
                new_ips = set(found_bulbs_ip) - set(current_bulbs_ips)
                missing_ips = set(current_bulbs_ips) - set(found_bulbs_ip)
                for new_ip in new_ips:
                    logger.info('Found new bulb at ip addr: %s', new_ip)
                for missing_ip in missing_ips:
                    logger.info('Missing bulb at ip addr: %s', missing_ip)
    
                current_bulbs_ips = found_bulbs_ip
                event.set()
                with cond:
                    logger.info("Static bulb")
                    cond.notify()
        time.sleep(60)
        
        
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
        self.check_ping_proc = mp.Process(target=checkPingThreaded, args=(self.ping_event, ping_child_pipe, self.wake_condition,))
        self.ping_res = True
        self.timer_wake = False
        
        self.switch_event = mp.Event()
        self.switch_pipe, switch_child_pipe = mp.Pipe()
        self.monitor_switches_proc = mp.Process(target=monitor_switches, args=(self.switch_event, switch_child_pipe, self.wake_condition,))
        self.switch_res = None
        self.switch_action = None
        
        signal.signal(signal.SIGTERM, self.graceful_shutdown)

    def graceful_shutdown(self):
        """
        Gracefully shut down the server, closing pipes, killing child procs, writing out states.
        :return:
        """
        self.monitor_bulb_static_proc.kill()
        self.monitor_bulb_advert_proc.kill()
        self.check_ping_proc.kill()
        self.monitor_switches_proc.kill()
        self.ping_pipe.close()
        self.switch_pipe.close()
        
        for roomName, room in ROOMS:
            room.graceful_kill()
        sys.exit(0)

    def wake_predicate(self):
        """
        The wake condition for the main thread
        :return:
        """
        return self.ping_event.is_set() or self.bulb_event.is_set()
    
    def resolve_wake(self):
        """
        Resolve whatever event woke the main thread
        :return:
        """
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
            self.switch_res, self.switch_action = self.switch_pipe.recv()
            self.switch_event.clear()
            

    
    def run(self):
        """
        Runs the server
        :return:
        """
        logger.error("Booting server")
        self.monitor_bulb_advert_proc.start()
        self.monitor_bulb_static_proc.start()
        self.check_ping_proc.start()
        
        systemStartTime = datetime.datetime.utcnow()
        while True:
            self.timer_wake = True
            self.switch_res = None
            self.switch_action = None
            with self.wake_condition:
                self.wake_condition.wait_for(self.wake_predicate, self.TIMEOUT_INTERVAL)
                if self.wake_predicate():
                    self.resolve_wake()
            logger.info("Woke up")
            if not self.ping_res:
                global_off(True)
            elif self.switch_res:
                if self.switch_res not in ROOMS:
                    logger.error('Received %s from switch_res, which is not in %s', self.switch_res, ', '.join(ROOMS))
                    continue
                ROOMS[self.switch_res].command(self.switch_action)
                    
            else:
                global_on()
                global_autoset(AUTOSET_DURATION if self.timer_wake else 300, autoset_auto_var=not self.timer_wake)

            if (systemStartTime + datetime.timedelta(days=3)) < datetime.datetime.utcnow():
                systemStartTime = datetime.datetime.utcnow()
                set_IRL_sunset()


def run_server():
    server = Server()
    server.run()



def checkPingThreaded(event, pipe, cond):
    """
    Threaded runner of checkPing
    :param event:
    :param pipe:
    :return:
    """
    global phoneStatus
    global pcStatus
    while True:
        res = checkPing()
        pipe.send([phoneStatus, pcStatus, res])
        event.set()
        with cond:
            cond.notify()


def checkPing():
    """
    Checks if my PC or phone is on the network and if their state has changed.
    :return:
    """
    global phoneStatus
    global pcStatus
    if phoneStatus:
        sleepTime = 7
    else:
        sleepTime = 0.5
    MAX_PHONE_ATTEMPTS = 5
    MAX_PC_ATTEMPTS = 2
    attempts = 0
    while True:
        time.sleep(sleepTime)
        phone_response = not bool(os.system("ping -c 1 -W 2 " + phoneIP))
        pc_response = not bool(os.system("ping -c 1 -W 2 " + pcIP))
        if (phone_response == phoneStatus) and (pc_response == pcStatus):  # no changes
            attempts = 0
            continue
        elif not phone_response:  # phone is missing
            if attempts == MAX_PHONE_ATTEMPTS:  # try until MAX_PHONE_ATTEMPTS is reached
                logger.info("Phone missing")
                pcStatus = pc_response
                phoneStatus = phone_response
                return False
            attempts += 1
            continue
        elif (not phoneStatus) and phone_response:  # phone re-appears
            logger.info("Phone re appeared")
            attempts = 0
            pcStatus = pc_response
            phoneStatus = phone_response
            return True
        elif phone_response and pc_response and not pcStatus:  # if pc turns on
            logger.info('PC turned on')
            pcStatus = pc_response
            phoneStatus = phone_response
            return True
        elif phoneStatus and pcStatus and not pc_response:  # if pc turns off
            if attempts == MAX_PC_ATTEMPTS:
                logger.info("PC turned off")
                pcStatus = pc_response
                phoneStatus = phone_response
                return False
            attempts += 1



def readManualOverride():
    if os.path.exists(MANUAL_OVERRIDE_PATH):
        with open(MANUAL_OVERRIDE_PATH, 'r') as f:
            return datetime.datetime.strptime(f.read().strip(), '%Y-%m-%d %H:%M:%S')
    else:
        fake_date = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        with open(MANUAL_OVERRIDE_PATH, 'w+') as f:
            f.write(fake_date.strftime('%Y-%m-%d %H:%M:%S'))
        return fake_date
        


def writeManualOverride():
    with open(MANUAL_OVERRIDE_PATH, 'w+') as f:
        f.write(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))

def global_on():
    for room in ROOMS.values():
        room.on()
        
def global_off(auto=False):
    for room in ROOMS.values():
        room.on(auto)
        
def global_writeState(newState):
    for room in ROOMS.values():
        room.writeState(newState)
        
def global_autoset(*args, **kwargs):
    for room in ROOMS.values():
        room.autoset(*args, **kwargs)
        


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
    global_on()
    
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
        
        
def getNightRange():
    with open(os.path.join(HOMEDIR, 'nightTimeRange.pickle'), 'rb') as f:
        nightTimeRange = pickle.load(f)
    return nightTimeRange


def getCalcTimes():
    global SUNSET_TIME
    global SLEEP_TIME
    with open(os.path.join(HOMEDIR, 'calcTimes.pickle'), 'rb') as f:
        calcTimes = pickle.load(f)
        SUNSET_TIME = calcTimes['sunsetTime']
        return calcTimes


def set_IRL_sunset():
    global SUNSET_TIME
    import re
    import requests
    import json
    import pytz
    import datetime
    r = requests.post('https://api.sunrise-sunset.org/json?lat=40.739589&lng=-74.035677&formatted=0')
    assert r.status_code == 200
    origDict = json.loads(r.text)['results']
    for key in origDict:
        if not isinstance(origDict[key], str) or not re.match(
                r'\d\d\d\d-\d\d-\d\dT\d\d:\d\d:\d\d\+\d\d:\d\d',
                origDict[key]):
            continue
        ogTime = datetime.datetime.strptime(origDict[key], "%Y-%m-%dT%H:%M:%S%z")
        localTime = ogTime.astimezone(pytz.timezone('US/Eastern'))
        localTime = localTime.replace(tzinfo=None)
        origDict[key] = localTime
        logger.info('%s: %s', key, origDict[key].strftime("%I:%M:%S %p"))
    
    sunsetOffset = datetime.timedelta(minutes=45)
    origDict['sunset'] -= sunsetOffset
    origDict['sunset'] = origDict['sunset'].replace(second=0)
    SUNSET_TIME = origDict['sunset'].strftime("%I:%M:%p")
    
    returnRange = []
    iters = 40  # number of iters to calc on
    tempDiff = DUSK_COLOR - SLEEP_COLOR  # temp difference between sunset and sleep
    brightnessChangePoint = DUSK_COLOR - (3 * tempDiff // 4)  # when to start changing brightness
    timeDiff = (datetime.datetime.combine(datetime.date.today(), (
                datetime.datetime.strptime(SLEEP_TIME, "%I:%M:%p") - datetime.timedelta(
            hours=1)).time()) - datetime.datetime.combine(datetime.date.today(),
                                                          datetime.datetime.strptime(SUNSET_TIME,
                                                                                     "%I:%M:%p").time())).total_seconds() // 60  # minutes between AFTER sunset and sleep
    
    logger.info('SUNSET_TIME: %s' % SUNSET_TIME)
    logger.info('SLEEP_TIME: %s' % SLEEP_TIME)
    logger.info('civil_twilight_end: %s' % origDict['civil_twilight_end'].strftime('%I:%M:%p'))
    
    logger.info('timeDiff: %s' % (timeDiff))
    
    brightnessDecreaseIterNum = 0  # None #The iteration where the brightness starts decreasing.
    for i in range(iters):
        brightness = 80
        startTime = origDict['sunset'] + datetime.timedelta(minutes=timeDiff * i // iters)
        endTime = startTime + datetime.timedelta(minutes=1 + (timeDiff // iters))
        temperature = DUSK_COLOR - int(tempDiff * i // iters)
        # logger.info([iters,i,brightnessDecreaseIterNum, iters - i , iters-brightnessDecreaseIterNum])
        if startTime >= origDict['nautical_twilight_end']:  # temperature < brightnessChangePoint:
            if not brightnessDecreaseIterNum:
                brightnessDecreaseIterNum = i
            brightness = int(80 * ((iters - i) / (iters - brightnessDecreaseIterNum)))
        returnRange.append([startTime.time(), endTime.time(), temperature, brightness])
    for startTime, endTime, temp, brightness in returnRange:
        logger.info(
            '%s, %s, %d, %d' % (startTime.strftime('%I:%M:%S %p'), endTime.strftime('%I:%M:%S %p'), temp, brightness))
    with open(os.path.join(HOMEDIR, 'nightTimeRange.pickle'), 'wb+') as f:
        pickle.dump(returnRange, f)
    with open(os.path.join(HOMEDIR, 'calcTimes.pickle'), 'wb+') as f:
        pickle.dump({'sunsetTime': SUNSET_TIME}, f)


if __name__ == "__main__":
    # Run the system tray app
    # run the python script
    main()
