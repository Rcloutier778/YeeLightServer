import datetime
import logging
import os
import pickle
from logging.handlers import RotatingFileHandler

HOMEDIR = os.path.dirname(os.path.abspath(__file__))
ROOM_STATES_DIR = os.path.join(HOMEDIR, 'roomStates')
BULB_IPS = ["10.0.0.5", "10.0.0.10", "10.0.0.15"]
room_to_ips = {'Bedroom': ["10.0.0.5", "10.0.0.10", "10.0.0.15"]}
phoneIP = "10.0.0.7"
pcIP = "10.0.0.2"

MANUAL_OVERRIDE_PATH = os.path.join(os.getcwd(), 'manualOverride.txt')

bulbCommands = ['dusk', 'day', 'night', 'sleep', 'off', 'on', 'toggle', 'sunrise', 'autoset', 'rgb']

commands = bulbCommands + ['run_server']
allcommands = commands + ['bright', 'brightness']

DAY_COLOR = 4000
DUSK_COLOR = 3300
NIGHT_COLOR = 2500
SLEEP_COLOR = 1500
SUNRISE_TIME = '6:50:AM'
WEEKEND_SUNRISE_TIME = '8:00:AM'
SUNSET_TIME = '5:30:PM'
SLEEP_TIME = '10:30:PM'
DAY_BRIGHTNESS = 80
DUSK_BRIGHTNESS = 80
NIGHT_BRIGHTNESS = 80
SLEEP_BRIGHTNESS = 20

PREDEF_STATES = {'day': [DAY_COLOR, DAY_BRIGHTNESS],
                 'dusk': [DUSK_COLOR, DUSK_BRIGHTNESS],
                 'night': [NIGHT_COLOR, NIGHT_BRIGHTNESS],
                 'sleep': [SLEEP_COLOR, SLEEP_BRIGHTNESS]}


AUTOSET_DURATION = 300000

# Legacy server, probably will be decommed at some point
LEGACY_SERVER_PORT_NUMBER = 9000

# REST API server port
REST_SERVER_PORT_NUMBER = 9001

# JS file host port
JS_SERVER_PORT = 9002




formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

actualLoggers = {}


try:
    from setproctitle import setproctitle as setprocname
except ImportError:
    def setprocname(name):
        return

def getLogger():
    global actualLoggers
    if actualLoggers.get('log'):
        return actualLoggers.get('log')
    
    
    
    logger = logging.getLogger('log')
    logger.setLevel(logging.INFO)
    fh = RotatingFileHandler(os.path.join(HOMEDIR, 'log.log'), mode='a+', maxBytes=1024)
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    actualLoggers['log'] = logger
    return logger


def getBulbLogger():
    global actualLoggers
    if actualLoggers.get('bulbLog'):
        return actualLoggers.get('bulbLog')
    bulbLog = logging.getLogger('bulbLog')
    bulbLog.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(os.path.join(HOMEDIR, 'bulbLog.log'), mode='a+', maxBytes=1024)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    bulbLog.addHandler(fh)
    actualLoggers['bulbLog'] = bulbLog
    return bulbLog

def getCalcTimes():
    global SUNSET_TIME
    global SLEEP_TIME
    with open(os.path.join(HOMEDIR, 'calcTimes.pickle'), 'rb') as f:
        calcTimes = pickle.load(f)
        SUNSET_TIME = calcTimes['sunsetTime']
        return calcTimes

def getNightRange():
    with open(os.path.join(HOMEDIR, 'nightTimeRange.pickle'), 'rb') as f:
        nightTimeRange = pickle.load(f)
    return nightTimeRange

def writeManualOverride():
    with open(MANUAL_OVERRIDE_PATH, 'w+') as f:
        f.write(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))

def readManualOverride():
    if os.path.exists(MANUAL_OVERRIDE_PATH):
        with open(MANUAL_OVERRIDE_PATH, 'r') as f:
            return datetime.datetime.strptime(f.read().strip(), '%Y-%m-%d %H:%M:%S')
    else:
        fake_date = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        with open(MANUAL_OVERRIDE_PATH, 'w+') as f:
            f.write(fake_date.strftime('%Y-%m-%d %H:%M:%S'))
        return fake_date


def set_IRL_sunset():
    global SUNSET_TIME
    import re
    import requests
    import json
    import pytz
    import datetime
    logger=getLogger()
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
        
        
