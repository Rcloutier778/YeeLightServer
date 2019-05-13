import yeelight
import time
import yeelight.transitions
import yeelight.enums
import sys
import datetime
import logging
import os

os.chdir('/home/richard/YeeLightServer/')

log = logging.getLogger('log')
logging.basicConfig(filename=os.getcwd()+'/log.log',
                    filemode='a',
                    format='%(asctime)s %(name)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d %I:%M:%S%p',
                    level=logging.INFO)

r_bed_stand = "10.0.0.5"
r_bed_desk ="10.0.0.10"
b=[]
phoneIP=None
pcIP=None
user=None
commands=['dusk','day','night','sleep', 'off', 'on','toggle','sunrise','autoset','logon','autoset_auto']
allcommands=commands + ['bright','brightness','rgb']

__DAY_COLOR=4000
__DUSK_COLOR=3300
__NIGHT_COLOR=2500
__SLEEP_COLOR=1500

#TODO
"""
1) autoset on wakeup from lan
2) cortana integration
3) hourly light temp color updates
4) Brightness slider in system tray
"""

def main():
    #print(desk.get_properties())
    global b
    global phoneIP
    global pcIP
    global user
    '''
    import subprocess

    response=subprocess.getstatusoutput('ping -n 2 10.0.0.7')
    if 'time=' not in response[1]: #timeout, phone not present.
        print("Phone not present.")
        log.warning("Phone not present.")
        off()
        return
    '''
    if len(sys.argv) == 1:
        print("No arguments.")
        log.warning('No arguments.')
        return
    else:
        cmd=sys.argv[1].lower()
        usr=sys.argv[2].lower()

        if cmd in allcommands:
            if cmd in commands:
                if cmd != 'autoset' :
                    log.info(cmd)
                
                if usr=='richard':
                    user='richard'
                    b=[yeelight.Bulb(r_bed_stand), yeelight.Bulb(r_bed_desk)]
                    phoneIP="10.0.0.7"
                    pcIP="10.0.0.2"
                else: #make this elif then throw error w/ else 
                    #TODO Add vlad lights
                    #b=[vlad lights]
                    #phoneIP=
                    #pcIP=
                    return
                globals()[cmd]()
        elif cmd in ['bright','brightness']:
            if type(sys.argv[2]) == int:
                print("Changing brightness to %d"%int(sys.argv[2]))
                for i in b:
                    i.set_brightness(int(sys.argv[1]))
        else:
            print("Command \"%s\" not found"%cmd)


def autoset_auto():
    log.info("Boot autoset_auto")
    while True:
        phoneFound = checkPing()

        if not phoneFound: #Was 1, now 0
            off()
            with open('/home/richard/yeelight/log.log','a+') as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"))
                f.write(" :  off\n")
        else: #Was 0, now 1
            #with open(os.getcwd()+'/'+user+'_manualOverride.txt', 'r') as f:
            #    ld = f.read().strip()
            #if datetime.datetime.strptime(ld,'%Y-%m-%d %H:%M:%S') + datetime.timedelta(hours=1) > datetime.datetime.utcnow():
            #    continue

            on()
            autoset(autosetDuration=30)
            with open('/home/richard/yeelight/log.log','a+') as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"))
                f.write(" :  on\n")

phoneStatus=True
pcStatus=True

def checkPing():
    global phoneStatus
    global pcStatus
    if phoneStatus:
        sleepTime=7
    else:
        sleepTime=0.5
    attempts=0
    while True:
        phone_response = not bool(os.system("ping -c 1 -W 2 "+phoneIP))
        pc_response = not bool(os.system("ping -c 1 -W 2 "+pcIP))
        if (phone_response == phoneStatus) and (pc_response==pcStatus): #no changes
            time.sleep(sleepTime)
            attempts=0
            continue
        elif not phone_response: #phone is missing
            attempts+=1
            if attempts > 3: #try 3 times 
                log.info("Phone missing")
                pcStatus=pc_response
                phoneStatus=phone_response
                return False
        elif (not phoneStatus) and phone_response: #phone re-appears
            log.info("Phone re appeared")
            pcStatus=pc_response
            phoneStatus=phone_response
            return True
        elif phone_response and pc_response and not pcStatus: #if pc turns on
            log.info('PC turned on')
            pcStatus=pc_response
            phoneStatus=phone_response
            return True
        elif phoneStatus and pcStatus and not pc_response: #if pc turns off
            log.info("PC turned off")
            pcStatus=pc_response
            phoneStatus=phone_response
            return False

        '''
        if phone_response==phoneStatus:#Nothing Changed
            time.sleep(sleepTime)
            attempts=0
        else:
            if not phone_response: #Phone not found
                attempts += 1
                if attempts > 3:
                    #Phone not found
                    return False
            else: #Phone found
                return True

        '''





def sunrise():
    #Prevent autoset from taking over
    with open(os.getcwd()+'/'+user+'_manualOverride.txt', 'w+') as f:
        f.write(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
    overallDuration=1200000 #1200000 == 20 min
    on()
    for i in b:
        i.set_brightness(0)
        i.set_rgb(255, 0, 0)
    time.sleep(1)
    
    transitions = [yeelight.HSVTransition(hue=39, saturation=100,
                    duration=overallDuration * 0.5, brightness=80),
                   yeelight.TemperatureTransition(degrees=3200,
                    duration=overallDuration * 0.5, brightness=80)]
    
    for i in b:
        i.start_flow(yeelight.Flow(count=1,action=yeelight.Flow.actions.stay,transitions=transitions))

def brightness(val):
    for i in b:
        i.set_brightness(val)

def day(duration=3000,auto=False):
    if not auto:
        on()
    #3200
    colorTempFlow(__DAY_COLOR, duration, 80)

def dusk(duration=3000,auto=False):
    if not auto:
        on()
    #3000
    colorTempFlow(__DUSK_COLOR, duration, 80)
    
def night(duration=3000,auto=False):
    if not auto:
        on()
    colorTempFlow(__NIGHT_COLOR, duration, 80)

def sleep(duration=3000,auto=False):
    if not auto:
        on()
    colorTempFlow(__SLEEP_COLOR,duration,20)

def off():
    while all(x.get_properties()['power'] != 'off' for x in b):
        for i in b:
            i.turn_off()

def on():
    while all(x.get_properties()['power'] != 'on' for x in b):
        for i in b:
            i.turn_on()
        
def toggle():
    """
    Doesn't use the built in toggle command in yeelight as it sometimes fails to toggle one of the lights.
    """
    oldPower = desk.get_properties()['power']
    if oldPower == 'off':
        on()
    else:
        off()

def colorTempFlow(temperature=3200,duration=3000, brightness=80):
    #control all lights at once
    #makes things look more condensed
    transition=yeelight.TemperatureTransition(degrees=temperature,duration=duration,brightness=brightness)
    for i in b:
        i.start_flow(yeelight.Flow(count=1,
                                   action=yeelight.Flow.actions.stay,
                                   transitions=[transition]))


def lightTime():
    #TODO
    #set light level based on time of day, hour by hour to get smoother transition than day/dusk/night/sleep
    #day=datetime.time.
    #time.time()
    pass

def discoverBulbs():
    bulbs=yeelight.discover_bulbs()
    for bulb in bulbs:
        print(bulb)

        
def logon():
    on()
    autoset(autosetDuration=3000)
    return
    
def autoset(autosetDuration=300000):
    if all(x.get_properties()['power']=='off' for x in b):
        log.info('Power is off, cancelling autoset')
        return -1
    #Check if system tray has been used recently to override autoset
    with open(os.getcwd()+'/'+user+'_manualOverride.txt', 'r') as f:
        ld = f.read().strip()
    if datetime.datetime.strptime(ld,'%Y-%m-%d %H:%M:%S') + datetime.timedelta(hours=1) > datetime.datetime.utcnow():
        print("SystemTray used recently, canceling autoset")
        log.info("SystemTray used recently, canceling autoset")
        return -1
    
    
    #set light level when computer is woken up, based on time of day
    rn=datetime.datetime.now() # If there is ever a problem here, just use time.localtime()
    now=datetime.time(rn.hour,rn.minute,0)
    print('now:',now)
    
    dayrange = ["6:50:AM", "6:00:PM"]
    if time.localtime().tm_wday in [5, 6]: #weekend
        print("weekend")
        dayrange[0] = "8:30:AM"

    #TODO Remember to make changes to raspberry pi too!
    duskrange=[dayrange[1],"7:45:PM"]
    nightrange=[duskrange[1],"9:30:PM"]
    sleeprange=[nightrange[1],"11:00:PM"]
    DNDrange=[sleeprange[1],dayrange[0]]
    
    
    timeranges=[dayrange,duskrange,nightrange,sleeprange,DNDrange]
    
    for r in timeranges:
        for rr in range(0,2):
            t = datetime.datetime.strptime(r[rr], "%I:%M:%p")
            r[rr] = datetime.time(t.hour,t.minute,0)
            
    if dayrange[0] <= now < dayrange[1]:
        print("Day")
        log.info("Autoset: Day")
        day(autosetDuration,True)
    elif duskrange[0] <= now < duskrange[1]:
        print("Dusk")
        log.info("Autoset: Dusk")
        dusk(autosetDuration,True)
    elif nightrange[0] <= now < nightrange[1]:
        print("Night")
        log.info("Autoset: Night")
        night(autosetDuration,True)
    elif sleeprange[0] <= now < sleeprange[1]:
        print("Sleep")
        log.info("Autoset: Sleep")
        sleep(autosetDuration,True)
    elif DNDrange[0] <= now or now < DNDrange[1]:
        print("dnd")
        log.info("Autoset: dnd")
        off()
    return 0














if __name__ == "__main__":
    #Run the system tray app
    #run the python script
    main()
