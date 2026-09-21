from yeelightLib import *

from functools import wraps
import json
import platform
import time
import inspect
import yeelight
import asyncio
import threading

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
    def __init__(self, name, bulbs, envState):
        global logger, bulbLog

        logger = getLogger()
        bulbLog = getBulbLogger()
        self.bulbs = bulbs
        assert name in room_to_ips
        self.name = name
        self.envState = envState

        if not os.path.exists(ROOM_STATES_DIR):
            os.mkdir(ROOM_STATES_DIR)
        self.room_dir = ROOM_DIR.format(room=self.name)
        if not os.path.exists(self.room_dir):
            os.mkdir(self.room_dir)

        self.roomStatePath = os.path.join(self.room_dir, 'state')
        self.state = None

        #Make sure we start off with the right state written.
        last_state = self._getLastState()
        self.writeState(last_state['state'], self.envState.pcStatus, self.envState.phoneStatus)

        self.influx_client = InfluxDBClient(url="https://10.0.0.18:8086",
                                   verify_ssl=False,
                                   #cert='/etc/ssl/influxdb/influxdb-selfsigned.crt',
                                   token = open('/home/richard/influx.secret','r').read().strip()
                                   ) if 'Windows' not in platform.platform() else None
        self.influx_writer = (
            self.influx_client.write_api(write_options=SYNCHRONOUS)
            if self.influx_client is not None else None
        )

        self.rebuild_bulbs()
        #self.bulb_listener = self.listen_for_bulb_updates()

        logger.info('Room %s has %s bulbs', self.name, ', '.join(sorted(b._ip for b in self.bulbs)))


    def listen_for_bulb_updates(self):
        res = {}
        for bulb in self.bulbs:
            thread = threading.Thread(target=self._listen_for_bulb_updates, args=(bulb,), daemon=True)
            thread.start()
            res[bulb._ip] = thread
        return res

    def _listen_for_bulb_updates(self, bulb):
        def lfbu_callback(*args, **kwargs):
            logger.info(bulb._ip)
            logger.info(args)
            logger.info(kwargs)
            bulb.listen(lfbu_callback)
        bulb.listen(lfbu_callback)


    def closeConns(self):
        logger.info("Closing conns")
        for bulb in self.bulbs:
            del bulb

    def openConns(self):
        self.rebuild_bulbs()


    def rebuild_bulbs(self, discovered_bulb_ips=None):
        """Rebuild the room bulb list from an optional shared discovery result."""
        if discovered_bulb_ips is None:
            discovered_bulb_ips = [b['ip'] for b in yeelight.discover_bulbs(3)]
        found_bulb_ips = sorted(set(discovered_bulb_ips) & set(room_to_ips[self.name]))
        current_bulb_ips = sorted(bulb._ip for bulb in self.bulbs)
        if current_bulb_ips != found_bulb_ips:
            logger.info('Different bulbs!')
            logger.info('Found bulbs: %s', ', '.join(found_bulb_ips))

            if YEELIGHT_STATIC_REBUILD:
                logger.info('Statically rebuilding bulb list')
                new_bulbs = [Bulb(ip, roomName=self.name) for ip in room_to_ips[self.name]]
            else:
                if YEELIGHT_USE_SAFE_BULBS:
                    logger.info("Adding Safe bulbs")
                    found_bulb_ips = list(set(found_bulb_ips) | set(safe_room_to_ips[self.name]))
                new_bulbs = [Bulb(found_ip, roomName=self.name) for found_ip in found_bulb_ips]

            # Construct the replacement list before swapping it into the room so a
            # discovery/constructor failure cannot leave the room with an empty list.
            self.bulbs = new_bulbs
            try:
                self.resetFromLoggedState(include_IP_states=False)
                return #TODO
                if self.envState.phoneStatus:
                    self.autoset(0, force=True)
                else:
                    self.resetFromLoggedState(include_IP_states=False)
            except Exception:
                logger.info(current_bulb_ips)
                logger.exception('Got exception when restting bulbs in rebuild_bulbs')

    def writeState(self, newState, pcStatusOverride=None, phoneStatusOverride=None):
        "Write out the state of the bulbs in the room"
        # TODO Should actually override self.envState.*?
        if pcStatusOverride is not None:
            pcStatus = pcStatusOverride
        else:
            pcStatus = self.envState.pcStatus
        if phoneStatusOverride is not None:
            phoneStatus = phoneStatusOverride
        else:
            phoneStatus = self.envState.phoneStatus
        if newState in hiddenCommands:
            bulbLog.info( "Command was %s, not actually saving" , newState )
            return
        bulbLogNewState = newState
        if inspect.stack()[2].function == 'autoset':
            bulbLogNewState = f'autoset ({bulbLogNewState})'
        bulbLog.info('%s = %s', self.name, bulbLogNewState)
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
            def get_color_and_brightness(state):
                c, b = None, None
                if state in PREDEF_STATES:
                    c, b = PREDEF_STATES[state]
                elif state.startswith('custom'):
                    c, b = state.split(':', 2)[1:]
                elif state in ('on','off'):
                    logger.info(prev_state_dict)
                    if state != prev_state_dict['state']:
                        c, b = get_color_and_brightness(prev_state_dict['state'])
                    else:

                        tmp = self.applyFuncAndRebuild(lambda blb: blb.get_properties(['bright','ct']), selectBulbIps=[self.bulbs[0]._ip])[0]
                        #tmp = self.bulbs[0].get_properties(['bright','ct'])
                        c = tmp['ct']
                        b = tmp['bright']
                elif state == 'autoset':
                    #TODO
                    return None, None
                else:
                    logger.error('%s is not a recognized state' % state)

                return c, b
            try: #TODO
                color, brightness = get_color_and_brightness(newState)
                if color is None:
                    return
            except Exception:
                logger.exception('got error when getting color and brightness')
                return

            if self.influx_writer is not None:
                try:
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
        validStates = ['day', 'dusk', 'night', 'off', 'sleep', 'on', 'color', 'autoset']
        
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
        
        jdict = self._getLastState()
        lastState = jdict['state']
        self.state = lastState
        if include_IP_states:
            phoneStatus = jdict['phoneStatus']
            pcStatus = jdict['pcStatus']
        else:
            phoneStatus = self.envState.phoneStatus
            pcStatus = self.envState.pcStatus
        logger.info('Restting %s to last state of %s', self.name, lastState)

        #states = [bulb.get_properties(['power','ct','bright']) for bulb in self.bulbs]
        states = self.applyFuncAndRebuild(lambda blb: blb.get_properties(['power', 'ct', 'bright']))

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
        elif lastState == 'autoset':
            self.autoset(force=True, forceLight=True)
        elif 'custom:' in lastState:
            temperature, brightness = lastState.split(':')[1:]
            if not all( int(state['ct']) == int(temperature) and int(state['bright']) == int(brightness) for state in states):
                self.customTempFlow(int(temperature), brightness=int(brightness))


    def applyFuncAndRebuild(self, func, selectBulbIps=None):
        """
        Loop through the bulbs in self.bulbs, apply func to each
        If it encounters an error, rebuild the error bulb only.
        selectBulbIps = list of bulb ips from self.bulbs to apply the func on
        """
        result = []
        bulbs = []
        tempBulbs = list(self.bulbs)
        for attempt in range(3):
            bulbs = list(tempBulbs)
            tempBulbs = []
            for bulbIdx, bulb in enumerate(list(bulbs)):
                if bulb is None:
                    tempBulbs.append(None)
                    continue
                if selectBulbIps is not None and bulb._ip not in selectBulbIps:
                    tempBulbs.append(None)
                    continue
                try:
                    result.append(func(bulb))
                    tempBulbs.append(None)
                except Exception as e:
                    logger.error('Error from %s on try %d' % (bulb, attempt,))
                    logger.exception(e)
                    newBulb = Bulb(bulb._ip, roomName=self.name)
                    tempBulbs.append(newBulb)
                    self.bulbs[bulbIdx] = newBulb
            if all(x is None for x in tempBulbs):
                if attempt != 0:
                    logger.info("Recovered from bulb failure")
                break
        else:
            raise RuntimeError("Failed to rebuild after apply failure!")

        return result

    def graceful_kill(self):
        logger.info('Shutting down %s', self.name)
        self.writeState(self.state)

    def brightness(self, val):
        bulbLog.info('Brightness = %d', val)
        for i in self.bulbs:
            i.set_brightness(val)

    def day(self, duration=3000, auto=False):
        if not auto:
            self.on(auto=auto, writeState=False)
        self.writeState('day')
        # 3200
        self.colorTempFlow(DAY_COLOR, duration, DAY_BRIGHTNESS)
    
    
    def dusk(self, duration=3000, auto=False):
        if not auto:
            self.on(auto=auto, writeState=False)
        self.writeState('dusk')
        # 3000
        self.colorTempFlow(DUSK_COLOR, duration, DUSK_BRIGHTNESS)
    
    
    def night(self, duration=3000, auto=False):
        if not auto:
            self.on(auto=auto, writeState=False)
        self.writeState('night')
        self.colorTempFlow(NIGHT_COLOR, duration, NIGHT_BRIGHTNESS)
    
    
    def sleep(self, duration=3000, auto=False):
        if not auto:
            self.on(auto=auto, writeState=False)
        self.writeState('sleep')
        self.colorTempFlow(SLEEP_COLOR, duration, SLEEP_BRIGHTNESS)
    
    
    def customTempFlow(self, temperature, duration=3000, auto=False, brightness=80):
        if not auto:
            self.on(auto=auto)
        #self.writeState('custom:%d:%d' % (temperature, brightness,))
        self.colorTempFlow(temperature, duration, brightness)

    def _onoff(self, f, writeState=True):
        # Send the first command to every bulb immediately, then verify with bounded
        # retries. A short backoff avoids a tight network-polling loop when a bulb is
        # slow or temporarily unreachable.
        assert f in ('on', 'off')
        self.applyFuncAndRebuild(lambda i: i.turn_off() if f == 'off' else i.turn_on())

        max_attempts = 5
        for attempt in range(max_attempts):
            states = self.applyFuncAndRebuild(
                lambda x: (x._ip, x.get_properties(['power'])['power'])
            )
            state_by_ip = dict(states)
            active_ips = {bulb._ip for bulb in self.bulbs}

            if active_ips and len(state_by_ip) == len(active_ips) and all(
                state_by_ip.get(ip) == f for ip in active_ips
            ):
                break

            if attempt + 1 >= max_attempts:
                raise RuntimeError(
                    'Failed to set all bulbs to power=%s after %d verification attempts'
                    % (f, max_attempts)
                )

            mismatched_ips = {
                ip for ip in active_ips
                if state_by_ip.get(ip) != f
            }
            time.sleep(min(0.5, 0.1 * (2 ** attempt)))
            self.applyFuncAndRebuild(
                lambda i: i.turn_off() if f == 'off' else i.turn_on(),
                selectBulbIps=mismatched_ips,
            )

        if writeState:
            self.writeState(f)

    @retry
    def off(self, auto=False, force=None):
        if auto:
            # Check if system tray has been used recently to override autoset
            ld = readManualOverride(self.name)
            if ld + MANUAL_OVERRIDE_OFFSET > datetime.datetime.utcnow():
                bulbLog.info("SystemTray used recently, canceling autoset")
                return -1
            logger.info('autoset_auto off')

        self._onoff('off')
    
    @retry
    def on(self, auto=False, force=None, writeState=True):
        # ~10 ms
        originallyOn = all(self.applyFuncAndRebuild(lambda x: x.get_properties(['power'])['power'] == 'on'))
        # ~211 ms
        self._onoff('on', writeState=writeState)
        if not auto and not originallyOn:
            self.autoset(autosetDuration=1, force=True, forceLight=True )

    def toggle(self):
        """
        Doesn't use the built in toggle command in yeelight as it sometimes fails to toggle one of the lights.
        """
        oldPower = self.applyFuncAndRebuild(lambda blb: blb.get_properties(['power'])['power'], selectBulbIps=[self.bulbs[0]._ip])[0]
        #oldPower = self.bulbs[0].get_properties(['power'])['power']
        if oldPower == 'off':
            self.on(force=True)
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


    def _threadedColorTempFlow(self, bulb, transition):
        for retryCount in range(3):
            try:
                bulb.start_flow(
                    yeelight.Flow(
                        count=1,
                        action=yeelight.Flow.actions.stay,
                        transitions=[transition],
                    )
                )
                break
            except Exception as e:
                logger.error('Got exception in _threadedColorTempFlow for bulb %s: %s', bulb._ip, str(e))
                time.sleep(0.5)
        else:
            logger.error('Hit max retries for bulb %s', bulb._ip)
            raise RuntimeError('Hit max retries in _threadedColorTempFlow')

    def threadedColorTempFlow(self, temperature=3200, duration=3000, brightness=80):
        # Control all lights at once; run each network request concurrently.
        transition = yeelight.TemperatureTransition(
            degrees=temperature, duration=duration, brightness=brightness
        )

        # The GU-10 bulbs don't support color temperature, so approximate it
        # with an RGB transition instead.
        rgb_transition = None
        if any(i._ip in GU_BULBS for i in self.bulbs):
            red, green, blue = ct_to_rgb(temperature)
            rgb_transition = yeelight.RGBTransition(
                red=red, green=green, blue=blue
            )

        errors = []

        def run_bulb(bulb):
            try:
                self._threadedColorTempFlow(
                    bulb,
                    rgb_transition if bulb._ip in GU_BULBS else transition,
                )
            except Exception as exc:
                errors.append((bulb._ip, exc))

        threads = []
        for bulb in self.bulbs:
            thread = threading.Thread(target=run_bulb, args=(bulb,))
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        if errors:
            for ip, exc in errors:
                logger.error(
                    'Color-temperature flow failed permanently for bulb %s: %s',
                    ip,
                    exc,
                )
            raise errors[0][1]
    

    #@retry
    def colorTempFlow(self, temperature=3200, duration=3000, brightness=80):
        # control all lights at once
        # makes things look more condensed
        #self.asyncColorTempFlow(temperature, duration, brightness)
        #return

        logger.info(time.time())
        self.threadedColorTempFlow(temperature, duration, brightness)
        logger.info(time.time())
        return

        transition = yeelight.TemperatureTransition(degrees=temperature, duration=duration, brightness=brightness)

        # The GU-10 bulbs don't support color temperature, so do some approximation to use
        #   RGB settings instead.
        if any(i._ip in GU_BULBS for i in self.bulbs):
            red, green, blue = ct_to_rgb(temperature)
            rgb_transition = yeelight.RGBTransition(red=red, green=green, blue=blue)
        bulbs = list(self.bulbs)
        for retryCount in range(3):
            if not bulbs:
                return
            if retryCount > 0:
                logger.info('colorTempFlow retry %d', retryCount)
                time.sleep(0.5)
            for bulbIdx, bulb in reversed(list(enumerate(list(bulbs)))):
                try:
                    logger.info(time.time())
                    if bulb._ip in GU_BULBS:
                        bulb.start_flow(yeelight.Flow(count=1,
                                                action=yeelight.Flow.actions.stay,
                                                transitions=[rgb_transition]))
                    else:
                        bulb.start_flow(yeelight.Flow(count=1,
                                               action=yeelight.Flow.actions.stay,
                                               transitions=[transition]))
                    bulbs.pop(bulbIdx)
                except Exception:
                    logger.exception('Got exception in colorTempFlow for bulb %s', bulb._ip)
        else:
            logger.error('Hit max retries in colorTempFlow!')
            raise RuntimeError('Hit max retries in colorTempFlow!')
        #for i in self.bulbs:
        #    if i._ip in GU_BULBS:
        #        i.start_flow(yeelight.Flow(count=1,
        #                                    action=yeelight.Flow.actions.stay,
        #                                    transitions=[rgb_transition]))
        #    else:
        #        i.start_flow(yeelight.Flow(count=1,
        #                               action=yeelight.Flow.actions.stay,
        #                               transitions=[transition]))

    @retry
    #@catchErr
    def autoset(self,
        autosetDuration=AUTOSET_DURATION,   # Transition period between current and autoset light
        autoset_auto_var=False,             # Automated call from timer
        force=False,                        # Override any manual override
        forceLight = False,                 # Force DND range to use sleep lighting.
        ):
        
        if not force and all(self.applyFuncAndRebuild(lambda x: x.get_properties(['power'])['power'] == 'off')):
            logger.info('Power is off, cancelling autoset')
            return -1
        #if not force and all(x.get_properties(['power'])['power'] == 'off' for x in self.bulbs):
        #    logger.info('Power is off, cancelling autoset')
        #    return -1
        
        from yeelightLib import SUNSET_TIME
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

        def isTimeInRange(timeRange):
            if timeRange[0] == DNDrange[0] and timeRange[1] == DNDrange[1]:
                return timeRange[0] <= now or now < timeRange[1]
            return timeRange[0] <= now < timeRange[1]

        # If what called autoset is not a checkping event
        if not force and not autoset_auto_var:
            # Check if system tray has been used recently to override autoset
            dct = readManualOverride(self.name, returnDict=True)
            if dct['time'] + MANUAL_OVERRIDE_OFFSET > datetime.datetime.utcnow():
                if dct['action'] == '':
                    dct['action'] = self._getLastState()['state']
                    logger.info(f'action was blank, setting to {dct["action"]} instead')
                if (dct['action'] not in ('autoset', 'MANUAL_AUTOSET_FORCE_LIGHT')) or (not isTimeInRange(DNDrange)):
                    #if (dct['action'] == 'toggle' and self.bulbs[0].get_properties(['power'])['power'] == 'on') or (dct['action'] == 'on'):
                    if dct['action'] == 'toggle' and self.applyFuncAndRebuild(lambda blb: blb.get_properties(['power'])['power'] == 'on', selectBulbIps=[self.bulbs[0]._ip])[0] or (dct['action'] == 'on'):
                        pass
                    elif (dct['action'] == 'MANUAL_AUTOSET_FORCE_LIGHT') and (not isTimeInRange(DNDrange)):
                        pass
                    elif (dct['action'] in ('autoset', 'MANUAL_AUTOSET_FORCE_LIGHT')) and isTimeInRange(nightrange):
                        pass
                    else:
                        # if the manual action wasn't a manual autoset, or it's not in DND time, return
                        logger.info(f"Autoset: SystemTray used recently ({dct['time']}) ({dct['action']}), canceling autoset")
                        return -1

        if AUTOSET_PHONE_REQUIRED and not force and autoset_auto_var:
            ld = readManualOverride(self.name)
            if ld + MANUAL_OVERRIDE_OFFSET < datetime.datetime.utcnow():
                if not self.envState.phoneStatus:
                    logger.info("AUTOSET_PHONE_REQUIRED is True and all conditions are met")
                    self.off(auto=True)
                    return -1

        current_bulb_properties = self.applyFuncAndRebuild(lambda x: [x, x.get_properties(['power','bright','ct'])]) if AUTOSET_TIMER_CHECK_BEFORE_EXEC else []
        current_bulb_properties = { b._ip : props for (b, props) in current_bulb_properties }

        def _bulb_properties_match(d_power, d_temp, d_bright):
            for b_ip, props in current_bulb_properties.items():
                for pkey, d_val in zip(['power','ct','bright'],[d_power, d_temp, d_bright]):
                    if props[pkey] != str(d_val):
                        logger.info(f'Bulb {b_ip} {pkey} was {props[pkey]}. Desired was {str(d_val)}')
                        return False

            return True

        getCalcTimes()

        auto = not force
        if inspect.stack()[2].function == 'on':
            auto = True

        if isTimeInRange(dayrange):
            logger.info("Autoset: Day")
            if not AUTOSET_TIMER_CHECK_BEFORE_EXEC:
                self.day(autosetDuration, auto=auto)
            elif _bulb_properties_match('on', DAY_COLOR, DAY_BRIGHTNESS):
                logger.info('All bulbs were already in day state')
            else:
                self.day(autosetDuration, auto=auto)
        elif isTimeInRange(nightrange):
            for (startTime, endTime, temperature, brightness) in autosetNightRange:
                if startTime <= now and now < endTime:
                    logger.info("Autoset: temperature: %d brightness %d" % (temperature, brightness))
                    self.writeState('autoset')
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
        elif isTimeInRange(DNDrange):
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
        bulbLog.info('Sunrise start')
        writeManualOverride(room=self.name, offset=datetime.timedelta(hours=2))
        self.writeState('day')
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


    def returnFromAway(self):
        """
        For when some motion detector activates at the front door
        """
        jdict = self._getLastState()
        logger.info("In returnFromAway")
        logger.info(f'state: {jdict["state"]} phoneStatus: {jdict["phoneStatus"]}')
        if jdict['state'] == 'off' and not jdict['phoneStatus']:
            logger.info("setting lights from returnFromAway")
            self.autoset(autosetDuration=1, force=True)

