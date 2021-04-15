import datetime
import json
import logging
import multiprocessing as mp
import os
import pickle
import sys
import time

import yeelight
import yeelight.enums
import yeelight.transitions


HOMEDIR = __file__.rsplit(os.sep, 1)[0] + os.sep
os.chdir(HOMEDIR)

formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

logger = logging.getLogger('log')
logger.setLevel(logging.INFO)
fh = logging.FileHandler(HOMEDIR + 'log.log', 'a+')
fh.setLevel(logging.INFO)
fh.setFormatter(formatter)
logger.addHandler(fh)

bulbLog = logging.getLogger('bulbLog')
bulbLog.setLevel(logging.DEBUG)
fh = logging.FileHandler(HOMEDIR + 'bulbLog.log', 'a+')
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)
bulbLog.addHandler(fh)



# logging.basicConfig(filename=HOMEDIR + 'log.log',
#                    filemode='a+',
#                    format='%(asctime)s %(levelname)s %(message)s',
#                    datefmt='%Y-%m-%d %I:%M:%S%p',
#                    level=logging.INFO)

richard_bulb_ips = ["10.0.0.5", "10.0.0.10", "10.0.0.15"]
bulbs = []

phoneIP = None
pcIP = None

user = None

phoneStatus = True
pcStatus = True

commands = ['dusk', 'day', 'night', 'sleep', 'off', 'on', 'toggle', 'sunrise', 'autoset', 'autoset_auto', 'run_server']
allcommands = commands + ['bright', 'brightness', 'rgb']

__DAY_COLOR = 4000
__DUSK_COLOR = 3300
__NIGHT_COLOR = 2500
__SLEEP_COLOR = 1500
__SUNRISE_TIME = '6:50:AM'
__WEEKEND_SUNRISE_TIME = '8:00:AM'
__SUNSET_TIME = '5:30:PM'
__SLEEP_TIME = '10:30:PM'

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
    global phoneIP
    global pcIP
    global user
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
        usr = sys.argv[2].lower()
        
        if cmd in allcommands:
            if cmd in commands:
                if 'autoset' not in cmd:
                    logger.info(cmd)
                
                if usr == 'richard':
                    user = 'richard'
                    bulbs = []
                    for blb in richard_bulb_ips:
                        bulbs.append(yeelight.Bulb(blb))
                    phoneIP = "10.0.0.7"
                    pcIP = "10.0.0.2"
                else:  # make this elif then throw error w/ else
                    # TODO Add vlad lights
                    # bulbs=[vlad lights]
                    # phoneIP=
                    # pcIP=
                    return
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

        
def monitor_advert_bulbs(event, cond):
    """
    Monitors Yeelights default multicast host and port
    Yeelight bulbs will advertise their presence on startup and every 60 minutes afterwardsd
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
                logger.info("Dynamic bulb")
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
        resetFromLoggedState()
        self.bulb_event = mp.Event()
        self.wake_condition = mp.Condition()
        self.TIMEOUT_INTERVAL = 5*60 # 5 min
        self.monitor_bulb_advert_proc = mp.Process(target=monitor_advert_bulbs, args=(self.bulb_event, self.wake_condition,))
        self.monitor_bulb_static_proc = mp.Process(target=monitor_bulb_static, args=(self.bulb_event,self.wake_condition,))

        self.ping_event = mp.Event()
        self.ping_pipe, child_pipe = mp.Pipe()
        self.check_ping_proc = mp.Process(target=checkPingThreaded, args=(self.ping_event, child_pipe, self.wake_condition,))
        self.ping_res = True
        self.timer_wake = False

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
            self.timer_wake=False
            global phoneStatus
            global pcStatus
            phoneStatus, pcStatus, self.ping_res = self.ping_pipe.recv()
            self.ping_event.clear()
    
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
            self.timer_wake=True
            with self.wake_condition:
                self.wake_condition.wait_for(self.wake_predicate, self.TIMEOUT_INTERVAL)
                if self.wake_predicate():
                    self.resolve_wake()
            logger.info("Woke up")
            if not self.ping_res:
                off(True)
            else:
                on()
                autoset(AUTOSET_DURATION if self.timer_wake else 300, autoset_auto_var=not self.timer_wake)

            if (systemStartTime + datetime.timedelta(days=3)) < datetime.datetime.utcnow():
                systemStartTime = datetime.datetime.utcnow()
                set_IRL_sunset()



def run_server():
    server = Server()
    server.run()



def autoset_auto():
    """
    Acts as a server, so should never exit.
    :return:
    """
    logger.error("Boot autoset_auto")
    try:
        set_IRL_sunset()
    except Exception as e:
        logger.error(e, exc_info=True)
        return
    # Below added to fix bug where this program would crash and burn upon phone reappearing
    # on()
    # autoset(autosetDuration=300)
    resetFromLoggedState()
    logger.info(__SUNSET_TIME)
    # End hack
    try:
        autoset(1, autoset_auto_var=True)
    except Exception as e:
        logger.error(e, exc_info=True)
    
    systemStartTime = datetime.datetime.utcnow()
    while True:
        try:
            # noinspection PyPep8Naming
            phoneFound = checkPing()
            logger.info(__SUNSET_TIME)
            if not phoneFound:  # Was 1, now 0
                logger.info("Autoset_auto off")
                off(True)
            else:  # Was 0, now 1
                while True:
                    logger.info("Autoset_auto on")
                    rebuild_bulbs()
                    try:
                        on()
                        logger.info("After on")
                        autoset(autosetDuration=300, autoset_auto_var=True)
                        break
                    except Exception as e:
                        logger.error(e, exc_info=True)
        except Exception as e:
            logger.error(e, exc_info=True)
        finally:
            if (systemStartTime + datetime.timedelta(days=3)) < datetime.datetime.utcnow():
                systemStartTime = datetime.datetime.utcnow()
                set_IRL_sunset()


def resetFromLoggedState():
    """
    Crash recovery. Reset light and color values from their last saved state.
    :return:
    """
    global phoneStatus
    global pcStatus
    
    jdict = getLastState()
    state = jdict['state']
    phoneStatus = jdict['phoneStatus']
    pcStatus = jdict['pcStatus']
    
    if state == 'day':
        day()
    elif state == 'dusk':
        dusk()
    elif state == 'night':
        night()
    elif state == 'sleep':
        sleep()
    elif state == 'off':
        off()
    elif state == 'on':
        on()
    elif state == 'color':
        pass  # Color is being manually manipulated, don't touch
    elif 'custom:' in state:
        temperature, brightness = state.split(':')[1:]
        customTempFlow(int(temperature), brightness=int(brightness))

def checkPingThreaded(event, pipe, cond):
    """
    Threaded runner of checkPing
    :param event:
    :param pipe:
    :return:
    """
    global phoneStatus
    global pcStatus
    resetFromLoggedState()
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


def writeManualOverride():
    with open(os.getcwd() + '/' + user + '_manualOverride.txt', 'w+') as f:
        f.write(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))


def sunrise():
    """
    Simulate a sunrise.
    :return:
    """
    # Prevent autoset from taking over
    writeManualOverride()
    
    # Write the new state
    writeState('day')
    
    bulbLog.info('Sunrise start')
    overallDuration = 1200000  # 1200000 == 20 min
    on()

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


def brightness(val):
    bulbLog.info('Brightness = %d', val)
    for i in bulbs:
        i.set_brightness(val)


def day(duration=3000, auto=False):
    writeState('day')
    bulbLog.info('Day')
    if not auto:
        on()
    # 3200
    colorTempFlow(__DAY_COLOR, duration, 80)


def dusk(duration=3000, auto=False):
    writeState('dusk')
    bulbLog.info('Dusk')
    if not auto:
        on()
    # 3000
    colorTempFlow(__DUSK_COLOR, duration, 80)


def night(duration=3000, auto=False):
    writeState('night')
    bulbLog.info('Night')
    if not auto:
        on()
    colorTempFlow(__NIGHT_COLOR, duration, 80)


def sleep(duration=3000, auto=False):
    writeState('sleep')
    bulbLog.info('Sleep')
    if not auto:
        on()
    colorTempFlow(__SLEEP_COLOR, duration, 20)


def customTempFlow(temperature, duration=3000, auto=False, brightness=80):
    writeState('custom:%d:%d' % (temperature, brightness,))
    bulbLog.info('Custom:%d:%d', temperature, brightness)
    if not auto:
        on()
    colorTempFlow(temperature, duration, brightness)


def off(auto=False):
    if auto:
        # Check if system tray has been used recently to override autoset
        with open(os.getcwd() + '/' + user + '_manualOverride.txt', 'r') as f:
            ld = f.read().strip()
        if datetime.datetime.strptime(ld, '%Y-%m-%d %H:%M:%S') + datetime.timedelta(
                hours=1) > datetime.datetime.utcnow():
            bulbLog.info("SystemTray used recently, canceling autoset")
            return -1
        logger.info('autoset_auto off')
    
    writeState('off')
    bulbLog.info('off')
    while True:
        for i in [x for x in bulbs if x.get_properties()['power'] == 'on']:
            i.turn_off()
        # time.sleep(0.2)
        if all(x.get_properties()['power'] == 'off' for x in bulbs):
            break


def on():
    writeState('on')
    bulbLog.info('on')
    while True:
        for i in [x for x in bulbs if x.get_properties()['power'] == 'off']:
            i.turn_on()
        # time.sleep(0.2)
        if all(x.get_properties()['power'] == 'on' for x in bulbs):
            break


def toggle():
    """
    Doesn't use the built in toggle command in yeelight as it sometimes fails to toggle one of the lights.
    """
    oldPower = bulbs[0].get_properties()['power']
    if oldPower == 'off':
        on()
    else:
        off()


def colorTempFlow(temperature=3200, duration=3000, brightness=80):
    # control all lights at once
    # makes things look more condensed
    transition = yeelight.TemperatureTransition(degrees=temperature, duration=duration, brightness=brightness)
    for i in bulbs:
        i.start_flow(yeelight.Flow(count=1,
                                   action=yeelight.Flow.actions.stay,
                                   transitions=[transition]))


def writeState(newState):
    with open(HOMEDIR + 'bulbStateLog', 'r+') as f:
        jdict = json.load(f)
        if not (newState == jdict['state'] \
                and pcStatus == jdict['pcStatus'] \
                and phoneStatus == jdict['phoneStatus']):
            jdict = {'state': newState, 'pcStatus': pcStatus, 'phoneStatus': phoneStatus}
            f.seek(0)
            json.dump(jdict, f)
            f.truncate()


def getLastState():
    validStates = ['day', 'dusk', 'night', 'off', 'sleep', 'on', 'color']
    with open(HOMEDIR + 'bulbStateLog', 'r') as f:
        jdict = json.load(f)
        if jdict['state'] not in validStates and 'custom:' not in jdict['state']:
            jdict['state'] = 'off'
    return jdict



def discoverBulbs():
    foundBulbs = yeelight.discover_bulbs()
    for bulb in foundBulbs:
        logger.info(bulb)


def autoset(autosetDuration=AUTOSET_DURATION, autoset_auto_var=False):
    if all(x.get_properties()['power'] == 'off' for x in bulbs):
        logger.info('Power is off, cancelling autoset')
        return -1
    
    # If what called autoset is not a checkping event
    if not autoset_auto_var:
        # Check if system tray has been used recently to override autoset
        with open(os.getcwd() + '/' + user + '_manualOverride.txt', 'r') as f:
            ld = f.read().strip()
        if datetime.datetime.strptime(ld, '%Y-%m-%d %H:%M:%S') + datetime.timedelta(
                hours=1) > datetime.datetime.utcnow():
            logger.info("SystemTray used recently, canceling autoset")
            return -1
    getCalcTimes()
    
    # set light level when computer is woken up, based on time of day
    rn = datetime.datetime.now()  # If there is ever a problem here, just use time.localtime()
    now = datetime.time(rn.hour, rn.minute, 0)
    
    # logger.info(['autoset: ',now])
    dayrange = [__SUNRISE_TIME, __SUNSET_TIME]
    if time.localtime().tm_wday in [5, 6]:  # weekend
        dayrange[0] = __WEEKEND_SUNRISE_TIME
    
    nightrange = [dayrange[1], __SLEEP_TIME]
    DNDrange = [nightrange[1], dayrange[0]]
    
    autosetNightRange = getNightRange()
    
    timeranges = [dayrange, nightrange, DNDrange]
    for r in timeranges:
        for rr in range(0, 2):
            t = datetime.datetime.strptime(r[rr], "%I:%M:%p")
            r[rr] = datetime.time(t.hour, t.minute, 0)
    if dayrange[0] <= now < dayrange[1]:
        logger.info("Autoset: Day")
        day(autosetDuration, True)
    elif nightrange[0] <= now and now < nightrange[1]:
        for (startTime, endTime, temperature, brightness) in autosetNightRange:
            if startTime <= now and now < endTime:
                logger.info("Autoset: temperature: %d brightness %d" % (temperature, brightness))
                customTempFlow(temperature, duration=autosetDuration, auto=True, brightness=brightness)
                return 0
        else:
            logger.warn("Didn't find applicable range!!!")
            logger.warn(dayrange)
            logger.warn(nightrange)
            logger.warn(DNDrange)
            logger.warn(now)
            logger.warn(__SUNSET_TIME)
            logger.warn(__SLEEP_TIME)
    elif DNDrange[0] <= now or now < DNDrange[1]:
        logger.info("Autoset: dnd")
        off()
    return 0


def getNightRange():
    with open(HOMEDIR + 'nightTimeRange.pickle', 'rb') as f:
        nightTimeRange = pickle.load(f)
    return nightTimeRange


def getCalcTimes():
    global __SUNSET_TIME
    global __SLEEP_TIME
    with open(HOMEDIR + 'calcTimes.pickle', 'rb') as f:
        calcTimes = pickle.load(f)
        __SUNSET_TIME = calcTimes['sunsetTime']


def set_IRL_sunset():
    global __SUNSET_TIME
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
    __SUNSET_TIME = origDict['sunset'].strftime("%I:%M:%p")
    
    returnRange = []
    iters = 40  # number of iters to calc on
    tempDiff = __DUSK_COLOR - __SLEEP_COLOR  # temp difference between sunset and sleep
    brightnessChangePoint = __DUSK_COLOR - (3 * tempDiff // 4)  # when to start changing brightness
    timeDiff = (datetime.datetime.combine(datetime.date.today(), (
                datetime.datetime.strptime(__SLEEP_TIME, "%I:%M:%p") - datetime.timedelta(
            hours=1)).time()) - datetime.datetime.combine(datetime.date.today(),
                                                          datetime.datetime.strptime(__SUNSET_TIME,
                                                                                     "%I:%M:%p").time())).total_seconds() // 60  # minutes between AFTER sunset and sleep
    
    logger.info('__SUNSET_TIME: %s' % __SUNSET_TIME)
    logger.info('__SLEEP_TIME: %s' % __SLEEP_TIME)
    logger.info('civil_twilight_end: %s' % origDict['civil_twilight_end'].strftime('%I:%M:%p'))
    
    logger.info('timeDiff: %s' % (timeDiff))
    
    brightnessDecreaseIterNum = 0  # None #The iteration where the brightness starts decreasing.
    for i in range(iters):
        brightness = 80
        startTime = origDict['sunset'] + datetime.timedelta(minutes=timeDiff * i // iters)
        endTime = startTime + datetime.timedelta(minutes=1 + (timeDiff // iters))
        temperature = __DUSK_COLOR - int(tempDiff * i // iters)
        # logger.info([iters,i,brightnessDecreaseIterNum, iters - i , iters-brightnessDecreaseIterNum])
        if startTime >= origDict['nautical_twilight_end']:  # temperature < brightnessChangePoint:
            if not brightnessDecreaseIterNum:
                brightnessDecreaseIterNum = i
            brightness = int(80 * ((iters - i) / (iters - brightnessDecreaseIterNum)))
        returnRange.append([startTime.time(), endTime.time(), temperature, brightness])
    for startTime, endTime, temp, brightness in returnRange:
        logger.info(
            '%s, %s, %d, %d' % (startTime.strftime('%I:%M:%S %p'), endTime.strftime('%I:%M:%S %p'), temp, brightness))
    with open(HOMEDIR + 'nightTimeRange.pickle', 'wb+') as f:
        pickle.dump(returnRange, f)
    with open(HOMEDIR + 'calcTimes.pickle', 'wb+') as f:
        pickle.dump({'sunsetTime': __SUNSET_TIME}, f)


if __name__ == "__main__":
    # Run the system tray app
    # run the python script
    main()
