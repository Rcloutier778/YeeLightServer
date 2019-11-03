import yeelight
import time
import yeelight.transitions
import yeelight.enums
import sys
import datetime
import logging
import json
import os
import pickle
import requests
import pytz

HOMEDIR = '/home/richard/YeeLightServer/'
os.chdir(HOMEDIR)

logger = logging.getLogger('log')
logging.basicConfig(filename=HOMEDIR+'logger.log',
                    filemode='a',
                    format='%(asctime)s %(name)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d %I:%M:%S%p',
                    level=logging.INFO)

richard_bulb_ips=["10.0.0.5","10.0.0.10","10.0.0.15"]

commands=['sunrise','autoset','autoset_auto']
allcommands=commands + ['bright','brightness','rgb']



#TODO
"""
2) cortana integration
"""



class bulbGroup(object):
    def __init__(self, bulbs, user):
        self.bulbs=bulbs
        self.user = user
        self.phoneStatus = True
        self.pcStatus = True
        self.__DAY_COLOR = 4000
        self.__DUSK_COLOR = 3300
        self.__NIGHT_COLOR = 2500
        self.__SLEEP_COLOR = 1500
        self.__SUNSET_TIME = '5:30:PM'
        self.__TWILIGHT_TIME = '6:30:PM'
        self.__SLEEP_TIME = '10:30:PM'
        self.phoneIP = "10.0.0.7"
        self.pcIP = "10.0.0.2"
        
    def sunrise(self):
        # Prevent autoset from taking over
        self.writeManualOverride()
    
        # Write the new state
        self.writeState('day')
        
        overallDuration = 1200000  # 1200000 == 20 min
        self.on()
        
        for i in self.bulbs:
            i.set_brightness(0)
            i.set_rgb(255, 0, 0)
        time.sleep(1)
        
        transitions = [yeelight.HSVTransition(hue=39, saturation=100,
                                              duration=overallDuration//2, brightness=80),
                       yeelight.TemperatureTransition(degrees=3200,
                                                      duration=overallDuration//2, brightness=80)]
        
        for i in self.bulbs:
            i.start_flow(yeelight.Flow(count=1, action=yeelight.Flow.actions.stay, transitions=transitions))
    
    
    def brightness(self, val):
        for i in self.bulbs:
            i.set_brightness(val)
    
    
    def day(self, duration=3000, auto=False):
        self.writeState('day')
        if not auto:
            self.on()
        # 3200
        self.colorTempFlow(self.__DAY_COLOR, duration, 80)
    
    
    def dusk(self, duration=3000, auto=False):
        self.writeState('dusk')
        if not auto:
            self.on()
        # 3000
        self.colorTempFlow(self.__DUSK_COLOR, duration, 80)
    
    
    def night(self, duration=3000, auto=False):
        self.writeState('night')
        if not auto:
            self.on()
        self.colorTempFlow(self.__NIGHT_COLOR, duration, 80)
    
    
    def sleep(self, duration=3000, auto=False):
        self.writeState('sleep')
        if not auto:
            self.on()
        self.colorTempFlow(self.__SLEEP_COLOR, duration, 20)
    
    
    def customTempFlow(self, temperature, duration=3000, auto=False, brightness=80):
        self.writeState('custom:%d' % temperature)
        if not auto:
            self.on()
        self.colorTempFlow(temperature, duration, brightness)
    
    
    def off(self, auto=False):
        if auto:
            # Check if system tray has been used recently to override autoset
            with open(os.getcwd() + '/' + self.user + '_manualOverride.txt', 'r') as f:
                ld = f.read().strip()
            if datetime.datetime.strptime(ld, '%Y-%m-%d %H:%M:%S') + datetime.timedelta(
                    hours=1) > datetime.datetime.utcnow():
                print("SystemTray used recently, canceling autoset")
                logger.info("SystemTray used recently, canceling autoset")
                return -1
        
        self.writeState('off')
        while all(x.get_properties()['power'] != 'off' for x in self.bulbs):
            for i in self.bulbs:
                i.turn_off()
    
    
    def on(self):
        self.writeState('on')
        while all(x.get_properties()['power'] != 'on' for x in self.bulbs):
            for i in self.bulbs:
                i.turn_on()
    
    
    def toggle(self):
        """
        Doesn't use the built in toggle command in yeelight as it sometimes fails to toggle one of the lights.
        """
        oldPower = self.bulbs[0].get_properties()['power']
        if oldPower == 'off':
            self.on()
        else:
            self.off()

    def resetFromLoggedState(self):
        jdict = self.getLastState()
        state = jdict['state']
        self.phoneStatus = jdict['self.phoneStatus']
        self.pcStatus = jdict['self.pcStatus']

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
            temperature = int(state.split(':')[1])
            self.customTempFlow(temperature)


    def writeState(self, newState):
        with open(HOMEDIR + 'bulbStateLog', 'r+') as f:
            jdict = json.load(f)
            if not (newState == jdict['state'] and self.pcStatus == jdict['self.pcStatus'] and self.phoneStatus == jdict['self.phoneStatus']):
                jdict = {'state': newState, 'self.pcStatus': self.pcStatus, 'self.phoneStatus': self.phoneStatus}
                f.seek(0)
                json.dump(jdict, f)
                f.truncate()

    def getLastState(self):
        validStates = ['day', 'dusk', 'night', 'off', 'sleep', 'on', 'color']
        with open(HOMEDIR + 'bulbStateLog', 'r') as f:
            jdict = json.load(f)
            if jdict['state'] not in validStates and 'custom:' not in jdict['state']:
                jdict['state'] = 'off'
        return jdict

    def set_IRL_sunset(self):
        r = requests.post('https://api.sunrise-sunset.org/json?lat=40.739589&lng=-74.035677')
        assert r.status_code == 200
        origDict = json.loads(r.text)['results']
        for key in ['sunset', 'civil_twilight_end']:
            ogTime = datetime.datetime.strptime(origDict[key], "%I:%M:%S %p")
            localTime = pytz.utc.localize(ogTime).astimezone(pytz.timezone('US/Eastern'))
            localTime = localTime.replace(tzinfo=None)
            origDict[key] = localTime  # .strftime("%I:%M:%S %p")
        self.__SUNSET_TIME = origDict['sunset'].strftime("%I:%M:%p")
        self.__TWILIGHT_TIME = origDict['civil_twilight_end'].strftime("%I:%M:%p")
        returnRange = []
        iters = 20  # number of iters to calc self.on
        tempDiff = self.__DUSK_COLOR - self.__SLEEP_COLOR  # temp difference between sunset and sleep
        brightnessChangePoint = self.__DUSK_COLOR - (3 * tempDiff // 4)  # when to start changing brightness
        timeDiff = (datetime.datetime.strptime(self.__SLEEP_TIME, "%I:%M:%p") - origDict[
            'civil_twilight_end']).total_seconds() // 60  # minutes between AFTER sunset and sleep
    
        for i in range(iters + 1):
            brightness = 80
            startTime = origDict['civil_twilight_end'] + datetime.timedelta(minutes=timeDiff * i // iters)
        
            endTime = startTime + datetime.timedelta(minutes=1 + (timeDiff // iters))
            temperature = self.__DUSK_COLOR - int(tempDiff * i // iters)
            if temperature < brightnessChangePoint:
                brightness = int(80 * ((iters - i) / iters)) + 20
            returnRange.append([startTime.time(), endTime.time(), temperature, brightness])
        for i in returnRange:
            logger.info(i)
        with open(HOMEDIR + 'nightTimeRange.pickle', 'wb+') as f:
            pickle.dump(returnRange, f)

    def autoset_auto(self):
        logger.error("Boot autoset_auto")
        try:
            self.set_IRL_sunset()
        except Exception as e:
            logger.error(e, exc_info=True)
            return
        # Below added to fix bug where this program would crash and burn upon phone reappearing
        # self.on()
        # autoset(autosetDuration=300)
        self.resetFromLoggedState()
    
        # End hack
        try:
            self.autoset(1, autoset_auto_var=True)
        except Exception as e:
            logger.error(e, exc_info=True)
    
        systemStartTime = datetime.datetime.utcnow()
        while True:
            try:
                phoneFound = self.checkPing()
            
                if not phoneFound:  # Was 1, now 0
                    logger.info("Autoset_auto off")
                    self.off(True)
                else:  # Was 0, now 1
                    while True:
                        logger.info("Autoset_auto on")
                        try:
                            self.on()
                            self.autoset(autosetDuration=300, autoset_auto_var=True)
                            break
                        except Exception as e:
                            logger.error(e, exc_info=True)
            except Exception as e:
                logger.error(e, exc_info=True)
            finally:
                if (systemStartTime + datetime.timedelta(days=3)) < datetime.datetime.utcnow():
                    systemStartTime = datetime.datetime.utcnow()
                    self.set_IRL_sunset()

    # noinspection PyTypeChecker
    def autoset(self, autosetDuration=300000, autoset_auto_var=False):
        if all(x.get_properties()['power'] == 'off' for x in self.bulbs):
            logger.info('Power is self.off, cancelling autoset')
            return -1
    
        # If what called autoset is not a checkping event
        if not autoset_auto_var:
            # Check if system tray has been used recently to override autoset
            with open(os.getcwd() + '/' + self.user + '_manualOverride.txt', 'r') as f:
                ld = f.read().strip()
            if datetime.datetime.strptime(ld, '%Y-%m-%d %H:%M:%S') + datetime.timedelta(
                    hours=1) > datetime.datetime.utcnow():
                print("SystemTray used recently, canceling autoset")
                logger.info("SystemTray used recently, canceling autoset")
                return -1
    
        # set light level when computer is woken up, based self.on time of day
        rn = datetime.datetime.now()  # If there is ever a problem here, just use time.localtime()
        now = datetime.time(rn.hour, rn.minute, 0)
    
        # logger.info(['autoset: ',now])
        dayrange = ["6:15:AM", self.__SUNSET_TIME]
        if time.localtime().tm_wday in [5, 6]:  # weekend
            print("weekend")
            dayrange[0] = "7:30:AM"
    
        # TODO Remember to make changes to raspberry pi too!
        duskrange = [dayrange[1], self.__TWILIGHT_TIME]
    
        nightrange = [duskrange[1], self.__SLEEP_TIME]
        DNDrange = [nightrange[1], dayrange[0]]
    
        with open(HOMEDIR + 'nightTimeRange.pickle', 'rb') as f:
            autosetNightRange = pickle.load(f)
    
        timeranges = [dayrange, duskrange, nightrange, DNDrange]
        for r in timeranges:
            for rr in range(0, 2):
                t = datetime.datetime.strptime(r[rr], "%I:%M:%p")
                r[rr] = datetime.time(t.hour, t.minute, 0)
        if dayrange[0] <= now < dayrange[1]:
            logger.info("Autoset: Day")
            self.day(autosetDuration, True)
        elif duskrange[0] <= now < duskrange[1]:
            logger.info("Autoset: Dusk")
            self.dusk(autosetDuration, True)
        elif nightrange[0] <= now and now < nightrange[1]:
            for (startTime, endTime, temperature, brightness) in autosetNightRange:
                if startTime <= now and now < endTime:
                    logger.info("Autoset: temperature: %d brightness %d" % (temperature, brightness))
                    self.customTempFlow(temperature, duration=autosetDuration, auto=True, brightness=brightness)
                    return 0
        elif DNDrange[0] <= now or now < DNDrange[1]:
            logger.info("Autoset: dnd")
            self.off()
        return 0

    def checkPing(self):
        if self.phoneStatus:
            sleepTime = 7
        else:
            sleepTime = 0.5
        attempts = 0
        while True:
            time.sleep(sleepTime)
            phone_response = not bool(os.system("ping -c 1 -W 2 " + self.phoneIP))
            pc_response = not bool(os.system("ping -c 1 -W 1 " + self.pcIP))
            if (phone_response == self.phoneStatus) and (pc_response == self.pcStatus):  # no changes
                attempts = 0
                continue
            elif not phone_response:  # phone is missing
                attempts += 1
                if attempts > 2:  # try 3 times
                    logger.info("Phone missing")
                    self.pcStatus = pc_response
                    self.phoneStatus = phone_response
                    return False
            elif (not self.phoneStatus) and phone_response:  # phone re-appears
                logger.info("Phone re appeared")
                self.pcStatus = pc_response
                self.phoneStatus = phone_response
                return True
            elif phone_response and pc_response and not self.pcStatus:  # if pc turns on
                logger.info('PC turned on')
                self.pcStatus = pc_response
                self.phoneStatus = phone_response
                return True
            elif self.phoneStatus and self.pcStatus and not pc_response:  # if pc turns off
                logger.info("PC turned off")
                self.pcStatus = pc_response
                self.phoneStatus = phone_response
                return False

    def colorTempFlow(self, temperature=3200, duration=3000, brightness=80):
        # control all lights at once
        # makes things look more condensed
        transition = yeelight.TemperatureTransition(degrees=temperature, duration=duration, brightness=brightness)
        for i in self.bulbs:
            i.start_flow(yeelight.Flow(count=1,
                                       action=yeelight.Flow.actions.stay,
                                       transitions=[transition]))
            
    def writeManualOverride(self):
        with open(os.getcwd() + '/' + self.user + '_manualOverride.txt', 'w+') as f:
            f.write(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))


def main():
    if len(sys.argv) == 1:
        print("No arguments.")
        logger.warning('No arguments.')
        return
    else:
        cmd=sys.argv[1].lower()
        usr=sys.argv[2].lower()

        if cmd in allcommands:
            if cmd in commands:
                if 'autoset' not in cmd:
                    logger.info(cmd)
                
                if usr=='richard':
                    bg = bulbGroup(richard_bulb_ips, usr)
                else: #make this elif then throw error w/ else 
                    #TODO Add vlad lights
                    #self.bulbs=[vlad lights]
                    #phoneIP=
                    #pcIP=
                    return
                if cmd == 'autoset':
                    bg.autoset()
                elif cmd == 'autoset_auto':
                    bg.autoset_auto()
                elif cmd == 'sunrise':
                    bg.sunrise()
                
        else:
            print("Command \"%s\" not found"%cmd)


def discoverBulbs():
    bulbs = yeelight.discover_bulbs()
    for bulb in bulbs:
        print(bulb)
        

if __name__ == "__main__":
    #Run the system tray app
    #run the python script
    main()
