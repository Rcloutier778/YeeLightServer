from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import signal
import platform
from yeelightLib import *

logger = getLogger()

def YeelightHTTP(event, cond, pipe):
    class YeelightHandler(BaseHTTPRequestHandler):
        
        def _set_headers(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
        
        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
        
        def do_POST(self):
            try:
                self.data_string = self.rfile.read(int(self.headers['Content-Length']))
                
                
                data = json.loads(self.data_string)
                logger.info(data)
                assert data['eventType'] in ('dashboard', 'manual')
                assert data['newState'] in bulbCommands + ['color', 'toggle']
                writeManualOverride()

                # Probably will need to pass in funky stuff for autoset and such
                room = data.get('room','global')
                assert room in list(room_to_ips) + ['global']
                data = {'room':room,
                        'action': data['newState'],
                        'eventType': data['eventType']
                        }
                pipe.send(data)
                event.set()
                with cond:
                    cond.notify()
                
                self.send_response(200)
                self.send_header('Content-type', 'json')
                self.end_headers()
            except:
                logger.exception("YeelightHTTP error")
                self.send_response(500)
                self.send_header('Content-type', 'json')
                self.end_headers()
    
    return YeelightHandler

def http_server(event, cond, pipe):
    logger.info('http_server')
    HOST_NAME = '10.0.0.2' if 'Windows' in platform.platform() else '10.0.0.17'
    PORT_NUMBER = 9001
    httpd = HTTPServer((HOST_NAME, PORT_NUMBER), YeelightHTTP(event, cond, pipe))
    logger.info('got httpd')
    def cleanup(*args, **kwargs):
        httpd.server_close()
    signal.signal(signal.SIGTERM, cleanup)
    logger.info('Starting server on %s:%d', HOST_NAME, PORT_NUMBER)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Error in server")
        raise
    cleanup()

