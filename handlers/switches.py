from yeelightLib import *
import atexit
import datetime
import json
import multiprocessing as mp
import signal
import subprocess
import sys



logger = getLogger()

CODES = {
        'aeb8798': ['Bedroom','toggle'],
        'aeb82f8': ['Bedroom','day'],
        'aebaeb8': ['Bedroom','autoset'],
        'aeb7678': ['Bedroom','sleep'],
        '50d5458': ['LivingRoom','toggle'],
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
    p,c = mp.Pipe()
    proc = subprocess.Popen(['rtl_433','-R','0', '-X', 'n=switch,m=OOK_PWM,s=464,l=1404,r=12600,g=1800,bits=25,unique', '-F','json'], stdout=subprocess.PIPE, text=True, universal_newlines=True, bufsize=1)

    def cleanup(*args, **kwargs):
        proc.kill()
        p.close()
        c.close()
        sys.exit(0)
    lastTime = datetime.datetime.now()
    signal.signal(signal.SIGTERM, cleanup)
    atexit.register(cleanup)

    ldata = None

    while True:
        try:
            res = proc.stdout.readline()
            try:
                res = json.loads(res)
                logger.info(res)
                if res.get('model') != 'switch' or res.get('len') != 25 or res.get('data') is None:
                    continue
                assert res.get('data') in CODES, '%s is not a valid code' % res.get('data','None')

                # Skip repeats
                dt = datetime.datetime.strptime(res['time'], '%Y-%m-%d %H:%M:%S')
                if dt - lastTime < datetime.timedelta(seconds=3) and ldata == res['data']:
                    logger.warn('Switch in %s hit %s too close to last previous hit, skipping', *CODES[res['data']])
                    continue
                ldata = res['data']
                lastTime = dt
                room, action = CODES[res['data']]
                logger.info('Switch in %s hit %s', room, action)
                pipe.send([room, action])
                event.set()
                with cond:
                    cond.notify()


            except Exception:
                logger.exception('Exception when parsing json in monitor switches')


        except Exception:
            logger.exception('Exception in switch loop')
            pass


