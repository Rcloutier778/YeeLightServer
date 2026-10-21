from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import json
import signal
import sys
import platform
import time
import urllib.parse
import queue
import threading
from yeelightLib import *

logger = None


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Serve persistent SSE connections without blocking ordinary REST requests."""
    daemon_threads = True
    allow_reuse_address = True


class SSEHub:
    """Fan out the latest JSON snapshot to each connected SSE client."""
    def __init__(self):
        self._lock = threading.Lock()
        self._clients = set()
        self._latest = '{}'

    @staticmethod
    def _offer(client_queue, message):
        # Keep streams bounded: for slow clients, the newest full snapshot is
        # more useful than a backlog of obsolete intermediate snapshots.
        try:
            client_queue.put_nowait(message)
            return
        except queue.Full:
            pass
        try:
            client_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            client_queue.put_nowait(message)
        except queue.Full:
            pass

    def publish(self, snapshot):
        message = json.dumps(snapshot, separators=(',', ':'))
        with self._lock:
            if message == self._latest:
                return
            self._latest = message
            clients = list(self._clients)
        for client_queue in clients:
            self._offer(client_queue, message)

    def subscribe(self):
        client_queue = queue.Queue(maxsize=8)
        with self._lock:
            self._clients.add(client_queue)
            latest = self._latest
        return client_queue, latest

    def unsubscribe(self, client_queue):
        with self._lock:
            self._clients.discard(client_queue)


def _relay_status_updates(status_push_queue, sse_hub):
    """Move snapshots from the bulb-control process to HTTP client streams."""
    while True:
        try:
            snapshot = status_push_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        except (EOFError, OSError):
            return
        try:
            if isinstance(snapshot, dict):
                sse_hub.publish(snapshot)
        except Exception:
            if logger is not None:
                logger.exception('Could not publish SSE status snapshot')


def YeelightHTTP(event, cond, pipe, sse_hub=None):
    # Multiple SSE connections require a threaded HTTP server. Serialize the
    # existing request/response pipe for property reads so concurrent readers
    # cannot consume each other's replies.
    property_pipe_lock = threading.Lock()
    def getProperty(room, *properties):
        data = {
            'room': room,
            'eventType': 'dashboard-query',
            'query': 'getProperty',
            'properties': properties
        }
        with property_pipe_lock:
            with cond:
                pipe.send(data)
                event.set()
                cond.notify()
            if pipe.poll(10):
                res = pipe.recv()
                return json.dumps(res)
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
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.rstrip('/') == '/events':
                self.do_SSE()
                return

            paths = {
                'property': [getProperty, 'application/json'],
            }
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

        def do_SSE(self):
            if sse_hub is None:
                self.respond(503, 'Status stream is unavailable', 'text/plain; charset=utf-8')
                return

            client_queue, initial_snapshot = sse_hub.subscribe()
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-transform')
                self.send_header('Connection', 'keep-alive')
                self.send_header('X-Accel-Buffering', 'no')
                self.end_headers()

                self.wfile.write(('data: %s\n\n' % initial_snapshot).encode('utf-8'))
                self.wfile.flush()
                while True:
                    try:
                        message = client_queue.get(timeout=15.0)
                        frame = 'data: %s\n\n' % message
                    except queue.Empty:
                        frame = ': keep-alive\n\n'
                    self.wfile.write(frame.encode('utf-8'))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                # The Android client normally disconnects when the app changes
                # foreground/background mode or loses its allowed Wi-Fi network.
                pass
            finally:
                sse_hub.unsubscribe(client_queue)

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
                with cond:
                    pipe.send(pipe_data)
                    event.set()
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



def http_server(event, cond, pipe, status_push_queue=None):
    global logger
    logger = getLogger()
    setprocname('http_server')
    HOST_NAME = '10.0.0.2' if 'Windows' in platform.platform() else '10.0.0.18'
    sse_hub = SSEHub()
    if status_push_queue is not None:
        threading.Thread(
            target=_relay_status_updates,
            args=(status_push_queue, sse_hub),
            name='sse-status-relay',
            daemon=True,
        ).start()
    httpd = ThreadedHTTPServer(
        (HOST_NAME, REST_SERVER_PORT_NUMBER),
        YeelightHTTP(event, cond, pipe, sse_hub),
    )
    
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
            # Avoid a tight restart loop if serve_forever repeatedly fails.
            time.sleep(0.5)

    cleanup()

