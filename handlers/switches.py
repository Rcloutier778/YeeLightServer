from yeelightLib import *
import atexit
import datetime
import json
import multiprocessing as mp
import signal
import subprocess
import sys
import time


logger = getLogger()

CODES = {
        'aeb8798': ['Bedroom','toggle'],
        'aeb82f8': ['Bedroom','day'],
        'aebaeb8': ['Bedroom','autoset'],
        'aeb7678': ['Bedroom','sleep'],
        'aeb7e68': ['Bedroom','sleep'], # I'm too lazy to reprogram a remote just for one key. 
        'd08b478': ['Den','toggle'], # 5278535
        'd08bab8': ['Den','day'],   #5278635
        'd08c0f8': ['Den','autoset'], #5278735
        'd08c738': ['Den','sleep'], #5278835
        '50d5458': ['LivingRoom','toggle'], #-84759640
        '50d47f8': ['LivingRoom','day'],
        '50d55d8': ['LivingRoom','autoset'],
        '50d57f8': ['LivingRoom','sleep'],
        }

def monitor_switches(event, cond, pipe):
    '''
    Monitors 433MHz radio for switch event
    '''
    setprocname('Switches')
    logger.info('Monitoring switches')
    lastStartTime = time.time()
    def start_rtl_433():
        nonlocal lastStartTime
        lastStartTime = time.time()
        # r=12600
        return subprocess.Popen(['rtl_433','-R','0', '-X', 'n=switch,m=OOK_PWM,s=464,l=1404,r=1800,g=1800,bits=25,unique', '-F','json', '-M', 'time:utc'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, universal_newlines=True, bufsize=1)

    proc = start_rtl_433()

    def cleanup(*args, **kwargs):
        logger.info('Cleaning up switches')
        proc.kill()
        sys.exit(0)
    lastTime = datetime.datetime.utcnow()
    ackRecvTime= datetime.datetime.utcnow()
    signal.signal(signal.SIGTERM, cleanup)
    atexit.register(cleanup)

    ldata = None

    logger.info('Entering switch loop')

    noneResCount = 0

    def sendMessage(room, action):
        nonlocal pipe
        nonlocal event
        nonlocal cond
        nonlocal ackRecvTime
        logger.info('Switch in %s hit %s', room, action)
        pipe.send([room, action])
        event.set()
        with cond:
            cond.notify()
        while not pipe.poll(2):
            logger.error('Switches did not receive an ack response!')
            pipe.send(['', None])
        # Clear the ack response
        pipe.recv()
        ackRecvTime = datetime.datetime.utcnow()


    def restart_proc():
        nonlocal lastStartTime
        logger.info('Restarting rtl_433 subprocess')
        rc = proc.poll()
        logger.info('rtl_433 has a return code of %s', str(rc))
        proc.kill()
        if time.time() - lastStartTime < 10:
            logger.error("Restarted rtl_433 too quickly!")
            sendMessage(SWITCH_RESTART_KEYWORD, SWITCH_RESTART_KEYWORD)
        lastStartTime = time.time()
        proc.communicate(timeout=1)
        time.sleep(5)

        return start_rtl_433()


    while True:
        try:
            res = proc.stdout.readline()
            # TODO
            logger.info(res)
            if not res:
                noneResCount += 1
                if noneResCount >= 10:
                    logger.warn("Got None as rtl_433 output %d times!", noneResCount)
                if noneResCount >= 100:
                    noneResCount = 0
                    restart_proc()
                continue
            noneResCount = 0
            try:
                res = json.loads(res)
            except Exception: #json.decoder.JSONDecodeError:
                logger.exception('Exception when loading json in monitor switches')
                logger.info(res)
                proc = restart_proc()
                continue
            try:
                if res.get('model') != 'switch' or res.get('len') != 25 or res.get('data') is None:
                    #logger.info(res)
                    continue
                assert res.get('data') in CODES, '%s is not a valid code' % res.get('data', 'None')

                # Skip repeats
                dt = datetime.datetime.strptime(res['time'], '%Y-%m-%d %H:%M:%S')
                if dt - lastTime < datetime.timedelta(seconds=3) and ldata == res['data']:
                    logger.warn('Switch in %s hit %s too close to last previous hit, skipping', *CODES[res['data']])
                    continue
                elif (dt - ackRecvTime).total_seconds() <= 0:
                    continue
                else:
                    logger.info(res)
                ldata = res['data']
                lastTime = dt
                room, action = CODES[res['data']]
                sendMessage(room, action)

            except Exception:
                logger.exception('Exception when evaluating rtl_433 output in monitor switches')


        except Exception:
            logger.exception('Exception in switch loop')
            proc = restart_proc()


