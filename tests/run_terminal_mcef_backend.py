"""Local TLS services for actual MCEF transport and account API QA.

Uses synthetic accounts/keys and actual HTTP clients, ASGI apps and SQLite stores.
Minecraft listeners use explicit fixtures, not a real game or CEF renderer. Native
proofs/tickets stay in captured pipes/memory and never enter the printed report.
"""
import argparse
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import uuid
import httpx
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
ROOT = Path(__file__).resolve().parents[1]

def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def package(name, path):
    spec = importlib.util.spec_from_file_location(name, path / '__init__.py', submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

def run(args):
    checks = []
    shared_games = getattr(args, 'shared_games', False)
    if shared_games and not args.native:
        raise ValueError('Shared games requires actual native mode')

    def check(value, name):
        if not value:
            raise AssertionError(name)
        checks.append(name)
    with tempfile.TemporaryDirectory(prefix='dedicated-sso-loopback-') as directory:
        tmp = Path(directory)
        # Minecraft's actual JVM prefers IPv6/system order; Uvicorn binds only 127.0.0.1.
        # Pin native transport to that exact listener, preserving TLS SAN/issuer checks.
        auth_url = f'https://{"127.0.0.1" if args.native else "localhost"}:{port()}'
        site_url = f'https://localhost:{port()}'
        terminal_key = 'synthetic-terminal-server-key-32-long'
        profile_key = 'synthetic-identity-server-key-32-long'
        social_key = 'synthetic-dedicated-social-key-32-long'
        web_key = 'synthetic-confidential-web-key-32-long'
        os.environ.update({'MUXI_DATABASE_PATH': str(tmp / 'auth.db'), 'MUXI_SIGNING_KEY_PATH': str(tmp / 'oidc.pem'), 'MUXI_ISSUER': auth_url, 'MUXI_TERMINAL_SSO_ENABLED': '1', 'MUXI_TERMINAL_SSO_SERVER_KEY': terminal_key, 'MUXI_MC_PROFILE_KEY': profile_key, 'MUXI_BMC_WEB_CLIENT_SECRET': web_key, 'BMC_DATABASE_PATH': str(tmp / 'website.db'), 'BMC_AUTH_ISSUER': auth_url, 'BMC_AUTH_CLIENT_SECRET': web_key, 'BMC_PUBLIC_URL': 'https://mc.muxigame.com', 'BMC_SERVE_WEB': '1', 'BMC_TERMINAL_SSO_ENABLED': '1'})
        if shared_games:
            import secrets
            game_key = secrets.token_urlsafe(48)
            social_key = secrets.token_urlsafe(48)
            os.environ.update(BMC_GAME_PLATFORM_ENABLED='1', BMC_GAME_SERVICE_KEY=game_key,
                BMC_GAME_SOCIAL_ENABLED='1', BMC_GAME_SOCIAL_KEY=social_key,
                MUXI_GAME_ADMISSION_ENABLED='1', MUXI_GAME_PLATFORM_URL=site_url,
                MUXI_GAME_PLATFORM_KEY=game_key, BMC_MINIGAME_REWARDS_JSON='{}')
        package('dedicated_auth', ROOT.parent / 'muxi-auth/app')
        package('dedicated_site', ROOT / 'server/app')
        from dedicated_auth import main as auth
        from dedicated_auth.security import pkce_s256, token_hash, utc_now
        from dedicated_auth.store import iso
        from dedicated_site import main as site
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Isolated SSO loopback fixture')])
        now = datetime.now(timezone.utc)
        cert = x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1)).add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.DNSName('mc.muxigame.com'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False).sign(key, hashes.SHA256())
        (tmp / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (tmp / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context = ssl.create_default_context(cafile=str(tmp / 'cert.pem'))
        suffix = '.exe' if os.name == 'nt' else ''
        subprocess.run([str(args.java_home / 'bin' / ('keytool' + suffix)), '-importcert', '-alias', 'isolated-loopback', '-file', str(tmp / 'cert.pem'), '-keystore', str(tmp / 'trust.p12'), '-storetype', 'PKCS12', '-storepass', 'isolated-synthetic', '-noprompt'], check=True, capture_output=True)
        ssl_patch = patch('ssl._create_default_https_context', return_value=context)
        ssl_patch.start()
        servers = []
        threads = []
        native = None
        stderr = None
        api = None
        browser = None
        request_log=[]
        auth_log=[]
        async def tracked_auth(scope,receive,send):
            delayed=scope.get("path","").endswith("terminal-ticket") and state.get("delay_ticket_once",False)
            if delayed:
                state["delay_ticket_once"]=False;state["pending_tickets"]+=1
                await asyncio.sleep(1.5)
            captured=bytearray()
            async def tracked_receive():
                message=await receive()
                if scope.get('path','').endswith(('terminal-ticket','terminal-disconnect')) and message['type']=='http.request':captured.extend(message.get('body',b''))
                return message
            async def tracked_send(message):
                if scope['type']=='http' and message['type']=='http.response.start':
                    row={'method':scope['method'],'path':scope['path'],'status':message['status']}
                    if captured:
                        try:
                            body=json.loads(captured);row['uid']=body.get('uid');row['gameSession']=body.get('gameSession');row['requestId']=body.get('requestId')
                        except (ValueError,TypeError):pass
                    auth_log.append(row)
                await send(message)
            try:await auth.app(scope,tracked_receive,tracked_send)
            finally:
                if delayed:state["pending_tickets"]-=1
        async def tracked_site(scope,receive,send):
            async def tracked_send(message):
                if scope['type']=='http' and message['type']=='http.response.start':
                    canonical_redirect=False
                    if args.native:
                        # The real website uses its issuer for both internal HTTP and front-channel redirects.
                        # Keep internal TLS loopback; expose the same canonical redirect host as production.
                        headers=[]
                        for name,value in message.get('headers',[]):
                            prefix=(auth_url+'/oauth/authorize?').encode('ascii')
                            if name.lower()==b'location' and value.startswith(prefix):
                                value=b'https://account.muxigame.com/oauth/authorize?'+value[len(prefix):]
                                canonical_redirect=True
                            headers.append((name,value))
                        message={**message,'headers':headers}
                    request_log.append({'method':scope['method'],'path':scope['path'],'status':message['status'],'cef_cookie':any(k.lower()==b'cookie' for k,v in scope.get('headers',[])),'canonical_auth_redirect':canonical_redirect})
                await send(message)
            await site.app(scope,receive,tracked_send)
        try:
            for app, url in ((tracked_auth, auth_url), (tracked_site, site_url)):
                server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=int(url.rsplit(':', 1)[1]), log_level='critical', access_log=False, lifespan='off', ssl_certfile=str(tmp / 'cert.pem'), ssl_keyfile=str(tmp / 'key.pem')))
                t = threading.Thread(target=server.run, daemon=True)
                servers.append(server)
                threads.append(t)
                t.start()
            deadline = time.monotonic() + 12
            while not all((s.started for s in servers)):
                if time.monotonic() > deadline:
                    raise AssertionError('Isolated TLS startup failed')
                time.sleep(0.02)
            api = httpx.Client(base_url=auth_url, verify=context, trust_env=False, timeout=6)
            browser = httpx.Client(base_url=site_url, verify=context, trust_env=False, timeout=6, follow_redirects=False)
            check(api.get('/healthz').status_code == 200 and browser.get('/healthz').status_code == 200, 'Both actual TLS services healthy')
            accounts = []
            access = []
            credentials = []
            for n in range(2):
                account, verify = auth.store.register(f'synthetic-{n}@example.com', f'Synthetic_{n}', 'Synthetic Player', 'synthetic-test-password-only')
                account = auth.store.verify_email(verify)
                accounts.append(account)
                token = auth.store.issue_access_token(account.id, auth.settings.bmc_launcher_client_id, 'openid profile', 3600)
                access.append(token)
                response = api.post('/api/launcher/minecraft/terminal-bootstrap', headers={'Authorization': 'Bearer ' + token})
                check(response.status_code == 200 and response.json()['uid'] == account.uid, 'Launcher bootstrap binds its account UID')
                credentials.append(response.json()['credential'])

            if shared_games:
                # Same Auth accounts and Web database for A/B. Register through
                # authenticated application APIs, never seed platform/social rows.
                for n in range(2):
                    headers={'Authorization':'Bearer '+access[n]}
                    profile=browser.get('/api/v1/player/profile',headers=headers)
                    check(profile.status_code==200 and str(profile.json()['player']['uid'])==str(accounts[n].uid), 'Shared profile matches actual Auth UID')
                    check(browser.get('/api/v1/player/social',headers=headers).status_code==200, 'Shared social actor registered through authenticated API')
                response=browser.put('/api/v1/player/social/requests/'+str(accounts[1].uid),json={},headers={'Authorization':'Bearer '+access[0],'Idempotency-Key':str(uuid.uuid4())})
                check(response.status_code==200, 'A sends B friend request through actual API')
                response=browser.post('/api/v1/player/social/requests/'+str(accounts[0].uid)+'/accept',json={},headers={'Authorization':'Bearer '+access[1],'Idempotency-Key':str(uuid.uuid4())})
                check(response.status_code==200, 'B accepts A friend request through actual API')

            def proof(n):
                verifier = 'v' * 43
                request_id = str(uuid.uuid4())
                response = api.post('/api/launcher/minecraft/terminal-proof', json={'challenge': pkce_s256(verifier), 'requestId': request_id}, headers={'Authorization': 'MuxiTerminal ' + credentials[n]})
                check(response.status_code == 200, 'Issuer creates legal PKCE terminal proof')
                return (response.json()['proof'], verifier, request_id)
            def command(op,**body):
                raise AssertionError('Synthetic listener command forbidden in real-native mode')
            if not args.native:
                java_log = args.report.with_suffix('.java.log')
                java_log.parent.mkdir(parents=True, exist_ok=True)
                stderr = java_log.open('w', encoding='utf-8')
                native_command = [sys.executable, str(ROOT.parent / 'muxi-game-core/tests/run_terminal_sso_trust.py'), '--java-home', str(args.java_home), '--gson', str(args.gson), '--trusted-source', str(args.trusted_source), '--truststore', str(tmp / 'trust.p12'), '--serve']
                remote_certificate = None
                native = subprocess.Popen(native_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, encoding='utf-8', bufsize=1)
                replies = queue.Queue()

                def read_native():
                    for line in native.stdout:
                        replies.put(line)
                    replies.put(None)
                threading.Thread(target=read_native, daemon=True).start()

                def command(op, **body):
                    native.stdin.write(json.dumps({'op': op, **body}) + '\n')
                    native.stdin.flush()
                    line = replies.get(timeout=18)
                    if line is None:
                        raise AssertionError('Native fixture exited before reply; inspect sanitized Java error log')
                    return json.loads(line)
                status = command('configure', endpoint=auth_url + '/api/internal/minecraft/', key=terminal_key, uid=accounts[0].uid)
            # Keep actual Auth/Core/website TLS services alive behind a QA-only CEF transport.
            # Minecraft server listeners remain deterministic fixtures. No browser borrows an HTTP client's cookie jar.
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            import secrets
            from urllib.parse import urlparse
            control_secret=secrets.token_urlsafe(32)
            launcher_secret=secrets.token_urlsafe(32)
            lock=threading.Lock()
            state={'selected':0,'version':0,'delay_ticket_once':False,'pending_tickets':0,'revoked':False,'last_ticket':0.0,'website_requests':request_log,'refreshes':0}
            refresh_tokens=[auth.store.issue_refresh_token(a.id,auth.settings.bmc_launcher_client_id,'openid profile',30) for a in accounts]

            def bootstrap():
                n=state['selected']
                # A revoked source must fail actual bootstrap/refresh endpoints.
                response=api.post('/api/launcher/minecraft/terminal-bootstrap',headers={'Authorization':'Bearer '+access[n]})
                if response.status_code==401:
                    result=api.post('/oauth/token',data={'grant_type':'refresh_token','client_id':auth.settings.bmc_launcher_client_id,'refresh_token':refresh_tokens[n]})
                    if result.status_code!=200:return None
                    access[n]=result.json()['access_token'];refresh_tokens[n]=result.json()['refresh_token'];state['refreshes']+=1
                    response=api.post('/api/launcher/minecraft/terminal-bootstrap',headers={'Authorization':'Bearer '+access[n]})
                return response.json()['credential'] if response.status_code==200 else None

            class Control(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_POST(self):
                    launcher_allowed=self.headers.get('X-Muxi-Launcher-QA-Control')==launcher_secret
                    if not launcher_allowed and self.headers.get('X-Muxi-QA-Control')!=control_secret:
                        self.send_response(403);self.end_headers();return
                    try:
                        size=int(self.headers.get('Content-Length','0'))
                        if size>2*1024*1024:raise ValueError()
                        request=json.loads(self.rfile.read(size))
                        with lock:
                            action=request['action']
                            launcher_n=request.get('accountIndex',state['selected']) if shared_games else state['selected']
                            if type(launcher_n) is not int or launcher_n not in (0,1):raise ValueError('Invalid isolated account selector')
                            if action.startswith('launcher-') and not launcher_allowed:
                                self.send_response(403);self.end_headers();return
                            if launcher_allowed and action not in ('launcher-state','launcher-session','launcher-rotated'):
                                self.send_response(403);self.end_headers();return
                            if action=='launcher-state':result={'uid':accounts[launcher_n].uid,'version':state['version'],'revoked':state['revoked']}
                            elif action=='launcher-session':
                                n=launcher_n;result={'uid':accounts[n].uid,'version':state['version'],'revoked':state['revoked'],'access_token':access[n],'refresh_token':refresh_tokens[n]}
                            elif action=='launcher-rotated':
                                n=launcher_n
                                if str(request.get('uid'))==str(accounts[n].uid):access[n]=request['access_token'];refresh_tokens[n]=request['refresh_token'];state['refreshes']+=1
                                result={'updated':True}
                            elif action=='ticket':
                                if args.native:raise AssertionError('Fixture ticket delivery forbidden in native mode')
                                credential=bootstrap() if str(request.get('gameUid'))==str(accounts[state['selected']].uid) else None
                                if not credential:result={'payload':''}
                                else:
                                    wait=2.1-(time.monotonic()-state['last_ticket'])
                                    if wait>0:time.sleep(wait)
                                    verifier=secrets.token_urlsafe(32);rid=str(uuid.uuid4())
                                    proof_response=api.post('/api/launcher/minecraft/terminal-proof',json={'challenge':pkce_s256(verifier),'requestId':rid},headers={'Authorization':'MuxiTerminal '+credential})
                                    if proof_response.status_code!=200:result={'payload':''}
                                    else:
                                        ticket=command('request',proof=proof_response.json()['proof'],requestId=rid)['ticket'];state['last_ticket']=time.monotonic()
                                        result={'payload':json.dumps({'ticket':ticket,'verifier':verifier,'requestId':rid}) if ticket else ''}
                            elif action=='validate':
                                if args.native:raise AssertionError('Fixture validation forbidden in native mode')
                                credential=bootstrap() if str(request.get('gameUid'))==str(accounts[state['selected']].uid) else None
                                valid=False
                                if credential:
                                    verifier=secrets.token_urlsafe(32)
                                    proof_response=api.post('/api/launcher/minecraft/terminal-proof',json={'challenge':pkce_s256(verifier),'requestId':str(uuid.uuid4())},headers={'Authorization':'MuxiTerminal '+credential})
                                    valid=proof_response.status_code==200
                                result={'valid':valid}
                            elif action=='proxy':
                                target=urlparse(request['url'])
                                if target.scheme!='https' or target.hostname!='mc.muxigame.com' or target.port not in (None,443):raise ValueError()
                                headers={k:v for k,v in request.get('headers',{}).items() if k.lower() not in ('host','content-length','accept-encoding','connection')}
                                # A new client per request prevents an implicit cookie jar from defeating CEF identity isolation.
                                with httpx.Client(base_url=site_url,verify=context,trust_env=False,timeout=10,follow_redirects=False) as transport:
                                    response=transport.request(request['method'],target.path+('?' + target.query if target.query else ''),headers=headers,content=base64.b64decode(request.get('body','')))
                                state['website_requests'].append({'method':request['method'],'path':target.path,'status':response.status_code,'cef_cookie':bool(headers.get('Cookie') or headers.get('cookie'))})
                                result={'status':response.status_code,'headers':{k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')},'body':base64.b64encode(response.content).decode()}
                            elif action=='hold-ticket':state['delay_ticket_once']=True;result={'armed':True}
                            elif action=='switch-account':
                                state['selected']=1;state['version']+=1;state['revoked']=False
                                if not args.native:command('configure',endpoint=auth_url+'/api/internal/minecraft/',key=terminal_key,uid=accounts[1].uid)
                                result={'uid':accounts[1].uid,'old_native_denied':True}
                            elif action=='expire-access':
                                with auth.store.connect() as db:db.execute('UPDATE access_tokens SET expires_at=? WHERE account_id=?',(iso(utc_now()-timedelta(seconds=1)),accounts[state['selected']].id))
                                result={'expired':True}
                            elif action=='expire-session':
                                with site.web_auth_store.connect() as db:db.execute('UPDATE oidc_web_sessions SET expires_at=?',(iso(utc_now()-timedelta(seconds=1)),))
                                result={'expired':True}
                            elif action=='revoke':
                                n=state['selected'];api.post('/oauth/revoke',data={'client_id':auth.settings.bmc_launcher_client_id,'token':refresh_tokens[n]});api.post('/oauth/revoke',data={'client_id':auth.settings.bmc_launcher_client_id,'token':access[n]});state['revoked']=True;state['version']+=1
                                result={'revoked':True}
                            elif action=='state':result={'uid':accounts[state['selected']].uid,'requests':state['website_requests'],'auth_requests':auth_log,'actual_native_mode':args.native,'pending_tickets':state['pending_tickets'],'refreshes':state['refreshes'],'skin_restored':site.skin_store.get(accounts[0].uid) is None,'other_skin_untouched':site.skin_store.get(accounts[1].uid) is None}
                            elif action=='stop':result={'stopping':True};threading.Thread(target=control.shutdown,daemon=True).start()
                            else:raise ValueError()
                        data=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                    except Exception as error:
                        # No raw HTTP/proof/ticket bodies in logs.
                        data=json.dumps({'error':type(error).__name__}).encode();self.send_response(500);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            control=ThreadingHTTPServer(('127.0.0.1',0),Control)
            args.ready.write_text(json.dumps({'url':'http://127.0.0.1:'+str(control.server_port),'capability':control_secret,'uid':accounts[0].uid,'backend':'actual131loopbackTLS','minecraft_listeners':'actual-dedicated-server' if args.native else 'fixtures','native_mode':args.native,'launcher_capability':launcher_secret,'auth_url':auth_url,'site_url':site_url,'truststore':str(tmp/'trust.p12'),'site_port':int(site_url.rsplit(':',1)[1]),'spki':base64.b64encode(hashlib.sha256(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).digest()).decode()}),encoding='utf-8')
            if shared_games:
                private=json.loads(args.ready.read_text(encoding='utf-8'))
                private.update(shared_games=True,uids=[account.uid for account in accounts],
                    profile_key=profile_key,terminal_key=terminal_key,social_key=social_key,game_key=game_key,ca_file=str(tmp/'cert.pem'))
                args.ready.write_text(json.dumps(private),encoding='utf-8')
                args.ready.with_name('shared-backend-public.json').write_text(json.dumps({
                    'ready':True,'sharedGames':True,'authURL':auth_url,'siteURL':site_url,
                    'caFile':str(tmp/'cert.pem'),
                    'controllerURL':private['url'],'uids':private['uids'],'checks':checks,
                    'socialEndpoint':site_url+'/api/internal/game/social/',
                    'platformEndpoint':site_url+'/api/internal/game/',
                    'joinMintEndpoint':auth_url+'/api/launcher/minecraft/join',
                    'joinConsumeEndpoint':auth_url+'/api/internal/minecraft/join/',
                    'minecraftStarted':False,'joinGrantsMinted':False,'fakePresence':False,
                    'productionMutation':False}),encoding='utf-8')
            print('131 actual Auth/Core/website TLS backend ready; no credentials printed',flush=True)
            timer=threading.Timer(getattr(args,'lifetime_seconds',600),control.shutdown);timer.daemon=True;timer.start()
            try:control.serve_forever()
            finally:timer.cancel();control.server_close();args.ready.unlink(missing_ok=True)
            if native:status=command('close');native.wait(timeout=6)
            args.report.write_text(json.dumps({'status':'stopped','production_mutation':False,'minecraft_listeners':'actual-dedicated-server' if args.native else 'fixtures',**state},indent=2),encoding='utf-8')
        finally:
            if native and native.poll() is None:
                try:native.stdin.write('{"op":"close"}\n');native.stdin.flush();native.wait(timeout=6)
                except (OSError,subprocess.TimeoutExpired):native.terminate();native.wait(timeout=5)
            if stderr:stderr.close()
            if api:api.close()
            if browser:browser.close()
            for server in servers:server.should_exit=True
            for thread in threads:thread.join(timeout=4)
            ssl_patch.stop()
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--java-home',type=Path,required=True)
    parser.add_argument('--gson',type=Path,required=True)
    parser.add_argument('--trusted-source',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--ready',type=Path,required=True)
    parser.add_argument('--native',action='store_true')
    parser.add_argument('--shared-games',action='store_true')
    parser.add_argument('--lifetime-seconds',type=int,choices=(600,1800,3600),default=600)
    run(parser.parse_args())
