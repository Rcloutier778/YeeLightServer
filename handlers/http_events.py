from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import signal
import sys
import platform
import urllib.parse
from yeelightLib import *

logger = None

def YeelightHTTP(event, cond, pipe):
    def getProperty(room, *properties):
        data = {
            'room': room,
            'eventType': 'dashboard-query',
            'query': 'getProperty',
            'properties': properties
        }
        pipe.send(data)
        event.set()
        with cond:
            cond.notify()
        if pipe.poll(10):
            res = pipe.recv()
            return json.dumps(res)
        else:
            raise RuntimeError("Didn't receive answer in time")
    
    class YeelightHandler(BaseHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            setprocname('http_server')
            self.data_string = b''
            super(YeelightHandler, self).__init__(*args, **kwargs)

        def _set_headers(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

        def handle_http(self, status_code, path, content_type='text/html'):
            self.send_header('Content-type', content_type)
            self.end_headers()
            content = ''
    
            return bytes(content, 'UTF-8')
        
        def respond(self, status_code, contents, content_type='text/html'):
            if not isinstance(contents, bytes):
                if isinstance(contents, str):
                    response = bytes(contents, 'UTF-8')
                else:
                    try:
                        response = bytes(contents)
                    except Exception:
                        response = b'ERROR converting content!'
                        status_code = 500
                        content_type = 'text/html'
            else:
                response = contents
            self.send_response(status_code)
            self.send_header('Content-type', content_type)
            self.end_headers()
            self.wfile.write(response)
        
        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
        # Info queries
        def do_GET(self):
            paths = {
                'property': [getProperty, 'application/json'],
            }

            parsed = urllib.parse.urlsplit(self.path)
            path_parts = [urllib.parse.unquote(part) for part in parsed.path.split('/') if part]

            if path_parts and path_parts[0] in paths:
                func, content_type = paths[path_parts[0]]
                args = path_parts[1:]
                try:
                    status_code = 200
                    # getProperty is defined as (room, *properties), so pass
                    # path components positionally instead of query-string
                    # keyword arguments (which previously caused TypeError).
                    content = func(*args)
                except Exception as e:
                    status_code = 500
                    content = 'ERROR: %s' % str(e)
                    content_type = 'text/html'
                self.respond(status_code, content, content_type)
            else:
                self.respond(500, 'Not a valid path')

        # Actions
        def do_POST(self):
            try:
                logger.info("In POST http request")
                self.data_string = self.rfile.read(int(self.headers['Content-Length']))
                data = json.loads(self.data_string)
                logger.info(data)
                assert data['eventType'] in ('dashboard', HTTP_EVENT_FROM_PC, 'zigbee', 'zigbeeSwitch')
                if data['eventType'] == 'dashboard':
                    data['eventType'] += '-action'
                assert data['newState'] in bulbCommands

                # Probably will need to pass in funky stuff for autoset and such
                room = data.get('room')
                room = room or 'global'
                assert room in list(room_to_ips) + ['global']

                #writeManualOverride(room if room != 'global' else None)

                pipe_data = {'room':room,
                    'action': data['newState'],
                    'eventType': data['eventType']
                }
                if 'room' in data:
                    del data['room']
                del data['newState']
                del data['eventType']
                pipe_data['kwargs'] = data
                pipe.send(pipe_data)
                event.set()
                with cond:
                    cond.notify()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
            except Exception:
                logger.exception("YeelightHTTP error")
                logger.error(self.data_string)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
    
    
    return YeelightHandler



def http_server(event, cond, pipe):
    global logger
    logger = getLogger()
    setprocname('http_server')
    HOST_NAME = '10.0.0.2' if 'Windows' in platform.platform() else '10.0.0.18'
    httpd = HTTPServer((HOST_NAME, REST_SERVER_PORT_NUMBER), YeelightHTTP(event, cond, pipe))
    
    def cleanup(*args, **kwargs):
        httpd.server_close()
        logger.info('http-server closed')
        sys.exit(0)
        
    signal.signal(signal.SIGTERM, cleanup)
    logger.info('Starting server on %s:%d', HOST_NAME, REST_SERVER_PORT_NUMBER)
    while True:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        except Exception:
            logger.exception("Error in server")

    cleanup()

