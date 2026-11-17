"""Optional loopback HTTP adapter; the desktop uses Controller directly."""
from __future__ import annotations
import hmac, json, mimetypes, secrets, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .control import Controller

class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    def __init__(self, address, receiver, webroot):
        super().__init__(address, Handler)
        self.receiver = receiver; self.settings = receiver.settings; self.webroot = Path(webroot)
        self.token = secrets.token_urlsafe(32)
        self.controller = Controller(receiver, self.schedule_shutdown)
        self.operation_lock = self.controller.operation_lock
    def schedule_shutdown(self):
        threading.Thread(target=self.shutdown,daemon=True).start()
    @property
    def url(self): return f'http://127.0.0.1:{self.server_port}/'

class Handler(BaseHTTPRequestHandler):
    server_version = 'FlightRadar24/0.1'
    def log_message(self, *args): pass
    def send_body(self, status, data, mime='application/json; charset=utf-8', filename=None):
        if isinstance(data,(dict,list)): data = json.dumps(data,allow_nan=False).encode('utf-8')
        if isinstance(data,str): data = data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; connect-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if filename: self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        try: self.wfile.write(data)
        except (ConnectionError,OSError): pass
    def allowed(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
            self.send_body(403,{'error':'Invalid local host.'}); return False
        origin = self.headers.get('Origin')
        if origin and origin not in (self.server.url.rstrip('/'),f'http://localhost:{self.server.server_port}'):
            self.send_body(403,{'error':'Cross-site requests are not allowed.'}); return False
        if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.token):
            self.send_body(401,{'error':'Open the app using FlightRadar24.exe to reconnect this browser.'}); return False
        return True
    def send_result(self, result):
        return self.send_body(result.status,result.data,result.mime,result.filename)
    def do_GET(self):
        path = urlsplit(self.path).path
        if not path.startswith('/api/'):
            files = {'/':'index.html','/app.js':'app.js','/map.js':'map.js','/details.js':'details.js','/style.css':'style.css',
                     '/vendor/leaflet/leaflet.js':'vendor/leaflet/leaflet.js',
                     '/vendor/leaflet/leaflet.css':'vendor/leaflet/leaflet.css'}
            if path not in files: return self.send_body(404,{'error':'Not found.'})
            target = self.server.webroot/files[path]
            return self.send_body(200,target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        if self.allowed(): return self.send_result(self.server.controller.execute(self.path,'GET'))
    def do_POST(self):
        if not self.allowed(): return
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.send_body(415,{'error':'Use JSON requests.'})
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0<=size<=131072: return self.send_body(413,{'error':'Request too large.'})
            data = json.loads(self.rfile.read(size))
            # JSON null is not equivalent to the native controller's omitted body.
            if not isinstance(data,dict): return self.send_body(400,{'error':'Request must be an object.'})
        except (ValueError,TypeError) as error: return self.send_body(400,{'error':str(error)})
        return self.send_result(self.server.controller.execute(urlsplit(self.path).path,'POST',data))
