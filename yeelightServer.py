import pickle
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import argparse
import logging
import json
import os
from yeelightpython import __DAY_COLOR, __DUSK_COLOR, __NIGHT_COLOR, __SLEEP_COLOR
import datetime

HOMEDIR= '/home/richard/YeeLightServer/'
logger = logging.getLogger('serverLog')
logging.basicConfig(filename='/home/richard/YeeLightServer/serverLog.log',
                        filemode='a',
                        format = '%(asctime)s %(name)s %(levelname)s %(message)s',
                        datefmt='%Y-%m-%d %I:%M:%S%p',
                        level=logging.INFO)

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
                with open('/home/richard/YeeLightServer/'+ data['user'] +'_manualOverride.txt','w+') as f:
                    f.write(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
                with open('/home/richard/YeeLightServer/bulbStateLog','r+') as f:
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
                import base64
                with open(HOMEDIR + 'favicon.ico', 'rb') as f:
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
        except Exception:
            logger.error( 'Got exception ', exc_info=True)
            content='<html><head><title>Panel GUI</title></head>\n<body>GOT ERROR!\n</body></html>\n'

        return bytes(content, 'UTF-8')

    def respond(self, opts):
        response = self.handle_http(opts['status'], self.path)
        self.wfile.write(response)


def GET_panel():
    doc = []
    #PC/Phone status
    with open(HOMEDIR + 'bulbStateLog', 'r') as f:
        bulbState = json.load(f)

    def onlineOffline(key):
        return '<p style="color:green">Online</p>' if bulbState[key] else '<p style="color:red">Offline</p>'
    doc.append('''<table><tr><td>PC</td><td>Phone</td></tr><tr><td>%s</td><td>%s</td></tr></table>''' % (onlineOffline('pcStatus'),onlineOffline('phoneStatus')))

    #Bulb State
    currBulb = 'Bulb state: '
    if 'custom:' in bulbState['state']:
        currBulb += 'custom Temperature:%sK Brightness:%s' % (bublState['state'].split(':')[1:])
    else:
        currBulb += bulbState['state']
    doc.append(currBulb)

    
    with open(HOMEDIR + 'calcTimes.pickle', 'rb') as f:
        calcTimes = pickle.load(f)
    doc.append(calcTimes)
    with open(HOMEDIR + 'nightTimeRange.pickle', 'rb') as f:
        nightTimeRange = pickle.load(f)
    doc.append(nightTimeRange)
    with open(HOMEDIR + 'richard_manualOverride.txt') as f:
        manualOverrideTime = f.read()
    doc.append(manualOverrideTime)








    return doc















if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dev',action='store_true', default=False)
    args = parser.parse_args()
    
    
    if args.dev:
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
