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
        '61e28a': ['Bedroom','toggle'],
        '0be28a': ['Bedroom','day'],
        '28a28a': ['Bedroom','autoset'],
        '19128a': ['Bedroom','sleep'],

        }

def monitor_switches(event, cond, pipe):
    '''
    Monitors 433MHz radio for switch event
    '''
    setprocname('Switches')
    logger.info('Monitoring switches')

    p,c = mp.Pipe()
    proc = subprocess.Popen(['rtl_433','-R','86','-F','json'], stdout=subprocess.PIPE, text=True, universal_newlines=True, bufsize=1)

    def cleanup(*args, **kwargs):
        proc.kill()
        p.close()
        c.close()
        sys.exit(0)
    lastTime = datetime.datetime.now()
    signal.signal(signal.SIGTERM, cleanup)
    atexit.register(cleanup)

    while True:
        try:
            #res = p.recv()
            res = proc.stdout.readline()
            try:
                res = json.loads(res)
                logger.info(res)
                if res.get('model') != 'Smoke-GS558':
                    continue
                assert res.get('code') in CODES, '%s is not a valid code' % res.get('code','None')

                # Skip repeats
                dt = datetime.datetime.strptime(res['time'], '%Y-%m-%d %H:%M:%S')
                if dt - lastTime < datetime.timedelta(seconds=5):
                    continue
                lastTime = dt
                room, action = CODES[res['code']]
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







#TODO
# testing
if __name__ == '__main__':
    monitor_switches(None, None, None)

