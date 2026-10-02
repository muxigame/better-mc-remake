"""Real loopback TLS: launcher endpoints -> actual Core Java -> Auth -> website cookie.

Uses synthetic accounts/keys and actual HTTP clients, ASGI apps and SQLite stores.
Minecraft listeners use explicit fixtures, not a real game or CEF renderer. Native
proofs/tickets stay in captured pipes/memory and never enter the printed report.
"""
import argparse
import base64
from datetime import datetime,timedelta,timezone
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
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT=Path(__file__).resolve().parents[1]
def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def package(name,path):
    spec=importlib.util.spec_from_file_location(name,path/'__init__.py',submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)

def run(args):
    checks=[]
    def check(value,name):
        if not value:raise AssertionError(name)
        checks.append(name)
    with tempfile.TemporaryDirectory(prefix='dedicated-sso-loopback-') as directory:
        tmp=Path(directory);auth_url=f'https://localhost:{port()}';site_url=f'https://localhost:{port()}'
        terminal_key='synthetic-terminal-server-key-32-long'
        profile_key='synthetic-identity-server-key-32-long'
        social_key='synthetic-dedicated-social-key-32-long'
        web_key='synthetic-confidential-web-key-32-long'
        os.environ.update({'MUXI_DATABASE_PATH':str(tmp/'auth.db'),'MUXI_SIGNING_KEY_PATH':str(tmp/'oidc.pem'),
            'MUXI_ISSUER':auth_url,'MUXI_TERMINAL_SSO_ENABLED':'1','MUXI_TERMINAL_SSO_SERVER_KEY':terminal_key,
            'MUXI_MC_PROFILE_KEY':profile_key,'MUXI_BMC_WEB_CLIENT_SECRET':web_key,
            'BMC_DATABASE_PATH':str(tmp/'website.db'),'BMC_AUTH_ISSUER':auth_url,
            'BMC_AUTH_CLIENT_SECRET':web_key,'BMC_PUBLIC_URL':'https://mc.muxigame.com',
            'BMC_SERVE_WEB':'1','BMC_TERMINAL_SSO_ENABLED':'1'})
        package('dedicated_auth',ROOT/'repos/muxi-auth-dedicated-sso/app')
        package('dedicated_site',ROOT/'repos/better-mc-remake-dedicated-sso-fixture/server/app')
        from dedicated_auth import main as auth
        from dedicated_auth.security import pkce_s256,token_hash,utc_now
        from dedicated_auth.store import iso
        from dedicated_site import main as site
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Isolated SSO loopback fixture')])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(hours=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
            .sign(key,hashes.SHA256()))
        (tmp/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (tmp/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        context=ssl.create_default_context(cafile=str(tmp/'cert.pem'))
        suffix='.exe' if os.name=='nt' else ''
        subprocess.run([str(args.java_home/'bin'/('keytool'+suffix)),'-importcert','-alias','isolated-loopback',
            '-file',str(tmp/'cert.pem'),'-keystore',str(tmp/'trust.p12'),'-storetype','PKCS12',
            '-storepass','isolated-synthetic','-noprompt'],check=True,capture_output=True)
        ssl_patch=patch('ssl._create_default_https_context',return_value=context);ssl_patch.start()
        servers=[];threads=[];native=None;stderr=None;api=None;browser=None;remote_certificate=None;flags=[]
        try:
            for app,url in ((auth.app,auth_url),(site.app,site_url)):
                server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=int(url.rsplit(':',1)[1]),
                    log_level='critical',access_log=False,lifespan='off',ssl_certfile=str(tmp/'cert.pem'),ssl_keyfile=str(tmp/'key.pem')))
                t=threading.Thread(target=server.run,daemon=True);servers.append(server);threads.append(t);t.start()
            deadline=time.monotonic()+12
            while not all(s.started for s in servers):
                if time.monotonic()>deadline:raise AssertionError('Isolated TLS startup failed')
                time.sleep(.02)
            api=httpx.Client(base_url=auth_url,verify=context,trust_env=False,timeout=6)
            browser=httpx.Client(base_url=site_url,verify=context,trust_env=False,timeout=6,follow_redirects=False)
            check(api.get('/healthz').status_code==200 and browser.get('/healthz').status_code==200,'Both actual TLS services healthy')
            accounts=[];access=[];credentials=[]
            for n in range(2):
                account,verify=auth.store.register(f'synthetic-{n}@example.com',f'Synthetic_{n}','Synthetic Player','synthetic-test-password-only')
                account=auth.store.verify_email(verify);accounts.append(account)
                token=auth.store.issue_access_token(account.id,auth.settings.bmc_launcher_client_id,'openid profile',3600);access.append(token)
                response=api.post('/api/launcher/minecraft/terminal-bootstrap',headers={'Authorization':'Bearer '+token})
                check(response.status_code==200 and response.json()['uid']==account.uid,'Launcher bootstrap binds its account UID')
                credentials.append(response.json()['credential'])
            def proof(n):
                verifier='v'*43;request_id=str(uuid.uuid4())
                response=api.post('/api/launcher/minecraft/terminal-proof',json={'challenge':pkce_s256(verifier),'requestId':request_id},
                                  headers={'Authorization':'MuxiTerminal '+credentials[n]})
                check(response.status_code==200,'Issuer creates legal PKCE terminal proof')
                return response.json()['proof'],verifier,request_id
            java_log=args.report.with_suffix('.java.log');java_log.parent.mkdir(parents=True,exist_ok=True)
            stderr=java_log.open('w',encoding='utf-8')
            native_command=[sys.executable,str(ROOT/'repos/muxi-game-core-dedicated-sso/tests/run_terminal_sso_trust.py'),
                '--java-home',str(args.java_home),'--gson',str(args.gson),'--trusted-source',str(args.trusted_source),
                '--truststore',str(tmp/'trust.p12'),'--serve']
            remote_certificate=None
            if args.remote131:
                flags=['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=8']
                remote_root='C:/Users/ranzh/Documents/Codex/terminal-sso-dedicated-task4-20261002'
                remote_certificate=remote_root+'/scratch/loopback-'+uuid.uuid4().hex+'-trust.p12'
                subprocess.run(['scp',*flags,str(tmp/'trust.p12'),'JBC-1@192.168.110.131:'+remote_certificate],check=True,capture_output=True,timeout=20)
                remote_ps="$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8;"+(
                    "& 'C:\\Python38\\python.exe' '{root}/repos/muxi-game-core-dedicated-sso/tests/run_terminal_sso_trust.py' "
                    "--java-home '{jdk}' --gson '{gson}' --trusted-source '{root}/dedicated-sso/inputs/TrustedAccounts.java' "
                    "--truststore '{certificate}' --serve; exit $LASTEXITCODE").format(root=remote_root,jdk=args.remote_java_home,gson=args.remote_gson,certificate=remote_certificate)
                encoded=base64.b64encode(remote_ps.encode('utf-16le')).decode()
                auth_port=auth_url.rsplit(':',1)[1]
                native_command=['ssh',*flags,'-o','ExitOnForwardFailure=yes','-R','127.0.0.1:'+auth_port+':127.0.0.1:'+auth_port,
                    'JBC-1@192.168.110.131','powershell','-NoProfile','-NonInteractive','-EncodedCommand',encoded]
            native=subprocess.Popen(native_command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=stderr,text=True,encoding='utf-8',bufsize=1)
            replies=queue.Queue()
            def read_native():
                for line in native.stdout:replies.put(line)
                replies.put(None)
            threading.Thread(target=read_native,daemon=True).start()
            def command(op,**body):
                native.stdin.write(json.dumps({'op':op,**body})+'\n');native.stdin.flush()
                line=replies.get(timeout=18)
                if line is None:raise AssertionError('Native fixture exited before reply; inspect sanitized Java error log')
                return json.loads(line)
            core_auth_url=auth_url.replace('localhost','127.0.0.1') if args.remote131 else auth_url
            status=command('configure',endpoint=core_auth_url+'/api/internal/minecraft/',key=terminal_key,uid=accounts[0].uid)
            core_computer=status['computer']
            if args.remote131:check(core_computer=='JBC_FCRL','Core HTTPS and connection tests execute on the requested 131 computer')
            check(not status['trusted'] and not status['joinAdmission'],'Claim alone never grants trust; LoginGate remains off')
            p,v,rid=proof(0)
            body={'proof':p,'uid':accounts[0].uid,'requestId':rid,'gameSession':str(uuid.uuid4())}
            for wrong in ('',profile_key,social_key):
                headers={'X-Muxi-Server-Key':wrong}
                check(api.post('/api/internal/minecraft/terminal-ticket',json=body,headers=headers).status_code==401,'Foreign key cannot issue a terminal ticket')
                check(api.post('/api/internal/minecraft/terminal-disconnect',json=body,headers=headers).status_code==401,'Foreign key cannot revoke a terminal session')
            for wrong in (terminal_key,social_key):
                headers={'X-Muxi-Server-Key':wrong}
                check(api.get('/api/internal/minecraft/identity/'+str(accounts[0].uid),headers=headers).status_code==401,'Terminal/social keys cannot query identity')
                check(api.post('/api/internal/minecraft/join/'+str(accounts[0].uid),headers=headers).status_code==401,'Terminal/social keys cannot consume join grants')
            check(api.get('/api/internal/minecraft/identity/'+str(accounts[0].uid),headers={'X-Muxi-Server-Key':profile_key}).status_code==200,'Existing identity credential retains its original purpose')
            result=command('request',proof=p,requestId=rid)
            check(result['trusted'] and result['uid']==accounts[0].uid and not result['joinAdmission'],'Actual Java HTTPS issuer response binds only social trust to verified UID')
            check(len(result['ticket'])==43,'Actual Core returns native one-use ticket')
            headers={'Origin':'https://mc.muxigame.com','X-Muxi-Terminal-Action':'1'}
            payload={'ticket':result['ticket'],'verifier':v,'requestId':rid}
            bad=browser.post('/api/v1/auth/terminal/exchange',json=payload,headers={**headers,'Origin':'https://attacker.example'})
            check(bad.status_code==403,'Wrong Origin cannot consume valid ticket')
            bad=browser.post('/api/v1/auth/terminal/exchange',json={**payload,'verifier':'w'*43},headers=headers)
            check(bad.status_code==401,'Wrong PKCE verifier cannot login')
            response=browser.post('/api/v1/auth/terminal/exchange',json=payload,headers=headers)
            check(response.status_code==303 and response.headers.get('location')=='/account.html','Native POST lands on mc account.html')
            cookie=response.headers.get('set-cookie','').lower()
            check(all(flag in cookie for flag in ('bmc_session=','secure','httponly','samesite=lax','path=/')) and 'domain=' not in cookie,'Website cookie is Secure HttpOnly host-only SameSite')
            user=browser.get('/api/v1/auth/me').json()['user']
            check(user['uid']==accounts[0].uid and user['role']=='player','Website session is the proof-bound player, no role escalation')
            check(browser.get('/account.html').status_code==200,'Actual player-center HTML is accessible with cookie')
            check(browser.get('/admin.html').status_code==403,'Player cannot access administrator page')
            check(browser.post('/api/v1/auth/terminal/exchange',json=payload,headers=headers).status_code==401,'Exchanged ticket is rejected on replay')
            check(browser.post('/api/v1/auth/logout').status_code==200 and browser.get('/api/v1/auth/me').status_code==401,'Website logout revokes its session')

            time.sleep(2.05) # Production per-connection throttle is exercised, never disabled.
            p2,v2,r2=proof(0);next_ticket=command('request',proof=p2,requestId=r2)['ticket']
            check(len(next_ticket)==43,'Same authenticated connection can issue a new one-use ticket')
            status=command('disconnect');check(not status['trusted'] and not status['joinAdmission'],'Game disconnect revokes exact-listener trust')
            expiry=time.monotonic()+4
            while True:
                with auth.store.connect() as db:
                    pending=db.execute('SELECT 1 FROM terminal_tickets WHERE ticket_hash=?',(token_hash(next_ticket),)).fetchone()
                if pending is None:break
                if time.monotonic()>expiry:raise AssertionError('Issuer disconnect did not revoke ticket')
                time.sleep(.04)
            response=browser.post('/api/v1/auth/terminal/exchange',json={'ticket':next_ticket,'verifier':v2,'requestId':r2},headers=headers)
            check(response.status_code==401,'Actual issuer disconnect cancels pending ticket')
            status=command('reconnect');check(not status['trusted'],'New connection never inherits previous trust')
            wrong,v3,r3=proof(1);result=command('request',proof=wrong,requestId=r3)
            check(not result['trusted'] and not result['ticket'],'Another player proof cannot authenticate this connection UID')
            time.sleep(2.05)
            p4,v4,r4=proof(0);result=command('request',proof=p4,requestId=r4)
            check(result['trusted'] and not result['joinAdmission'],'Fresh proof reauthenticates the renewed connection without LoginGate')
            with auth.store.connect() as db:
                db.execute('UPDATE terminal_tickets SET expires_at=? WHERE ticket_hash=?',(iso(utc_now()-timedelta(seconds=1)),token_hash(result['ticket'])))
            check(browser.post('/api/v1/auth/terminal/exchange',json={'ticket':result['ticket'],'verifier':v4,'requestId':r4},headers=headers).status_code==401,'Expired one-use ticket fails over actual HTTPS')
            command('reconnect');p5,v5,r5=proof(0);result=command('request',proof=p5,requestId=r5)
            check(len(result['ticket'])==43,'Renewed connection obtains current account ticket')
            response=api.post('/oauth/revoke',data={'client_id':auth.settings.bmc_launcher_client_id,'token':access[0]})
            check(response.status_code==200,'Launcher OAuth revoke accepted')
            check(browser.post('/api/v1/auth/terminal/exchange',json={'ticket':result['ticket'],'verifier':v5,'requestId':r5},headers=headers).status_code==401,'Launcher logout/source-token revocation invalidates pending platform ticket')
            check(api.post('/api/launcher/minecraft/terminal-proof',json={'challenge':pkce_s256(v5),'requestId':str(uuid.uuid4())},headers={'Authorization':'MuxiTerminal '+credentials[0]}).status_code==401,'Revoked process credential cannot create further proofs')
            status=command('configure',endpoint=core_auth_url+'/api/internal/minecraft/',key=terminal_key,uid=accounts[1].uid)
            check(not status['trusted'],'Second account starts with an unverified connection')
            pb,vb,rb=proof(1);second=command('request',proof=pb,requestId=rb)
            check(second['trusted'] and second['uid']==accounts[1].uid and not second['joinAdmission'],'Second legal account independently binds its own social UID')
            response=browser.post('/api/v1/auth/terminal/exchange',json={'ticket':second['ticket'],'verifier':vb,'requestId':rb},headers=headers)
            check(response.status_code==303 and browser.get('/api/v1/auth/me').json()['user']['uid']==accounts[1].uid,'Second account native POST establishes its own website session')
            check(browser.post('/api/v1/auth/logout').status_code==200 and browser.get('/api/v1/auth/me').status_code==401,'Second website account logout revokes cookie')
            check(api.post('/api/launcher/minecraft/terminal-bootstrap/revoke',headers={'Authorization':'MuxiTerminal '+credentials[1]}).status_code==200,'Game process credential can be revoked on exit')
            check(api.post('/api/launcher/minecraft/terminal-proof',json={'challenge':pkce_s256(vb),'requestId':str(uuid.uuid4())},headers={'Authorization':'MuxiTerminal '+credentials[1]}).status_code==401,'Exited game process cannot create further proofs')
            status=command('close');check(not status['trusted'],'Server shutdown clears trust')
            native.wait(timeout=6)
            check(native.returncode==0,'Actual Java HTTPS process exits normally')
            sources={}
            for p in (ROOT/'repos/muxi-auth-dedicated-sso/app/main.py',ROOT/'repos/muxi-auth-dedicated-sso/app/terminal_server_auth.py',
                      ROOT/'repos/muxi-game-core-dedicated-sso/src/main/java/net/muxigame/core/feature/login/TerminalPassportServer.java',args.trusted_source):
                sources[str(p.name)]=hashlib.sha256(p.read_bytes()).hexdigest()
            report={'status':'passed','assertions':len(checks),'checks':checks,'computer':os.getenv('COMPUTERNAME','unknown'),
                'actual_auth_core_website_tls_clients_and_sqlite':True,'core_computer':core_computer,
                'backend_location':'008 task-owned loopback services over SSH loopback-only forward' if args.remote131 else 'local loopback',
                'minecraft_listeners':'deterministic fixtures',
                'real_minecraft_or_mcef_player_acceptance':False,'production_mutation':False,'credentials_printed':False,
                'two_synthetic_accounts_independently_authenticated':True,
                'source_sha256':sources}
            args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps({'status':'passed','assertions':len(checks),'computer':report['computer'],'core_computer':core_computer,'minecraft_listeners':'fixtures','real_player_acceptance':False}))
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
            if args.remote131 and remote_certificate:
                cleanup="Remove-Item -LiteralPath '"+remote_certificate+"' -ErrorAction SilentlyContinue"
                subprocess.run(['ssh',*flags,'JBC-1@192.168.110.131','powershell','-NoProfile','-NonInteractive',
                    '-EncodedCommand',base64.b64encode(cleanup.encode('utf-16le')).decode()],capture_output=True,timeout=15)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--java-home',type=Path,required=True)
    parser.add_argument('--gson',type=Path,required=True);parser.add_argument('--trusted-source',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--remote131',action='store_true')
    parser.add_argument('--remote-java-home',default='C:/Users/ranzh/Documents/Codex/terminal-integration-task6-20261001/tools/jdk/jdk-21.0.12.1+1')
    parser.add_argument('--remote-gson',default='C:/Users/ranzh/workspace/dev/muxigame/bmc5server/libraries/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar')
    args=parser.parse_args();run(args)
