from yeelightLib import *

from functools import wraps
import json
import platform
import time
import inspect
import yeelight



from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

os.chdir(HOMEDIR)

logger = None

bulbLog = None

# Rebuild from static list.
#   NOTE: you probably want this off since the living room has the 3 overhead lights that 
#       you normally keep off, remember?
YEELIGHT_STATIC_REBUILD = False # True

# When rebuilding dynamically, do we want to ensure that the known "safe" bulbs
#   are always in the self.bulbs dict? Known "safe" are those that are not on a wall switch
#   and should therefore always be live.
YEELIGHT_USE_SAFE_BULBS = True

def catchErr( orig_func=None ):
    def _decorate(func):
        @wraps(func)
        def catchErr_wrapper(*args, **kwargs):
            try:
                res = func(*args, **kwargs)
            except yeelight.main.BulbException as e:
                if 'A socket error occurred when sending the command' in str(e):
                    return
                raise e

            return res
        return catchErr_wrapper
    if orig_func:
        return _decorate(orig_func)
    return _decorate


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

        self.influx_client = InfluxDBClient(url="https://10.0.0.18:8086",
                                   verify_ssl=False,
                                   #cert='/etc/ssl/influxdb/influxdb-selfsigned.crt',
                                   token = open('/home/richard/influx.secret','r').read().strip()
                                   ) if 'Windows' not in platform.platform() else None
        self.influx_writer = self.influx_client.write_api(write_options=SYNCHRONOUS)

        self.rebuild_bulbs()
        logger.info('Room %s has %s bulbs', self.name, ', '.join(sorted(b._ip for b in self.bulbs)))

    def closeConns(self):
        logger.info("Closing conns")
        for bulb in self.bulbs:
            del bulb

    def openConns(self):
        self.rebuild_bulbs()


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
                if YEELIGHT_USE_SAFE_BULBS:
                    logger.info("Adding Safe bulbs")
                    found_bulb_ips = list(set(found_bulb_ips) | safe_room_to_ips[self.name])
                self.bulbs = [yeelight.Bulb(found_ip) for found_ip in found_bulb_ips]
            try:
                #self.autoset(0, force=True)
                self.resetFromLoggedState(include_IP_states=False)
            except Exception:
                logger.exception('Got exception when restting bulbs in rebuild_bulbs')

    def writeState(self, newState):
        "Write out the state of the bulbs in the room"
        global pcStatus, phoneStatus
        if newState in hiddenCommands:
            bulbLog.info( "Command was %s, not actually saving" , newState )
            return
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
                self.influx_writer.write('yeelight', 'orgname', [{'measurement':'room_state',
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
    
    def resetFromLoggedState(self, include_IP_states=True):
        """
        Crash recovery. Reset light and color values from their last saved state.
        :return:
        """
        global phoneStatus
        global pcStatus
        
        jdict = self._getLastState()
        lastState = jdict['state']
        self.state = lastState
        if include_IP_states:
            phoneStatus = jdict['phoneStatus']
            pcStatus = jdict['pcStatus']
        logger.info('Restting %s to last state of %s', self.name, lastState)
        states = [bulb.get_properties(['power','ct','bright']) for bulb in self.bulbs]

        if lastState == 'off' and not all(state['power'] == 'off' for state in states):
            self.off()
        elif lastState == 'on' and not all(state['power'] == 'on' for state in states):
            self.on()
        elif lastState == 'day' and not all( int(state['ct']) == DAY_COLOR and int(state['bright']) == DAY_BRIGHTNESS for state in states):
            self.day()
        elif lastState == 'dusk' and not all( int(state['ct']) == DUSK_COLOR and int(state['bright']) == DUSK_BRIGHTNESS for state in states):
            self.dusk()
        elif lastState == 'night' and not all( int(state['ct']) == NIGHT_COLOR and int(state['bright']) == NIGHT_BRIGHTNESS for state in states):
            self.night()
        elif lastState == 'sleep' and not all( int(state['ct']) == SLEEP_COLOR and int(state['bright']) == SLEEP_BRIGHTNESS for state in states):
            self.sleep()
        elif lastState == 'color':
            pass # Color is being manually manipulated, don't touch
        elif 'custom:' in lastState:
            temperature, brightness = lastState.split(':')[1:]
            if not all( int(state['ct']) == int(temperature) and int(state['bright']) == int(brightness) for state in states):
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
            self.on(auto=auto)
        self.writeState('day')
        # 3200
        self.colorTempFlow(DAY_COLOR, duration, DAY_BRIGHTNESS)
    
    
    def dusk(self, duration=3000, auto=False):
        if not auto:
            self.on(auto=auto)
        self.writeState('dusk')
        # 3000
        self.colorTempFlow(DUSK_COLOR, duration, DUSK_BRIGHTNESS)
    
    
    def night(self, duration=3000, auto=False):
        if not auto:
            self.on(auto=auto)
        self.writeState('night')
        self.colorTempFlow(NIGHT_COLOR, duration, NIGHT_BRIGHTNESS)
    
    
    def sleep(self, duration=3000, auto=False):
        if not auto:
            self.on(auto=auto)
        self.writeState('sleep')
        self.colorTempFlow(SLEEP_COLOR, duration, SLEEP_BRIGHTNESS)
    
    
    def customTempFlow(self, temperature, duration=3000, auto=False, brightness=80):
        if not auto:
            self.on(auto=auto)
        self.writeState('custom:%d:%d' % (temperature, brightness,))
        self.colorTempFlow(temperature, duration, brightness)

    def _onoff(self, f):
        # To reduce the input delay between pressing the button and actually performing the action,
        #   Perform on all, then begin the loop for ones that haven't changed after the first blast.
        #   Then write state
        assert f in ('on','off')
        for i in self.bulbs:
            i.turn_off() if f == 'off' else i.turn_on()

        while True:
            if all(x.get_properties(['power'])['power'] == f for x in self.bulbs):
                break
            for i in [x for x in self.bulbs if x.get_properties(['power'])['power'] != f ]:
                i.turn_off() if f == 'off' else i.turn_on()

        self.writeState(f)


    @retry
    def off(self, auto=False):
        if auto:
            # Check if system tray has been used recently to override autoset
            ld = readManualOverride(self.name)
            if ld + MANUAL_OVERRIDE_OFFSET > datetime.datetime.utcnow():
                bulbLog.info("SystemTray used recently, canceling autoset")
                return -1
            logger.info('autoset_auto off')

        self._onoff('off')
    
    @retry
    def on(self, auto=False):
        self._onoff('on')
        if not auto:
            self.autoset(autosetDuration=1, force=True, forceLight=True )
    
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

        # The GU-10 bulbs don't support color temperature, so do some approximation to use
        #   RGB settings instead.
        if any(i._ip in GU_BULBS for i in self.bulbs):
            red, green, blue = ct_to_rgb(temperature)
            rgb_transition = yeelight.RGBTransition(red=red, green=green, blue=blue)

        for i in self.bulbs:
            if i._ip in GU_BULBS:
                i.start_flow(yeelight.Flow(count=1,
                                            action=yeelight.Flow.actions.stay,
                                            transitions=[rgb_transition]))
            else:
                i.start_flow(yeelight.Flow(count=1,
                                       action=yeelight.Flow.actions.stay,
                                       transitions=[transition]))
    
    @catchErr
    def autoset(self,
        autosetDuration=AUTOSET_DURATION,   # Transition period between current and autoset light
        autoset_auto_var=False,             # Automated call from timer
        force=False,                        # Override any manual override
        forceLight = False,                 # Force DND range to use sleep lighting.
        ):
        if not force and all(x.get_properties(['power'])['power'] == 'off' for x in self.bulbs):
            logger.info('Power is off, cancelling autoset')
            return -1
        
        # If what called autoset is not a checkping event
        if not force and not autoset_auto_var:
            # Check if system tray has been used recently to override autoset
            ld = readManualOverride(self.name)
            if ld + MANUAL_OVERRIDE_OFFSET > datetime.datetime.utcnow():
                logger.info("Autoset: SystemTray used recently, canceling autoset")
                return -1

        if AUTOSET_PHONE_REQUIRED and not force and autoset_auto_var:
            ld = readManualOverride(self.name)
            if ld + MANUAL_OVERRIDE_OFFSET < datetime.datetime.utcnow():
                if not phoneStatus:
                    logger.info("AUTOSET_PHONE_REQUIRED is True and all conditions are met")
                    off(auto=True)
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
        auto = not force
        if inspect.stack()[2].function == 'on':
            auto = True
        for r in timeranges:
            for rr in range(0, 2):
                t = datetime.datetime.strptime(r[rr], "%I:%M:%p")
                r[rr] = datetime.time(t.hour, t.minute, 0)
        if dayrange[0] <= now < dayrange[1]:
            logger.info("Autoset: Day")
            self.day(autosetDuration, auto=auto)
        elif nightrange[0] <= now and now < nightrange[1]:
            for (startTime, endTime, temperature, brightness) in autosetNightRange:
                if startTime <= now and now < endTime:
                    logger.info("Autoset: temperature: %d brightness %d" % (temperature, brightness))
                    self.customTempFlow(temperature, duration=autosetDuration, auto=auto, brightness=brightness)
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
            if forceLight:
                logger.info("Force light is True, setting light to sleep colors")
                self.sleep(0, auto=auto)
                return 0
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



            transitions = [ yeelight.TemperatureTransition(degrees=1700, duration=overallDuration/4.5, brightness=10),
                    yeelight.TemperatureTransition(degrees=2700, duration=overallDuration/3, brightness=50),
                    yeelight.TemperatureTransition(degrees=DUSK_COLOR, duration=overallDuration/4.5, brightness=80),
                    yeelight.TemperatureTransition(degrees=DAY_COLOR, duration=overallDuration/4.5, brightness=80)]


            # Old manual RYC way
            #transitions = [
            #    yeelight.HSVTransition(hue=39, saturation=100,
            #        duration=overallDuration * 0.5, brightness=80),
            #    yeelight.TemperatureTransition(degrees=3200,
            #        duration=overallDuration * 0.5, brightness=80)
            #]
            for i in self.bulbs:
                i.start_flow(yeelight.Flow(count=1, action=yeelight.Flow.actions.stay, transitions=transitions))
            logger.info('Sunrise Flowing')

        except Exception:
            logger.exception('Got exception during sunrise')



