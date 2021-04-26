import os
import time

from yeelightLib import getLogger, phoneIP, pcIP, setprocname

logger = getLogger()


def checkPingThreaded(event, pipe, cond, pcStatus, phoneStatus):
    """
    Threaded runner of checkPing
    :param event:
    :param pipe:
    :return:
    """
    setprocname('checkPing')
    while True:
        try:
            res, pcStatus, phoneStatus = checkPing(pcStatus, phoneStatus)
            pipe.send([phoneStatus, pcStatus, res])
            event.set()
            with cond:
                cond.notify()
        except:
            logger.exception('Exception in checkping!')


def checkPing(pcStatus, phoneStatus):
    """
    Checks if my PC or phone is on the network and if their state has changed.
    :return:
    """

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
                return False, pcStatus, phoneStatus
            attempts += 1
            continue
        elif (not phoneStatus) and phone_response:  # phone re-appears
            logger.info("Phone re appeared")
            attempts = 0
            pcStatus = pc_response
            phoneStatus = phone_response
            return True, pcStatus, phoneStatus
        elif phone_response and pc_response and not pcStatus:  # if pc turns on
            logger.info('PC turned on')
            pcStatus = pc_response
            phoneStatus = phone_response
            return True, pcStatus, phoneStatus
        elif phoneStatus and pcStatus and not pc_response:  # if pc turns off
            if attempts == MAX_PC_ATTEMPTS:
                logger.info("PC turned off")
                pcStatus = pc_response
                phoneStatus = phone_response
                return False, pcStatus, phoneStatus
            attempts += 1
