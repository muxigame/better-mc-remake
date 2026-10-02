"""Run a visible isolated 131 Minecraft/MCEF integration fixture; never production SSO."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import time
import httpx
import atexit
from urllib.parse import urlparse,parse_qs

OWNED=Path(__file__).resolve().parents[1]
ROOT=OWNED.parent/'muxi-terminal'
sys.path.insert(0,str(ROOT))
import build as terminal_build

REPORTS=[]
BACKEND=None
def control(action,**body):
    if not BACKEND:raise RuntimeError('Isolated backend unavailable')
    response=httpx.post(BACKEND['url'],headers={'X-Muxi-QA-Control':BACKEND['capability']},json={'action':action,**body},timeout=20,trust_env=False)
    response.raise_for_status();return response.json()

class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        if self.path!='/backend' or not BACKEND or self.headers.get('X-Muxi-QA-Native')!=BACKEND['capability']:
            self.send_response(403);self.end_headers();return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size>2*1024*1024:raise ValueError()
            body=json.loads(self.rfile.read(size));action=body.pop('action');result=control(action,**body)
            data=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        except Exception as error:
            data=json.dumps({'error':type(error).__name__}).encode();self.send_response(500);self.end_headers();self.wfile.write(data)
    def do_GET(self):
        path=urlparse(self.path)
        if path.path=='/report':
            REPORTS.append(parse_qs(path.query));self.send_response(204);self.end_headers();return
        if path.path=='/redirect':
            self.send_response(302);self.send_header('Location','/page');self.end_headers();return
        role='iframe' if path.path=='/frame' else 'main'
        script="""<script>
        const role=ROLE;
        const report=(value)=>fetch('/report?role='+role+'&bridge='+encodeURIComponent(value));
        report(typeof window.muxiTerminalQuery==='function'?'present-no-grant':'undefined');
        if(typeof window.muxiTerminalQuery==='function')window.muxiTerminalQuery({request:'tasks.snapshot',onSuccess:()=>report('PRIVILEGED'),onFailure:(code)=>report('denied:'+code)});
        </script>""".replace('ROLE',json.dumps(role))
        body=('<!doctype html><html><body style="background:#e6efff;font:24px sans-serif;padding:30px">'
              '<h1>Isolated web app '+role+'</h1><p>Independent content view / persistent terminal shell</p>'
              +script+('<iframe src="/frame" title="security test"></iframe>' if role=='main' else '')+'</body></html>').encode()
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('X-Frame-Options','SAMEORIGIN');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)


def allowed(rules: list[dict] | None) -> bool:
    if not rules: return True
    result=False
    for rule in rules:
        platform=rule.get('os',{})
        if platform.get('name','windows')!='windows': continue
        if platform.get('arch','x86_64') not in ('x86_64','amd64'): continue
        if any((key=='has_custom_resolution')!=value for key,value in rule.get('features',{}).items()): continue
        result=rule.get('action')=='allow'
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    global BACKEND
    native_mode="--native" in sys.argv
    workspace=Path(sys.argv[1])
    game=workspace/'_client_test/game'
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    fixture_url='http://127.0.0.1:'+str(fixture.server_port)
    version='BatterMC5Remake'
    meta=json.loads((game/f'versions/{version}/{version}.json').read_text(encoding='utf-8'))
    release=json.loads((ROOT/'build/release.json').read_text(encoding='utf-8'))
    terminal=ROOT/'build/libs'/release['artifact']
    if not terminal.is_file(): raise SystemExit('Build muxi-terminal first')
    core_release={'artifact':'muxi-game-core-1.12.0-sso-review.jar'}
    core=Path(sys.argv[2]) if len(sys.argv)>2 else ROOT.parent.parent/'task-4/artifacts'/core_release['artifact']
    if not core.is_file(): raise SystemExit('Build muxi-game-core first')

    lab=OWNED/'build'/('sso-mcef-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+uuid.uuid4().hex[:8])
    for name in ('mods','natives','config'): (lab/name).mkdir(parents=True,exist_ok=True)
    ready=lab/'backend-ready.json'
    backend_log=(lab/'backend.log').open('w',encoding='utf-8')
    backend=subprocess.Popen([sys.executable,'-B',str(OWNED/'tests/bootstrap_python_runtime.py'),str(OWNED/'tests/run_terminal_mcef_backend.py'),
        '--java-home',str(Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-21.0.12.1+1')),
        '--gson',str(workspace/'bmc5server/libraries/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar'),
        '--trusted-source',str(OWNED.parent/'muxi-minigames/src/main/java/net/muxigame/minigames/TrustedAccounts.java'),
        '--report',str(lab/'backend-result.json'),'--ready',str(ready),*(['--native'] if native_mode else [])],cwd=lab,stdin=subprocess.DEVNULL,stdout=backend_log,stderr=subprocess.STDOUT)
    def cleanup_backend():
        try:control('stop')
        except Exception:pass
        try:backend.wait(timeout=12)
        except subprocess.TimeoutExpired:backend.terminate();backend.wait(timeout=5)
        backend_log.close()
    atexit.register(cleanup_backend)
    deadline=time.monotonic()+30
    while not ready.exists():
        if backend.poll() is not None or time.monotonic()>deadline:raise SystemExit('Actual TLS QA backend failed; inspect backend.log')
        time.sleep(0.05)
    BACKEND=json.loads(ready.read_text(encoding='utf-8'))

    (lab/'config/fml.toml').write_text('earlyWindowControl = false\nearlyWindowProvider = ""\nversionCheck = false\n',encoding='utf-8')
    (lab/'options.txt').write_text('lang:zh_cn\nguiScale:2\nmaxFps:30\nenableVsync:false\nonboardAccessibility:false\nsoundCategory_master:0.0\nfullscreen:false\npauseOnLostFocus:false\n',encoding='utf-8')

    shutil.copy2(terminal,lab/'mods'/terminal.name)
    shutil.copy2(core,lab/'mods'/core.name)
    framework=OWNED.parent/'muxi-minigames/build/libs'/('muxi-minigames-'+json.loads((OWNED.parent/'muxi-minigames/mod.json').read_text(encoding='utf-8'))['version']+'.jar')
    shutil.copy2(framework,lab/'mods'/framework.name)
    for pattern in ['balm-neoforge*.jar','waystones-neoforge*.jar','xaerominimap-neoforge*.jar','xaeroworldmap-neoforge*.jar']:
        jars=list((workspace/'bmc5server/mods').glob(pattern))
        if len(jars)!=1: raise SystemExit('Ambiguous client smoke dependency '+pattern)
        shutil.copy2(jars[0],lab/'mods'/jars[0].name)
    mcef=next((game/'mods').glob('*mcef-neoforge-2.1.6-1.21.1.jar'))
    from mcef_private_qa_copy import create_private_copy
    isolation=create_private_copy(mcef,lab/'mods'/mcef.name)
    (lab/'mcef-shutdown-isolation.json').write_text(json.dumps(isolation),encoding='utf-8')
    for name in ('mcef-libraries',):
        src=game/'mods'/name
        if src.is_dir(): shutil.copytree(src,lab/'mods'/name,dirs_exist_ok=True)
    if (game/'config/mcef').is_dir(): shutil.copytree(game/'config/mcef',lab/'config/mcef',dirs_exist_ok=True)

    libraries=[]
    for lib in meta['libraries']:
        if not allowed(lib.get('rules')): continue
        artifact=lib.get('downloads',{}).get('artifact')
        if artifact: libraries.append(game/'libraries'/artifact['path'])
    libraries.append(game/f'versions/{version}/{version}.jar')
    libraries=list(dict.fromkeys(libraries))
    missing=[str(p) for p in libraries if not p.is_file()]
    if missing: raise SystemExit('Missing existing client dependencies: '+', '.join(missing))

    compiler,runtime=terminal_build.java_tools(Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-21.0.12.1+1'))
    neo=game/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-client.jar'
    mc=game/'libraries/net/minecraft/client/1.21.1-20240808.144430/client-1.21.1-20240808.144430-srg.jar'
    server_libs=list((workspace/'bmc5server/libraries').rglob('*.jar'))
    cp=os.pathsep.join(map(str,[terminal,core,mcef,neo,mc,*libraries,*server_libs,*list((lab/'mods').glob('*.jar'))]))
    classes=lab/'test-classes'
    sources=sorted((OWNED/'tests/terminal-mcef-qa').rglob('*.java'))+sorted((OWNED/'tests/terminal-mcef-environment').rglob('*.java'))
    terminal_build.compile_java(compiler,sources,classes,cp,lab/'compile.args')

    agent=lab/'tcp-selector-qa-only.jar'
    with zipfile.ZipFile(agent,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\nPremain-Class: net.muxigame.terminal.smoke.TcpSelectorQaAgent\n\n')
        name='net/muxigame/terminal/smoke/TcpSelectorQaAgent.class'
        z.write(classes/name,name)

    with zipfile.ZipFile(lab/'mods/muxi-terminal-smoke-only.jar','w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('META-INF/neoforge.mods.toml',
            'modLoader="javafml"\nloaderVersion="[4,)"\nlicense="MIT"\n'
            '[[mixins]]\nconfig="muxi_terminal_smoke.mixins.json"\n[[mixins]]\nconfig="terminal_mcef_environment.mixins.json"\n'
            '[[mods]]\nmodId="muxi_terminal_smoke"\nversion="1.0.0"\ndisplayName="Muxi Terminal hidden smoke"\n'
            '[[dependencies.muxi_terminal_smoke]]\nmodId="muxi_terminal"\ntype="required"\nversionRange="[0.1.0,)"\nordering="AFTER"\nside="CLIENT"\n')
        z.writestr('META-INF/terminal-smoke-dependency.txt','muxi_game_core required by the fixture\n')
        z.writestr('muxi_terminal_smoke.mixins.json',json.dumps({
            'required':True,'minVersion':'0.8','package':'net.muxigame.terminal.smoke.mixin',
            'compatibilityLevel':'JAVA_21','client':['AccountClientFixtureMixin','NativeClientTransportMixin'] if native_mode else ['AccountClientFixtureMixin','PassportRequestFixtureMixin','PassportIdentityFixtureMixin','PassportCallbackFixtureMixin'],'injectors':{'defaultRequire':1}
        }))
        z.writestr('terminal_mcef_environment.mixins.json',json.dumps({'required':True,'minVersion':'0.8','package':'net.muxigame.terminal.qa.mixin','compatibilityLevel':'JAVA_21','client':['OfflineMcefMixin','HardwareWmiTimeoutQAMixin','DirectTlsCefMixin'],'injectors':{'defaultRequire':1}}))
        for p in classes.rglob('*.class'):
            if p.stem.split('$')[0]!='NativeServerQA':z.write(p,p.relative_to(classes).as_posix())

    native_server=None
    if native_mode:
        from native_pipeline_runtime import prepare_server,start_server
        native_server=prepare_server(lab,workspace,classes,BACKEND,runtime)
    old_natives=game/f'versions/{version}/{version}-natives'
    if old_natives.is_dir(): shutil.copytree(old_natives,lab/'natives',dirs_exist_ok=True)
    identity=bytearray(hashlib.md5(('OfflinePlayer:'+str(BACKEND['uid'])).encode()).digest());identity[6]=(identity[6]&15)|48;identity[8]=(identity[8]&63)|128
    substitutions={
        'auth_player_name':str(BACKEND['uid']),'auth_uuid':uuid.UUID(bytes=bytes(identity)).hex,'auth_access_token':'0',
        'version_name':version,'game_directory':str(lab),'assets_root':str(game/'assets'),
        'assets_index_name':meta['assetIndex']['id'],'clientid':'','auth_xuid':'','user_type':'legacy','version_type':'release',
        'resolution_width':'1280','resolution_height':'720','natives_directory':str(lab/'natives'),
        'launcher_name':'muxi-terminal-qa','launcher_version':'1','classpath':os.pathsep.join(map(str,libraries)),
        'library_directory':str(game/'libraries'),'classpath_separator':os.pathsep
    }
    def expand(items):
        out=[]
        for item in items:
            if isinstance(item,dict):
                if not allowed(item.get('rules')): continue
                values=item['value'] if isinstance(item['value'],list) else [item['value']]
            else: values=[item]
            for value in values:
                for key,replacement in substitutions.items(): value=value.replace('${'+key+'}',replacement)
                if '${' in value: raise ValueError('Unresolved client launch template '+value)
                out.append(value)
        return out
    args=['-Xms512M','-Xmx2G','-XX:ActiveProcessorCount=2','-Dfile.encoding=UTF-8','-Djava.awt.headless=false',
          '-javaagent:'+str(agent),
          '-Dmuxi.container.fixtureUrl='+fixture_url,'-Dmuxi.sso.liveBackend=true','-Dmuxi.sso.qaUid='+str(BACKEND['uid']),'-Dmuxi.sso.directTls=true','-Dmuxi.sso.sitePort='+str(BACKEND['site_port']),'-Dmuxi.sso.spki='+BACKEND['spki'],
          *expand(meta['arguments']['jvm']),meta['mainClass'],*expand(meta['arguments']['game'])]
    if native_mode:args[:0]=['-Dmuxi.sso.realNative=true','-Dmuxi.sso.nativeAuthURL='+BACKEND['auth_url'],'-Dmuxi.sso.nativeServerPort='+str(native_server['port']),'-Djavax.net.ssl.trustStore='+BACKEND['truststore'],'-Djavax.net.ssl.trustStorePassword=isolated-synthetic']
    argfile=lab/'launch.args'; argfile.write_text('\n'.join('"'+a.replace('\\','/').replace('"','\\"')+'"' for a in args),encoding='utf-8')
    print(json.dumps({'lab':str(lab),'actual_mcef_renderer':True,'auth_ticket_identity_fixture':not native_mode,'production_mutation':False}),flush=True)
    if '--prepare-only' in sys.argv:
        fixture.shutdown();fixture.server_close();return
    if native_mode:server_process,server_cleanup=start_server(native_server)
    suite='--suite' in sys.argv
    results=[]
    phases=['switch-account','preserve','revoke'] if suite else ['revoke']
    for round_index,end_action in enumerate(phases):
        runlab=lab if round_index==0 else lab/('round-'+str(round_index+1))
        selected=control('state')['uid'];runargs=list(args)
        if round_index:
            runlab.mkdir(parents=True,exist_ok=True)
            for tree in ('mods','config'):
                for source in (lab/tree).rglob('*'):
                    target=runlab/tree/source.relative_to(lab/tree)
                    if source.is_dir():target.mkdir(parents=True,exist_ok=True)
                    else:target.parent.mkdir(parents=True,exist_ok=True);os.link(source,target)
            shutil.copy2(lab/'options.txt',runlab/'options.txt')
            runargs[runargs.index('--gameDir')+1]=str(runlab)
            runargs[runargs.index('--username')+1]=str(selected)
            identity=bytearray(hashlib.md5(('OfflinePlayer:'+str(selected)).encode()).digest());identity[6]=(identity[6]&15)|48;identity[8]=(identity[8]&63)|128
            runargs[runargs.index('--uuid')+1]=uuid.UUID(bytes=bytes(identity)).hex
        runargs=[('-Dmuxi.sso.qaUid='+str(selected)) if a.startswith('-Dmuxi.sso.qaUid=') else a for a in runargs]
        runargs.insert(0,'-Dmuxi.sso.endAction='+end_action)
        roundarg=runlab/'launch.args';roundarg.write_text('\n'.join('"'+a.replace('\\','/').replace('"','\\"')+'"' for a in runargs),encoding='utf-8')
        print(json.dumps({'phase':end_action,'uid':selected,'lab':str(runlab),'fresh_minecraft_process':True}),flush=True)
        with (runlab/'boot.log').open('w',encoding='utf-8') as log:
            qa_env=dict(os.environ)
            for name in ('MUXI_TERMINAL_GAME_CREDENTIAL','MUXI_TERMINAL_CREDENTIAL_PIPE','MUXI_TERMINAL_CREDENTIAL_BROKER'):qa_env.pop(name,None)
            qa_env['MUXI_SSO_QA_CONTROL']=BACKEND['capability']
            native_job=None
            if native_mode:
                from windows_owned_job import OwnedProcessJob
                native_job=OwnedProcessJob()
                config={'controller':BACKEND['url'],'authURL':BACKEND['auth_url'],'siteURL':BACKEND['site_url'],'spki':BACKEND['spki'],'java':str(runtime),'gameDir':str(runlab),'argFile':str(roundarg),'launcherState':str(runlab/'private-launcher-state'),'report':str(runlab/'native-launcher-result.json')}
                native_config=runlab/'native-launcher-public-config.json';native_config.write_text(json.dumps(config),encoding='utf-8')
                qa_env['MUXI_LAUNCHER_QA_CONTROL']=BACKEND['launcher_capability'];qa_env['MUXI_GAME_QA_CONTROL']=BACKEND['capability'];qa_env.pop('MUXI_SSO_QA_CONTROL',None)
                launcher=OWNED/'tests/terminal-native-launcher/bin/Debug/net9.0-windows/NativeLauncherQA.dll'
                dotnet=Path(r'C:\Users\ranzh\Documents\Codex\2026-10-02\task-3\tools\dotnet\dotnet.exe')
                if not launcher.is_file():raise RuntimeError('Build actual native launcher QA first')
                process=subprocess.Popen([str(dotnet),str(launcher),str(native_config)],cwd=runlab,env=qa_env,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,text=True,encoding='utf-8')
                native_job.assign(process);process.stdin.write('GO\n');process.stdin.flush()
            else:process=subprocess.Popen([str(runtime),'@'+str(roundarg)],cwd=runlab,env=qa_env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
            try:code=process.wait(timeout=240)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                raise SystemExit('Terminal client smoke timed out; logs: '+str(runlab/'boot.log'))
            finally:
                if native_job:native_job.close()
        report=runlab/'client-smoke-result.json'
        result=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {'success':False,'error':'No result file'}
        result.update({'exitCode':code,'lab':str(runlab),'phase':end_action,'client_uid':selected,'fixtureReports':REPORTS,'actual_tls_backend':True,'backend_state':control('state')})
        (runlab/'container-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        results.append(result);print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        if not result.get('success') or code:
            print('\n'.join((runlab/'boot.log').read_text(encoding='utf-8',errors='replace').splitlines()[-120:]))
            raise SystemExit(1)
    if native_mode:
        server_cleanup()
        events=json.loads((native_server['root']/'native-listener-events.json').read_text(encoding='utf-8'))
        if not any(e['event']=='actual-game-result-packet' and e['legalTicket'] and str(e['socialUid'])==e['uid'] and not e['isOp'] for e in events):raise AssertionError('Actual packet/listener/trust receipt absent')
        if not any(e['event']=='actual-logout-revoked' and e['socialUid']<0 for e in events):raise AssertionError('Actual disconnect revoke receipt absent')
    fixture.shutdown();fixture.server_close()
    (lab/'suite-result.json').write_text(json.dumps({'success':True,'rounds':results,'fresh_minecraft_processes':len(results),'actualCEFHTTPS':True,'productionMutation':False,'minecraft_listeners':'actual-dedicated-server' if native_mode else 'fixtures'},ensure_ascii=False,indent=2),encoding='utf-8')



if __name__=='__main__': main()
