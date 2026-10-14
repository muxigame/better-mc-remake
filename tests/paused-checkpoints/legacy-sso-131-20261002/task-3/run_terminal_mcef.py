"""Launch an invisible, isolated Minecraft client and render the local terminal home page."""
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
from urllib.parse import urlparse,parse_qs

OWNED=Path(__file__).resolve().parents[1]
ROOT=OWNED.parent/'muxi-terminal'
sys.path.insert(0,str(ROOT))
import build as terminal_build

REPORTS=[]
class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
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
    shutil.copy2(mcef,lab/'mods'/mcef.name)
    for name in ('mcef-libraries','mcef-cache'):
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
    sources=sorted((ROOT/'tests/integration-smoke').rglob('*.java'))+sorted((OWNED/'tests/terminal-mcef-environment').rglob('*.java'))
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
            'compatibilityLevel':'JAVA_21','client':['HiddenWindowMixin','AccountClientFixtureMixin','PassportRequestFixtureMixin','PassportIdentityFixtureMixin','PassportCallbackFixtureMixin'],'injectors':{'defaultRequire':1}
        }))
        z.writestr('terminal_mcef_environment.mixins.json',json.dumps({'required':True,'minVersion':'0.8','package':'net.muxigame.terminal.qa.mixin','compatibilityLevel':'JAVA_21','client':['OfflineMcefMixin','HardwareWmiTimeoutQAMixin'],'injectors':{'defaultRequire':1}}))
        for p in classes.rglob('*.class'): z.write(p,p.relative_to(classes).as_posix())

    old_natives=game/f'versions/{version}/{version}-natives'
    if old_natives.is_dir(): shutil.copytree(old_natives,lab/'natives',dirs_exist_ok=True)
    substitutions={
        'auth_player_name':'MuxiTerminalQA','auth_uuid':'c53d9bc3f8644a1093cd41bd94eb05ff','auth_access_token':'0',
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
    args=['-Xms512M','-Xmx2G','-XX:ActiveProcessorCount=2','-Dfile.encoding=UTF-8','-Djava.awt.headless=true',
          '-javaagent:'+str(agent),
          '-Dmuxi.container.fixtureUrl='+fixture_url,
          *expand(meta['arguments']['jvm']),meta['mainClass'],*expand(meta['arguments']['game'])]
    argfile=lab/'launch.args'; argfile.write_text('\n'.join('"'+a.replace('\\','/').replace('"','\\"')+'"' for a in args),encoding='utf-8')
    print(json.dumps({'lab':str(lab),'actual_mcef_renderer':True,'auth_ticket_identity_fixture':True,'production_mutation':False}),flush=True)
    with (lab/'boot.log').open('w',encoding='utf-8') as log:
        qa_env=dict(os.environ);qa_env.pop('MUXI_TERMINAL_GAME_CREDENTIAL',None)
        process=subprocess.Popen([str(runtime),'@'+str(argfile)],cwd=lab,env=qa_env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        try: code=process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            raise SystemExit('Terminal client smoke timed out; logs: '+str(lab/'boot.log'))
    report=lab/'client-smoke-result.json'
    result=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {'success':False,'error':'No result file'}
    fixture.shutdown();fixture.server_close()
    result.update({'exitCode':code,'lab':str(lab),'fixtureReports':REPORTS})
    if not all(any(r.get('role')==[role] and r.get('bridge',[None])[0] in ['undefined','present-no-grant','denied:403'] for r in REPORTS) for role in ['main','iframe']) or any(r.get('bridge')==['PRIVILEGED'] for r in REPORTS):
        result['success']=False;result['fixtureError']='external main/iframe bridge absence not confirmed'
    (lab/'container-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result.get('success') or code:
        print('\n'.join((lab/'boot.log').read_text(encoding='utf-8',errors='replace').splitlines()[-120:]))
        raise SystemExit(1)


if __name__=='__main__': main()
