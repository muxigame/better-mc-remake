"""Stage a private actual dedicated-server/native-launcher QA runtime."""
from pathlib import Path
import atexit
import json
import os
import shutil
import socket
import subprocess
import time
import zipfile
from windows_owned_job import OwnedProcessJob

def prepare_server(lab,workspace,classes,backend,runtime):
    root=lab/'dedicated-native-server';(root/'mods').mkdir(parents=True,exist_ok=True);(root/'config').mkdir(exist_ok=True)
    for jar in (lab/'mods').glob('*.jar'):
        if jar.name.startswith(('muxi-game-core-','muxi-minigames-','muxi-horse-racing-','muxi-flight-','muxi-terminal-0.','balm-','waystones-')):os.link(jar,root/'mods'/jar.name)
    shutil.copy2(workspace/'bmc5server/eula.txt',root/'eula.txt')
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    (root/'server.properties').write_text('server-ip=127.0.0.1\nserver-port='+str(port)+'\nonline-mode=false\nenforce-secure-profile=false\nmax-players=4\nview-distance=2\nsimulation-distance=2\nspawn-protection=0\nlevel-name=private-native-qa\nlevel-seed=13120261002\nlevel-type=minecraft:flat\ngamemode=creative\ndifficulty=peaceful\nspawn-monsters=false\nspawn-animals=false\nallow-flight=true\nenable-rcon=false\nenable-query=false\nmotd=131 isolated actual native SSO QA\n',encoding='utf-8')
    config={'features':{'terminalSso':{'enabled':True,'endpoint':'https://account.muxigame.com/api/internal/minecraft/','serverKey':'synthetic-terminal-server-key-32-long'}}}
    (root/'config/muxi-game-core.json').write_text(json.dumps(config),encoding='utf-8')
    with zipfile.ZipFile(root/'mods/native-server-qa-only.jar','w',zipfile.ZIP_DEFLATED) as target:
        target.writestr('META-INF/neoforge.mods.toml','modLoader="javafml"\nloaderVersion="[4,)"\nlicense="MIT"\n[[mixins]]\nconfig="native_server_qa.mixins.json"\n[[mods]]\nmodId="muxi_terminal_native_server_qa"\nversion="1.0.0"\ndisplayName="131 isolated native server QA"\n')
        target.writestr('native_server_qa.mixins.json',json.dumps({'required':True,'minVersion':'0.8','package':'net.muxigame.terminal.smoke.mixin','compatibilityLevel':'JAVA_21','mixins':['NativeServerTransportMixin','NativeServerTraceMixin'],'injectors':{'defaultRequire':1}}))
        for source in classes.rglob('*.class'):
            if source.stem.split('$')[0] in ('NativeServerQA','NativeIssuerEndpoint','NativeServerTransportMixin','NativeServerTraceMixin'):target.write(source,source.relative_to(classes).as_posix())
    installed=workspace/'bmc5server'
    original=(installed/'libraries/net/neoforged/neoforge/21.1.250/win_args.txt').read_text(encoding='utf-8').split()
    library_prefix=(installed/'libraries').as_posix()
    original=[part.replace('libraries/',library_prefix+'/') if 'libraries/' in part else part for part in original]
    original=[('-DlibraryDirectory='+library_prefix) if part=='-DlibraryDirectory=libraries' else part for part in original]
    arguments=['-Xms512M','-Xmx2G','-XX:ActiveProcessorCount=2','-Dfile.encoding=UTF-8','-Dmuxi.sso.nativeAuthURL='+backend['auth_url'],'-Djavax.net.ssl.trustStore='+backend['truststore'],'-Djavax.net.ssl.trustStorePassword=isolated-synthetic',*original,'nogui']
    argfile=root/'server-launch.args';argfile.write_text('\n'.join('"'+arg.replace('\\','/').replace('"','\\"')+'"' for arg in arguments),encoding='utf-8')
    return {'root':root,'port':port,'argfile':argfile,'runtime':runtime}

def start_server(prepared):
    root=prepared['root'];log=(root/'boot.log').open('w',encoding='utf-8');job=OwnedProcessJob()
    process=subprocess.Popen([str(prepared['runtime']),'@'+str(prepared['argfile'])],cwd=root,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,text=True,encoding='utf-8')
    job.assign(process)
    def cleanup():
        try:
            if process.poll() is None:process.stdin.write('stop\n');process.stdin.flush();process.wait(timeout=25)
        except (OSError,subprocess.TimeoutExpired):pass
        finally:job.close();log.close()
    atexit.register(cleanup)
    deadline=time.monotonic()+100
    while True:
        if process.poll() is not None or time.monotonic()>deadline:cleanup();raise RuntimeError('Actual dedicated QA server failed; inspect private boot.log')
        if 'Done (' in (root/'boot.log').read_text(encoding='utf-8',errors='replace'):break
        time.sleep(.2)
    (root/'process-owner.json').write_text(json.dumps({'pid':process.pid,'ownedWindowsJob':True,'startedAt':time.time(),'productionMutation':False}),encoding='utf-8')
    return process,cleanup
