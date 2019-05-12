import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import argparse
import json
import os
import datetime

class MyHandler(BaseHTTPRequestHandler):
    def _set_headers(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
    def do_HEAD(self):
        print('do head')
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def do_GET(self):
        print('do get')
        paths = {
            '/foo': {'status': 200},
            '/bar': {'status': 302},
            '/baz': {'status': 404},
            '/qux': {'status': 500}
        }

        if self.path in paths:
            self.respond(paths[self.path])
            
        else:
            self.respond({'status': 500})
            
    def do_POST(self):
        print('got post')
        self._set_headers()

        self.data_string = self.rfile.read(int(self.headers['Content-Length']))

        self.send_response(200)
        self.end_headers()
        
        data = json.loads(self.data_string)
        print(data)
        try:
            if data["eventType"]=='manual':
                with open('/home/richard/yeelight/' + data['user'] +'_manualOverride.txt','w+') as f:
                    f.write(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
        except Exception as e:
            print(e)
        return
    
    def do_PUT(self): #Dont use
        print('ignoring put')
        #print('got put')
        #self._set_headers()
        #self.data_string = self.rfile.read(int(self.headers['Content-Length']))

        #self.send_response(200)
        #self.end_headers()
        #print("{}".format(self.data_string))
        return
    
    def handle_http(self, status_code, path):
        print('handle')
        self.send_response(status_code)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        content = '''
        <html><head><title>Title goes here.</title></head>
        <body><p>This is a test.</p>
        <p>You accessed path: {}</p>
        </body></html>
        '''.format(path)
        return bytes(content, 'UTF-8')

    def respond(self, opts):
        print('respond')
        response = self.handle_http(opts['status'], self.path)
        self.wfile.write(response)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dev',action='store_true', default=False)
    args = parser.parse_args()
    
    
    if args.dev:
        HOST_NAME = 'localhost'  #
        PORT_NUMBER = 8080
    else:
        HOST_NAME = '10.0.0.17'
        PORT_NUMBER = 9000
    
    server_class = HTTPServer
    httpd = server_class((HOST_NAME, PORT_NUMBER), MyHandler)
    print(time.asctime(), 'Server Starts - %s:%s' % (HOST_NAME, PORT_NUMBER))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    print(time.asctime(), 'Server Stops - %s:%s' % (HOST_NAME, PORT_NUMBER))