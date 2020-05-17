import pickle
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import argparse
import logging
import json
import html
import datetime
import matplotlib.pyplot as plt
import os
import os.path
import io
import base64
import platform
from yeelightpython import __DAY_COLOR, __DUSK_COLOR, __NIGHT_COLOR, __SLEEP_COLOR, __SUNRISE_TIME, __WEEKEND_SUNRISE_TIME, __SLEEP_TIME


HOMEDIR = '/home/richard/YeeLightServer' if 'Linux' in platform.platform() else os.getcwd()
lastPlot = None
#logging.basicConfig(filename=HOMEDIR+'serverLog.log',
#                    filemode='a+',
#                    format = '%(asctime)s %(levelname)s %(message)s',
#                    datefmt='%Y-%m-%d %I:%M:%S%p',
#                    level=logging.INFO,
#                    force=True)

logger = logging.getLogger('serverLog')
logger.setLevel(logging.INFO)
fh = logging.FileHandler(os.path.join(HOMEDIR, 'serverLog.log'), 'a+')
fh.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)
class MyHandler(BaseHTTPRequestHandler):
    def _set_headers(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def do_GET(self):
        paths = {
            '/panel': {'status': 200}
        }

        if self.path in paths:
            self.respond(paths[self.path])
        else:
            logger.info('Not in path')
            self.respond({'status': 500})
            
    def do_POST(self):
        self._set_headers()

        self.data_string = self.rfile.read(int(self.headers['Content-Length']))

        self.send_response(200)
        self.end_headers()
        
        print(self.data_string)
        data = json.loads(self.data_string)
        logger.info(data)
        try:
            if data["eventType"]=='manual':
                with open(os.path.join(HOMEDIR, data['user'] + '_manualOverride.txt'),'w+') as f:
                    f.write(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
                with open(os.path.join(HOMEDIR, 'bulbStateLog'),'r+') as f:
                    jdict = json.load(f)
                    jdict['state'] = data["newState"]
                    f.seek(0)
                    json.dump(jdict,f)
                    f.truncate()
        except Exception as e:
            logger.error(e)
        return
    
    def do_PUT(self): #Dont use
        #print('got put')
        #self._set_headers()
        #self.data_string = self.rfile.read(int(self.headers['Content-Length']))

        #self.send_response(200)
        #self.end_headers()
        #print("{}".format(self.data_string))
        return
     
    def handle_http(self, status_code, path):
        self.send_response(status_code)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        try:
            if path == '/panel':
                with open(os.path.join(HOMEDIR, 'favicon.ico'), 'rb') as f:
                    img = base64.b64encode(f.read()).decode()
                content = '''
<html><head>
<title>Panel GUI</title>
<link rel="icon" href="data:image/x-icon;base64,%s" type="image/x-icon"/>
</head>
<body>%%s
</body></html>\n''' % img
                doc = GET_panel()
                content = content % ('<br>\n'.join(str(x) for x in doc))
            else:
                content = '''
                <html><head><title>Title goes here.</title></head>
                <body><p>This is a test.</p>
                <p>badPath</p>
                </body></html>
                '''
        except Exception as e:
            logger.error( 'Got exception ', exc_info=True)
            content='<html><head><title>Panel GUI</title></head>\n<body>GOT ERROR!<br>%s\n</body></html>\n' % html.escape(e)

        return bytes(content, 'UTF-8')

    def respond(self, opts):
        response = self.handle_http(opts['status'], self.path)
        self.wfile.write(response)


def GET_panel():
    global lastPlot
    doc = []
    #PC/Phone status
    with open(os.path.join(HOMEDIR, 'bulbStateLog'), 'r') as f:
        bulbState = json.load(f)

    def onlineOffline(key):
        return '<p style="color:green">Online</p>' if bulbState[key] else '<p style="color:red">Offline</p>'
    doc.append('''<table><tr><td>PC</td><td>Phone</td></tr><tr><td>%s</td><td>%s</td></tr></table>''' % (onlineOffline('pcStatus'),onlineOffline('phoneStatus')))

    #Bulb State
    currBulb = 'Bulb state: %s Temperature: %sK Brightness: %s%%'
    stateTranslation = {'day': ('day',__DAY_COLOR, 80,), 'dusk':('dusk',__DUSK_COLOR,80,), 'night':('night',__NIGHT_COLOR, 60,), 'sleep':('sleep',__SLEEP_COLOR, 20,)}
    if 'custom:' in bulbState['state']:
        currBulb = currBulb % ('custom',bulbState['state'].split(':')[1:])
    elif bulbState['state'] in stateTranslation:
        currBulb = currBulb % stateTranslation[bulbState['state']]
    else:
        currBulb = bulbState['state']
    doc.append(currBulb)
    
    #Temperature plot
    temperaturePlotPic = os.path.join(HOMEDIR,'temperaturePlot.png')
    if not lastPlot or (datetime.datetime.today() - lastPlot).days > 3 or not os.path.exists(temperaturePlotPic):
        lastPlot = datetime.datetime.today()
        with open(os.path.join(HOMEDIR, 'calcTimes.pickle'), 'rb') as f:
            calcTimes = pickle.load(f)
        with open(os.path.join(HOMEDIR, 'nightTimeRange.pickle'), 'rb') as f:
            nightTimeRange = pickle.load(f)
        createPlot(nightTimeRange, calcTimes)
    doc.append('<img src="data:image/png;base64, %s">' % (base64.b64encode(open(temperaturePlotPic,'rb').read()).decode('utf-8')))

    with open(os.path.join(HOMEDIR, 'richard_manualOverride.txt')) as f:
        manualOverrideTime = datetime.datetime.strptime(f.read(), '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %I:%M:%S %p')
    doc.append(manualOverrideTime)
    
    return doc



def createPlot(nightTimeRange, calcTimes):

    __SUNSET_TIME = calcTimes['sunsetTime']

    dayrange = [__SUNRISE_TIME, __SUNSET_TIME]
    if time.localtime().tm_wday in [5,6]:
        dayrange[0] = __WEEKEND_SUNRISE_TIME
    nightrange = [dayrange[1], __SLEEP_TIME]
    DNDrange = [nightrange[1], dayrange[0]]
    for r in [dayrange, nightrange, DNDrange]:
        for rr in range(2):
            t = datetime.datetime.strptime(r[rr], "%I:%M:%p")
            r[rr] = datetime.time(t.hour, t.minute, 0)

    X, Y = [],[]
    for hr in range(24):
        for m in range(12):
            m*=5
            cur = datetime.time(hr, m, 0)
            X.append(cur.strftime("%I:%M:%p"))
            if dayrange[0] <= cur < dayrange[1]:
                Y.append(__DAY_COLOR)
            elif nightrange[0] <= cur < nightrange[1]:
                for (startTime, endTime, temperature, brightness) in nightTimeRange:
                    if startTime <= cur and cur < endTime:
                        Y.append(temperature)
                        break
            elif DNDrange[0] <= cur or cur < DNDrange[1]:
                Y.append(0)

    fig, ax = plt.subplots()
    plt.plot(X,Y)
    fig.savefig(os.path.join(HOMEDIR, 'temperaturePlot.png'), format='png', bbox_inches='tight')
    return

if __name__ == '__main__':
    if 'Windows' in platform.platform():
        HOST_NAME = '10.0.0.2'  #
        PORT_NUMBER = 9000
    else:
        HOST_NAME = '10.0.0.17'
        PORT_NUMBER = 9000
    
    
    server_class = HTTPServer
    httpd = server_class((HOST_NAME, PORT_NUMBER), MyHandler)
    logger.info('Server Starts - %s:%s' % (HOST_NAME, PORT_NUMBER))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logger.info('Server Stops - %s:%s' % (HOST_NAME, PORT_NUMBER))
