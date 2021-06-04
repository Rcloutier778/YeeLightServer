import datetime
from functools import wraps
import logging
import os
import pickle
import time
from logging.handlers import RotatingFileHandler

HOMEDIR = os.path.dirname(os.path.abspath(__file__))
ROOM_STATES_DIR = os.path.join(HOMEDIR, 'roomStates')
ROOM_DIR = os.path.join(ROOM_STATES_DIR, '{room}')

BULB_IPS = ["10.0.0.5", "10.0.0.10", "10.0.0.15"]
room_to_ips = {'LivingRoom': ["10.0.0.5", "10.0.0.10" ], 'Bedroom':["10.0.0.15"] }
phoneIP = "10.0.0.7"
pcIP = "10.0.0.2"

MANUAL_OVERRIDE_PATH = os.path.join(ROOM_DIR, 'manualOverride.txt')

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

pcStatus = True
phoneStatus = True

formatter = logging.Formatter('%(asctime)s [pid %(process)d] %(levelname)s %(message)s')

actualLoggers = {}


try:
    from setproctitle import setproctitle as setprocname
except ImportError:
    def setprocname(name):
        return

def getLogger(quiet=False):
    global actualLoggers
    logpath = os.path.join(HOMEDIR, 'log.log')
    if actualLoggers.get('log'):
        logger = actualLoggers.get('log')
        if not quiet:
            logger.info('Logging to %s', logpath)
        return logger
    
    logger = logging.getLogger('log')
    logger.setLevel(logging.INFO)
    fh = RotatingFileHandler(logpath, maxBytes=1024*1024*5, mode='a', backupCount=2, delay=0)
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    actualLoggers['log'] = logger
    logger.info('Logging to %s', logpath)
    return logger


def getBulbLogger():
    global actualLoggers
    if actualLoggers.get('bulbLog'):
        return actualLoggers.get('bulbLog')
    bulbLog = logging.getLogger('bulbLog')
    bulbLog.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(os.path.join(HOMEDIR, 'bulbLog.log'), maxBytes=1024, delay=0, mode='a')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    bulbLog.addHandler(fh)
    actualLoggers['bulbLog'] = bulbLog
    return bulbLog

def getCalcTimes():
    #NOTE:
    # If you're going to use this, make sure to add 'from yeelightLib import SUNSET_TIME' right above it
    #   The from * import won't work!!!
    global SUNSET_TIME
    with open(os.path.join(HOMEDIR, 'calcTimes.pickle'), 'rb') as f:
        calcTimes = pickle.load(f)
        SUNSET_TIME = calcTimes['sunsetTime']
        return calcTimes

def getNightRange():
    with open(os.path.join(HOMEDIR, 'nightTimeRange.pickle'), 'rb') as f:
        nightTimeRange = pickle.load(f)
    return nightTimeRange

def writeManualOverride(room=None, offset=None):
    offset = offset if isinstance(offset, datetime.timedelta) else datetime.timedelta(hours=0)
    t=datetime.datetime.utcnow() + offset
    assert room is None or room in room_to_ips
    rooms = [room] if room is not None else list(room_to_ips.keys())
    for rm in rooms:
        with open(MANUAL_OVERRIDE_PATH.format(room=rm), 'w+') as f:
            f.write(t.strftime('%Y-%m-%d %H:%M:%S'))

def readManualOverride(room=None):
    room = room or list(room_to_ips.keys())[0]
    if os.path.exists(MANUAL_OVERRIDE_PATH.format(room=room)):
        with open(MANUAL_OVERRIDE_PATH.format(room=room), 'r') as f:
            return datetime.datetime.strptime(f.read().strip(), '%Y-%m-%d %H:%M:%S')
    else:
        fake_date = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        with open(MANUAL_OVERRIDE_PATH.format(room=room), 'w+') as f:
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
        if startTime >= origDict['civil_twilight_end']:  # temperature < brightnessChangePoint:
            if not brightnessDecreaseIterNum:
                brightnessDecreaseIterNum = i
            brightness = max(20, int(80 * ((iters - i) / (iters - brightnessDecreaseIterNum))))
        returnRange.append([startTime.time(), endTime.time(), temperature, brightness])
    returnRange.append([returnRange[-1][0], datetime.datetime.strptime("10:30:PM","%I:%M:%p").time(), SLEEP_COLOR, SLEEP_BRIGHTNESS])
    for startTime, endTime, temp, brightness in returnRange:
        logger.info(
            '%s, %s, %d, %d' % (startTime.strftime('%I:%M:%S %p'), endTime.strftime('%I:%M:%S %p'), temp, brightness))
    with open(os.path.join(HOMEDIR, 'nightTimeRange.pickle'), 'wb+') as f:
        pickle.dump(returnRange, f)
    with open(os.path.join(HOMEDIR, 'calcTimes.pickle'), 'wb+') as f:
        pickle.dump({'sunsetTime': SUNSET_TIME}, f)

#class Bulb(yeelight.Bulb):
#    def __init__(self, *args, **kwargs):
#        super(yeelight.Bulb, self).__init__(*args, **kwargs)

def retry(orig_func=None, max_attempts=3):
    def _decorate(func):
        @wraps(func)
        def retry_wrapper(*args, **kwargs):
            logger = getLogger(quiet=True)
            e = None
            for attemptNum in range(max_attempts):
                try:
                    res = func(*args, **kwargs)
                    break
                except Exception as exc:
                    e = exc
                    logger.warn('Failed to execute %s on try %d\n%s', func.__name__, attemptNum+1, ' '.join(exc.args))
                    time.sleep(1)
            else:
                raise(e)
            return res
        return retry_wrapper
    if orig_func:
        return _decorate(orig_func)

    return _decorate


