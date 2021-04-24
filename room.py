from yeelightLib import *

import json
import platform
import time
import yeelight


from influxdb import InfluxDBClient

os.chdir(HOMEDIR)

logger = getLogger()

bulbLog = getBulbLogger()


class Room:
    def __init__(self, name, bulbs):
        self.bulbs = bulbs
        assert name in room_to_ips
        self.name = name
        
        self.roomStatePath = os.path.join(ROOM_STATES_DIR, self.name)
        self.state = None

        self.influx_client = InfluxDBClient(database='yeelight',
                                   host='127.0.0.1',
                                   port=8086,
                                   username='admin',
                                   password = open('/home/richard/aqm/sensor/influx.secret','r').read().strip()
                                   ) if 'Windows' not in platform.platform() else None

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
            
            try:
                def get_color_and_brightness(state):
                    c, b = None, None
                    if state in PREDEF_STATES:
                        c, b = PREDEF_STATES[state]
                    elif state.startswith('custom'):
                        c, b = state.split(':', 2)[1:]
                    elif state in ('on','off'):
                        if state != prev_state_dict['state']:
                            c, b = get_color_and_brightness(prev_state_dict['state'])
                        else:
                            tmp = self.bulbs[0].get_properties(['bright','ct'])
                            c = tmp['ct']
                            b = tmp['bright']
                    else:
                        logger.error('%s is not a recognized state' % state)
                    
                    return c, b
                
                color, brightness = get_color_and_brightness(newState)
                if color is None:
                    return
                self.influx_client.write_points([{'measurement':'room_state',
                                             'fields':{
                                                 'room': self.name,
                                                 'state': newState.split(':', 1)[0],
                                                 'color': int(color),
                                                 'brightness': int(brightness),
                                             }}])
            except Exception:
                logger.exception('Got error trying to write to influx')
    
    
    
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
        self.colorTempFlow(DAY_COLOR, duration, DAY_BRIGHTNESS)
    
    
    def dusk(self, duration=3000, auto=False):
        self.writeState('dusk')
        if not auto:
            self.on()
        # 3000
        self.colorTempFlow(DUSK_COLOR, duration, DUSK_BRIGHTNESS)
    
    
    def night(self, duration=3000, auto=False):
        self.writeState('night')
        if not auto:
            self.on()
        self.colorTempFlow(NIGHT_COLOR, duration, NIGHT_BRIGHTNESS)
    
    
    def sleep(self, duration=3000, auto=False):
        self.writeState('sleep')
        if not auto:
            self.on()
        self.colorTempFlow(SLEEP_COLOR, duration, SLEEP_BRIGHTNESS)
    
    
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
            for i in [x for x in self.bulbs if x.get_properties(['power'])['power'] == 'on']:
                i.turn_off()
            # time.sleep(0.2)
            if all(x.get_properties(['power'])['power'] == 'off' for x in self.bulbs):
                break
    
    
    def on(self):
        self.writeState('on')
        while True:
            for i in [x for x in self.bulbs if x.get_properties(['power'])['power'] == 'off']:
                i.turn_on()
            # time.sleep(0.2)
            if all(x.get_properties(['power'])['power'] == 'on' for x in self.bulbs):
                break
    
    
    def toggle(self):
        """
        Doesn't use the built in toggle command in yeelight as it sometimes fails to toggle one of the lights.
        """
        
        oldPower = self.bulbs[0].get_properties(['power'])['power']
        if oldPower == 'off':
            self.on()
        else:
            self.off()
    
    def rgb(self, red, green, blue):
        transition = yeelight.RGBTransition(red=red, green=green, blue=blue)
        for i in self.bulbs:
            i.start_flow(yeelight.Flow(count=1,
                                       action=yeelight.Flow.actions.stay,
                                       transitions=[transition]))
    
    def colorTempFlow(self, temperature=3200, duration=3000, brightness=80):
        # control all lights at once
        # makes things look more condensed
        transition = yeelight.TemperatureTransition(degrees=temperature, duration=duration, brightness=brightness)
        for i in self.bulbs:
            i.start_flow(yeelight.Flow(count=1,
                                       action=yeelight.Flow.actions.stay,
                                       transitions=[transition]))
    
    
    def autoset(self, autosetDuration=AUTOSET_DURATION, autoset_auto_var=False):
        if all(x.get_properties(['power'])['power'] == 'off' for x in self.bulbs):
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

