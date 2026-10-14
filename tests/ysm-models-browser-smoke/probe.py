"""Loopback-only browser fixture; not production account, YSM parser or MCEF proof."""
import os
import sys
from pathlib import Path
_dlls = []
for _directory in [Path(sys.executable).parent, Path(sys.executable).parent / 'DLLs', Path(sys.executable).parent / 'Library' / 'bin']:
    if _directory.is_dir() and hasattr(os, 'add_dll_directory'):
        _dlls.append(os.add_dll_directory(str(_directory)))
os.environ['BMC_SKIP_DOTENV'] = '1'
import base64
import hashlib
import json
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build' / 'owner-131-ysm-browser-smoke'
WEB = ROOT / 'server' / 'web'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
PUBLIC_BYTES = b'PUBLIC_FIXTURE_ONLY_NOT_A_REAL_YSM'
PUBLIC_HASH = hashlib.sha256(PUBLIC_BYTES).hexdigest()
models, requests = [], []
selected = None
lock = threading.Lock()

def model(name, visibility, verified=False):
    return {'id': 'm_' + str(len(models) + 1), 'name': name, 'size': 0, 'visibility': visibility,
            'validationStatus': 'verified' if verified else 'pending', 'syncStatus': 'staged' if verified else 'blocked',
            'deletedAt': None, 'canSelect': False, 'canDownload': verified, 'canRestore': False}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def user(self):
        cookies = SimpleCookie(self.headers.get('Cookie', ''))
        uid = cookies['fixture_uid'].value if 'fixture_uid' in cookies else '10000'
        assert uid in ('10000', '10001')
        return uid
    def json(self, body, status=200):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw))); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(raw)
    def gate(self):
        expected = self.headers.get('X-Muxi-Expected-UID')
        if expected and expected != self.user():
            requests.append({'path': self.path, 'method': self.command, 'status': 409, 'expectedUID': expected, 'actorUID': self.user(), 'bodyAccepted': False})
            self.json({'detail': 'account_changed'}, 409); return False
        return True
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/api/v1/auth/me': return self.json({'user': {'uid': self.user(), 'username': 'local-fixture', 'nickname': '本地测试账户'}})
        if path == '/api/v1/player/profile': return self.json({'user': {'uid': int(self.user())}, 'player': {'loginName': self.user()}})
        if path == '/api/v1/player/ysm/models':
            if not self.gate(): return
            return self.json({'models': models, 'selectedModelId': selected, 'limits': {'maxFileBytes': 67108864, 'maxUserBytes': 268435456, 'maxModels': 100}})
        if path == '/api/v1/ysm/models/public': return self.json({'models': [{'id': 'p_fixture', 'name': '公共夹具.ysm', 'size': len(PUBLIC_BYTES), 'visibility': 'public', 'validationStatus': 'verified', 'syncStatus': 'staged', 'canSelect': False, 'canDownload': True, 'deletedAt': None}]})
        if path.startswith('/api/v1/ysm/models/public/by-sha256/'):
            if not self.gate(): return
            hit = path.rsplit('/', 1)[-1] == PUBLIC_HASH
            requests.append({'path': path, 'method': 'GET', 'status': 200 if hit else 404})
            return self.json({'model': {'id': 'p_fixture'}} if hit else {'detail': 'not found'}, 200 if hit else 404)
        # Serve only the page's static files, never credentials or arbitrary project files.
        names = {'/ysm-models.html', '/ysm-models.js', '/ysm-models.css', '/styles.css', '/player-center.css', '/auth-nav.js', '/favicon.svg'}
        if path not in names: self.send_error(404); return
        raw = (WEB / path[1:]).read_bytes()
        mime = 'text/html' if path.endswith('.html') else 'text/css' if path.endswith('.css') else 'application/javascript' if path.endswith('.js') else 'image/svg+xml'
        self.send_response(200); self.send_header('Content-Type', mime + '; charset=utf-8'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def write(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self.gate(): return
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        request = {'path': parsed.path, 'method': self.command, 'expectedUID': self.headers.get('X-Muxi-Expected-UID'), 'actorUID': self.user(), 'bodyBytes': len(raw), 'status': 200}
        if self.command == 'POST':
            assert self.headers.get('Idempotency-Key') or parsed.path.endswith('/restore')
        with lock:
            if parsed.path == '/api/v1/player/ysm/models':
                args = urllib.parse.parse_qs(parsed.query)
                item = model(args['filename'][0], args['visibility'][0]); item['size'] = len(raw)
                assert hashlib.sha256(raw).hexdigest() == hashlib.sha256((OUT / 'local-private.ysm').read_bytes()).hexdigest()
                request['kind'] = 'raw-upload'; request['sha256'] = hashlib.sha256(raw).hexdigest(); models.append(item)
            elif parsed.path.endswith('/references'):
                body = json.loads(raw); assert body['publicId'] == 'p_fixture'
                item = model(body['filename'], body['visibility'], True); item['size'] = len(PUBLIC_BYTES); models.append(item)
                request['kind'] = 'reference'; assert len(raw) < 1024
            else:
                identifier = parsed.path.split('/')[6]
                item = next(item for item in models if item['id'] == identifier)
                if self.command == 'PATCH': item['visibility'] = json.loads(raw)['visibility']
                elif self.command == 'DELETE': item.update(deletedAt='2026-10-13T00:00:00Z', canRestore=True)
                elif parsed.path.endswith('/restore'): item.update(deletedAt=None, canRestore=False, visibility='private')
                else: raise AssertionError('Unexpected fixture route')
            requests.append(request)
            self.json({'model': item, 'selectedModelId': None})
    do_POST = write
    do_PATCH = write
    do_DELETE = write

class CDP:
    def __init__(self, port, path):
        self.socket = socket.create_connection(('127.0.0.1', port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: 127.0.0.1:' + str(port) + '\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        head = b''
        while not head.endswith(b'\r\n\r\n'): head += self.socket.recv(1)
        assert b'101' in head.split(b'\r\n')[0], head
        self.identifier = 0; self.events = []
    def exact(self, count):
        raw = b''
        while len(raw) < count:
            part = self.socket.recv(count - len(raw))
            if not part: raise EOFError('Browser WebSocket closed')
            raw += part
        return raw
    def send(self, message):
        raw = json.dumps(message).encode(); mask = os.urandom(4)
        header = bytes([0x81, 0x80 | len(raw)]) if len(raw) < 126 else bytes([0x81, 0xfe]) + struct.pack('!H', len(raw)) if len(raw) < 65536 else bytes([0x81, 0xff]) + struct.pack('!Q', len(raw))
        self.socket.sendall(header + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw)))
    def receive(self):
        first, second = self.exact(2); length = second & 127
        if length == 126: length = struct.unpack('!H', self.exact(2))[0]
        elif length == 127: length = struct.unpack('!Q', self.exact(8))[0]
        mask = self.exact(4) if second & 128 else None
        raw = self.exact(length)
        if mask: raw = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
        if first & 15 == 8: raise EOFError('Browser WebSocket closed')
        return json.loads(raw)
    def call(self, method, params=None, session=None):
        self.identifier += 1; identifier = self.identifier
        message = {'id': identifier, 'method': method, 'params': params or {}}
        if session: message['sessionId'] = session
        self.send(message)
        while True:
            result = self.receive()
            if result.get('id') != identifier: self.events.append(result); continue
            if 'error' in result: raise RuntimeError(result['error'])
            return result.get('result', {})

def main():
    assert str(OUT).startswith(str(ROOT / 'build'))
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'receipt.json').exists():
        (OUT / ('receipt-prior-' + str(int(time.time())) + '.json')).write_bytes((OUT / 'receipt.json').read_bytes())
    private_file = OUT / 'local-private.ysm'; private_file.write_bytes(b'LOCAL_FIXTURE_ONLY_NOT_A_REAL_YSM\n' * 65536)
    public_file = OUT / 'local-public.ysm'; public_file.write_bytes(PUBLIC_BYTES)
    profile = OUT / ('edge-profile-' + str(int(time.time())))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port) + '/ysm-models.html'
    log = (OUT / 'edge-output.log').open('wb')
    startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = 0
    process = subprocess.Popen([EDGE, '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-background-networking', '--disable-component-update', '--disable-sync', '--host-resolver-rules=MAP * 0.0.0.0, EXCLUDE 127.0.0.1, EXCLUDE localhost', 'about:blank'], stdout=log, stderr=log, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    receipt = {'scope': 'local Edge browser fixture only; no production account or MCEF', 'browserPID': process.pid, 'fixturePort': server.server_port, 'url': url, 'modelsAreParserFixturesOnly': True, 'skipDotenv': os.environ['BMC_SKIP_DOTENV'], 'passed': False}
    cdp = None
    try:
        deadline = time.monotonic() + 30
        while not (profile / 'DevToolsActivePort').exists():
            assert process.poll() is None, 'Own browser exited during startup'
            if time.monotonic() > deadline: raise TimeoutError('Own Edge debugging startup timed out')
            time.sleep(.1)
        port, ws_path = (profile / 'DevToolsActivePort').read_text().strip().splitlines()
        cdp = CDP(int(port), ws_path)
        receipt['browserVersion'] = cdp.call('Browser.getVersion')
        target = cdp.call('Target.createTarget', {'url': url})['targetId']
        session = cdp.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        cdp.call('Page.enable', session=session); cdp.call('Runtime.enable', session=session)
        def evaluate(expression):
            result = cdp.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True}, session)
            if 'exceptionDetails' in result: raise AssertionError(result['exceptionDetails'])
            return result.get('result', {}).get('value')
        def wait(expression):
            deadline = time.monotonic() + 20
            while not evaluate(expression):
                if time.monotonic() > deadline: raise TimeoutError(expression)
                time.sleep(.1)
        def files(paths):
            root = cdp.call('DOM.getDocument', session=session)['root']['nodeId']
            identifier = cdp.call('DOM.querySelector', {'nodeId': root, 'selector': '#ysm-files'}, session)['nodeId']
            cdp.call('DOM.setFileInputFiles', {'nodeId': identifier, 'files': [str(path) for path in paths]}, session)
        def click(expression): evaluate(expression + '.click()')
        wait("document.getElementById('ysm-private-content') && !document.getElementById('ysm-private-content').hidden")
        files([private_file, public_file]); wait("document.getElementById('ysm-upload-list').children.length===2")
        click("document.getElementById('ysm-upload-start')")
        wait("Array.from(document.querySelectorAll('#ysm-upload-list .ysm-state')).every(x=>/上传完成|引用已保存/.test(x.textContent))")
        wait("document.getElementById('ysm-my-list').children.length===2")
        assert len([r for r in requests if r.get('kind') == 'raw-upload']) == 1
        assert len([r for r in requests if r.get('kind') == 'reference']) == 1
        assert all(item['visibility'] == 'private' for item in models)
        evaluate("document.querySelector('#ysm-my-list select').value='public';document.querySelector('#ysm-my-list select').dispatchEvent(new Event('change'))")
        wait("document.getElementById('ysm-confirm').open"); click("document.getElementById('ysm-confirm-ok')")
        wait("document.querySelector('#ysm-my-list select').value==='public'")
        def screenshot(name, width, height):
            cdp.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': width < 500}, session)
            time.sleep(.2)
            layout = evaluate("({viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,title:document.querySelector('h1').textContent,items:document.querySelectorAll('#ysm-my-list>li').length})")
            assert layout['scrollWidth'] <= layout['viewport'], layout
            raw = cdp.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True}, session)['data']
            (OUT / name).write_bytes(base64.b64decode(raw)); return layout
        receipt['desktopLayout'] = screenshot('desktop.png', 1280, 960)
        receipt['mobileLayout'] = screenshot('mobile-390.png', 390, 844)
        click("Array.from(document.querySelectorAll('#ysm-my-list button')).find(x=>x.textContent==='删除')")
        wait("document.getElementById('ysm-confirm').open"); click("document.getElementById('ysm-confirm-ok')")
        wait("document.getElementById('ysm-my-list').children.length===1")
        click("document.getElementById('ysm-show-deleted')"); wait("document.getElementById('ysm-my-list').children.length===2")
        click("Array.from(document.querySelectorAll('#ysm-my-list button')).find(x=>x.textContent==='恢复为私有')")
        wait("!Array.from(document.querySelectorAll('#ysm-my-list button')).some(x=>x.textContent==='恢复为私有')")
        assert models[0]['deletedAt'] is None and models[0]['visibility'] == 'private'
        cdp.call('Network.setCookie', {'name': 'fixture_uid', 'value': '10001', 'url': url, 'path': '/'}, session)
        files([private_file]); wait("document.getElementById('ysm-upload-list').children.length===3")
        click("document.getElementById('ysm-upload-start')")
        wait("document.getElementById('ysm-private-content').hidden && document.getElementById('ysm-upload-list').children.length===0")
        assert any(r.get('status') == 409 and r.get('expectedUID') == '10000' and r.get('actorUID') == '10001' for r in requests)
        assert len([r for r in requests if r.get('kind') == 'raw-upload']) == 1
        receipt['accountSwitchNotice'] = evaluate("document.getElementById('ysm-message').textContent")
        receipt['requests'] = requests
        receipt['runtimeExceptions'] = [event for event in cdp.events if event.get('method') == 'Runtime.exceptionThrown']
        assert not receipt['runtimeExceptions']
        receipt['passed'] = True
    except BaseException as error:
        receipt['error'] = repr(error)
        raise
    finally:
        if cdp:
            try: cdp.call('Browser.close')
            except (EOFError, OSError): pass
            cdp.socket.close()
        try: process.wait(timeout=20); receipt['normalBrowserExit'] = True; receipt['browserExitCode'] = process.returncode
        except subprocess.TimeoutExpired: receipt['normalBrowserExit'] = False; receipt['remainingOwnPID'] = process.pid
        server.shutdown(); server.server_close(); thread.join(timeout=5); log.close()
        receipt['fixtureStopped'] = not thread.is_alive()
        receipt['finishedAtUTC'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        (OUT / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': receipt['passed'], 'normalBrowserExit': receipt['normalBrowserExit'], 'fixtureStopped': receipt['fixtureStopped'], 'evidence': str(OUT)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
