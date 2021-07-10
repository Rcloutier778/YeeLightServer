from yeelightLib import *

import json
import platform
import time
import yeelight


from influxdb import InfluxDBClient

os.chdir(HOMEDIR)

logger = None

bulbLog = None

YEELIGHT_STATIC_REBUILD = True

class Room:
    def __init__(self, name, bulbs):
        global logger, bulbLog

        logger = getLogger()
        bulbLog = getBulbLogger()
        self.bulbs = bulbs
        assert name in room_to_ips
        self.name = name

        if not os.path.exists(ROOM_STATES_DIR):
            os.mkdir(ROOM_STATES_DIR)
        self.room_dir = ROOM_DIR.format(room=self.name)
        if not os.path.exists(self.room_dir):
            os.mkdir(self.room_dir)

        self.roomStatePath = os.path.join(self.room_dir, 'state')
        self.manualOverridePath = MANUAL_OVERRIDE_PATH.format(room=self.name)
        self.state = None

        self.influx_client = InfluxDBClient(database='yeelight',
                                   host='127.0.0.1',
                                   port=8086,
                                   username='admin',
                                   password = open('/home/richard/aqm/sensor/influx.secret','r').read().strip()
                                   ) if 'Windows' not in platform.platform() else None
        self.rebuild_bulbs()
        logger.info('Room %s has %s bulbs', self.name, ', '.join(sorted(b._ip for b in self.bulbs)))

    def rebuild_bulbs(self):
        found_bulb_ips = sorted(bulb['ip'] for bulb in yeelight.discover_bulbs(3) if bulb['ip'] in room_to_ips[self.name] )
        current_bulb_ips = sorted(bulb._ip for bulb in self.bulbs)
        if current_bulb_ips != found_bulb_ips:
            logger.info('Different bulbs!')
            logger.info('Found bulbs: %s', ', '.join(found_bulb_ips))

            # Clear out the bulbs, since they don't like having multiple 
            #   connections to the same machine. 
            del self.bulbs[:]

            if YEELIGHT_STATIC_REBUILD:
                logger.info('Statically rebuilding bulb list')
                self.bulbs = [ yeelight.Bulb(ip) for ip in room_to_ips[self.name] ]
            else:
                self.bulbs = [yeelight.Bulb(found_ip) for found_ip in found_bulb_ips]
            try:
                self.resetFromLoggedState()
            except Exception:
                logger.exception('Got exception when restting bulbs in rebuild_bulbs')

    def writeState(self, newState):
        "Write out the state of the bulbs in the room"
        global pcStatus, phoneStatus
        bulbLog.info('%s = %s', self.name, newState)
        self.state = newState
        if not os.path.exists(self.room_dir):
            os.mkdir(self.room_dir)
        
        prev_state_dict = {'state': None, 'pcStatus': None, 'phoneStatus': None}
        if os.path.exists(self.roomStatePath):
            with open(self.roomStatePath, 'r') as f:
                prev_state_dict = json.load(f)
        
        
        if not (newState == prev_state_dict['state'] \
                and pcStatus == prev_state_dict['pcStatus'] \
                and phoneStatus == prev_state_dict['phoneStatus']):
            
            new_state_dict = {'state': newState, 'pcStatus': pcStatus, 'phoneStatus': phoneStatus}
            logger.info('Writing state dict for %s as %s', self.name, str(new_state_dict))
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
        
        if not os.path.exists(self.room_dir):
            os.mkdir(self.room_dir)
        if not os.path.exists(self.roomStatePath):
            self.writeState('day')
        
        with open(self.roomStatePath) as f:
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
        logger.info('Restting %s to last state of %s', self.name, state)
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
        logger.info('Shutting down %s', self.name)
        self.writeState(self.state)
    
    def brightness(self, val):
        bulbLog.info('Brightness = %d', val)
        for i in self.bulbs:
            i.set_brightness(val)
    
    
    def day(self, duration=3000, auto=False):
        if not auto:
            self.on()
        self.writeState('day')
        # 3200
        self.colorTempFlow(DAY_COLOR, duration, DAY_BRIGHTNESS)
    
    
    def dusk(self, duration=3000, auto=False):
        if not auto:
            self.on()
        self.writeState('dusk')
        # 3000
        self.colorTempFlow(DUSK_COLOR, duration, DUSK_BRIGHTNESS)
    
    
    def night(self, duration=3000, auto=False):
        if not auto:
            self.on()
        self.writeState('night')
        self.colorTempFlow(NIGHT_COLOR, duration, NIGHT_BRIGHTNESS)
    
    
    def sleep(self, duration=3000, auto=False):
        if not auto:
            self.on()
        self.writeState('sleep')
        self.colorTempFlow(SLEEP_COLOR, duration, SLEEP_BRIGHTNESS)
    
    
    def customTempFlow(self, temperature, duration=3000, auto=False, brightness=80):
        if not auto:
            self.on()
        self.writeState('custom:%d:%d' % (temperature, brightness,))
        self.colorTempFlow(temperature, duration, brightness)
 
    @retry
    def off(self, auto=False):
        if auto:
            # Check if system tray has been used recently to override autoset
            ld = readManualOverride(self.name)
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
    
    @retry
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
    @retry
    def rgb(self, red, green, blue):
        red = int(red)
        green = int(green)
        blue = int(blue)
        transition = yeelight.RGBTransition(red=red, green=green, blue=blue)
        for i in self.bulbs:
            i.start_flow(yeelight.Flow(count=1,
                                       action=yeelight.Flow.actions.stay,
                                       transitions=[transition]))
    
    @retry
    def colorTempFlow(self, temperature=3200, duration=3000, brightness=80):
        # control all lights at once
        # makes things look more condensed
        transition = yeelight.TemperatureTransition(degrees=temperature, duration=duration, brightness=brightness)
        for i in self.bulbs:
            i.start_flow(yeelight.Flow(count=1,
                                       action=yeelight.Flow.actions.stay,
                                       transitions=[transition]))
    
    
    def autoset(self, autosetDuration=AUTOSET_DURATION, autoset_auto_var=False, force=False):
        if not force and all(x.get_properties(['power'])['power'] == 'off' for x in self.bulbs):
            logger.info('Power is off, cancelling autoset')
            return -1
        
        # If what called autoset is not a checkping event
        if not force and not autoset_auto_var:
            # Check if system tray has been used recently to override autoset
            ld = readManualOverride(self.name)
            if ld + datetime.timedelta(hours=1) > datetime.datetime.utcnow():
                logger.info("Autoset: SystemTray used recently, canceling autoset")
                return -1

        from yeelightLib import SUNSET_TIME
        getCalcTimes()

        
        # set light level when computer is woken up, based on time of day
        rn = datetime.datetime.now()  # If there is ever a problem here, just use time.localtime()
        now = datetime.time(rn.hour, rn.minute, 0)
        
        # logger.info(['autoset: ',now])
        dayrange = [SUNRISE_TIME, SUNSET_TIME]
        if time.localtime().tm_wday in [5, 6]:  # weekend
            dayrange[0] = WEEKEND_SUNRISE_TIME
        
        autosetNightRange = getNightRange()

        nightrange = [dayrange[1], SLEEP_TIME]
        DNDrange = [nightrange[1], dayrange[0]]
        
        
        timeranges = [dayrange, nightrange, DNDrange]
        for r in timeranges:
            for rr in range(0, 2):
                t = datetime.datetime.strptime(r[rr], "%I:%M:%p")
                r[rr] = datetime.time(t.hour, t.minute, 0)
        if dayrange[0] <= now < dayrange[1]:
            logger.info("Autoset: Day")
            self.day(autosetDuration, not force)
        elif nightrange[0] <= now and now < nightrange[1]:
            for (startTime, endTime, temperature, brightness) in autosetNightRange:
                if startTime <= now and now < endTime:
                    logger.info("Autoset: temperature: %d brightness %d" % (temperature, brightness))
                    self.customTempFlow(temperature, duration=autosetDuration, auto=not force, brightness=brightness)
                    return 0
            else:
                warnstr = ["Didn't find applicable range!!!"]
                warnstr.append(dayrange)
                warnstr.append(nightrange)
                warnstr.append(DNDrange)
                warnstr.append(now)
                warnstr.append(SUNSET_TIME)
                warnstr.append(SLEEP_TIME)
                logger.warning('\n'.join(str(x) for x in warnstr))
        elif DNDrange[0] <= now or now < DNDrange[1]:
            logger.info("Autoset: dnd")
            self.off()
        return 0

    def sunrise(self):
        """
        Simulate a sunrise
        """
        writeManualOverride(room=self.name, offset=datetime.timedelta(hours=2))
        self.writeState('day')
        bulbLog.info('Sunrise start')
        overallDuration = 1200000  # 1200000 == 20 min
        self.on()
        try:
            for i in self.bulbs:
                i.set_brightness(0)
                i.set_rgb(255, 0, 0)
            logger.info("Set initial state for sunrise")
            time.sleep(1)
            transitions = [
                yeelight.HSVTransition(hue=39, saturation=100,
                    duration=overallDuration * 0.5, brightness=80),
                yeelight.TemperatureTransition(degrees=3200,
                    duration=overallDuration * 0.5, brightness=80)
            ]
            for i in self.bulbs:
                i.start_flow(yeelight.Flow(count=1, action=yeelight.Flow.actions.stay, transitions=transitions))
            logger.info('Sunrise Flowing')

        except Exception:
            logger.exception('Got exception during sunrise')



