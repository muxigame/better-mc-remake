"""Horse-specific fixtures layered on the shared runner; never edits its source."""
from pathlib import Path
import argparse,ctypes,datetime,hashlib,json,os,re,shutil,socket,subprocess,sys,threading,time,zipfile
ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace-root',type=Path,default=REPO.parent)
parser.add_argument('--reference-client',type=Path,required=True,help='Previously isolated complete pack, with private configs only')
parser.add_argument('--artifact-manifest',type=Path,required=True,help='Five frozen normal JAR paths and sha256 values')
parser.add_argument('--source-manifest',type=Path,required=True)
parser.add_argument('--java-home',type=Path,required=True)
parser.add_argument('--output-root',type=Path,default=REPO/'build/qa/unified')
parser.add_argument('--allow-focused-host',action='store_true',help='Only after a coordinated desktop window: show/focus host for terminal actions, auto-hide after 45s')
parser.add_argument('--capture-native-exit',action='store_true',help='Observe only own clients during normal shutdown; local native stack/minidump')
parser.add_argument('--cpu-affinity-mask',type=lambda v:int(v,0),default=0x0f,help='Inherited Windows CPU budget; default CPUs 0-3')
parser.add_argument('--unix-temp',type=Path,required=True,help='Short, existing directory required by AF_UNIX on Windows')
options=parser.parse_args()
WORKSPACE=options.workspace_root.resolve()
PROJECT=WORKSPACE/'better-mc-remake'
REFERENCE=options.reference_client.resolve()
JDK=options.java_home.resolve()
OUTPUT=options.output_root.resolve()
OUTPUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(PROJECT/'scripts'))
import local_mc_debug as debug
import local_mc_runtime as runtime
import psutil
from private_points_fixture import Fixture

def main():
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(),options.cpu_affinity_mask):raise ctypes.WinError(ctypes.get_last_error())
    artifacts=json.loads(options.artifact_manifest.read_text())
    candidates=[Path(row['path']).resolve() for row in artifacts.values()]
    if set(artifacts)!={'muxi-horse-racing','muxi-minigames','muxi-terminal','muxi-game-core','muxi-flight'}:raise ValueError('Exact five normal candidates required')
    for row in artifacts.values():
        if runtime.sha256(Path(row['path']))!=row['sha256'] or not row.get('qaOverlayAbsent'):raise ValueError('Frozen normal artifact mismatch: '+row['path'])
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    lab=PROJECT/'build/local-mc-debug'/('horse-fullpack-'+stamp)
    control=OUTPUT/('horse-unified-control-'+stamp);control.mkdir()
    fixture=Fixture(PROJECT/'server/app/player_platform.py',control)
    for key in list(os.environ):
        if key.startswith('MUXI_'):del os.environ[key]
    os.environ.update(MUXI_GAME_PLATFORM_URL=fixture.url,MUXI_GAME_PLATFORM_KEY=fixture.key)
    overlays={};inventory={};configReceipt={};exitObservers=[]
    originalCompile=runtime.compile_agent
    def compile_agent(project,root,jdk,classpath):
        result=originalCompile(project,root,jdk,classpath)
        classes=root/'horse-qa-classes';classes.mkdir()
        sources=[ROOT/name for name in ('HorseUnifiedServerQA.java','HorseUnifiedClientQA.java','HorseUnifiedRenderProbe.java','CefPrivatePathsMixin.java')]
        sources += [WORKSPACE/'muxi-horse-racing/tests/horse-client/java/net/muxigame/minigames/horseclientqa/HorseCefQA.java',WORKSPACE/'muxi-horse-racing/tests/horse-client/java/net/muxigame/minigames/horseclientqa/mixin/OfflineMcefMixin.java']
        args=['--release','21','-encoding','UTF-8','-proc:none','-cp',classpath+os.pathsep+os.pathsep.join(map(str,candidates)),'-d',str(classes),*map(str,sources)]
        runtime.argfile(root/'horse-qa-javac.args',args)
        with (root/'horse-qa-compile.log').open('w') as log:subprocess.run([str(jdk/'bin/javac.exe'),'-J-XX:ActiveProcessorCount=3','@'+str(root/'horse-qa-javac.args')],stdout=log,stderr=subprocess.STDOUT,check=True)
        normal=Path(artifacts['muxi-horse-racing']['path']);overlay=root/'horse-qa-overlay.jar'
        with zipfile.ZipFile(normal) as src,zipfile.ZipFile(overlay,'w',zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data=src.read(item.filename)
                if item.filename=='META-INF/neoforge.mods.toml':data+=b'\n[[mixins]]\nconfig="horse_unified_qa.mixins.json"\n\n[[mixins]]\nconfig="horse_offline_qa.mixins.json"\n'
                dst.writestr(item,data)
            for file in classes.rglob('*.class'):dst.write(file,file.relative_to(classes).as_posix())
            dst.writestr('horse_unified_qa.mixins.json',json.dumps({'required':True,'minVersion':'0.8','package':'net.muxigame.horseunifiedqa.mixin','compatibilityLevel':'JAVA_21','client':['HorseUnifiedRenderProbe','CefPrivatePathsMixin'],'injectors':{'defaultRequire':1}}))
            # QA-only offline checksum hook uses already-installed native libraries, never a fake browser.
            dst.writestr('horse_offline_qa.mixins.json',json.dumps({'required':True,'minVersion':'0.8','package':'net.muxigame.minigames.horseclientqa.mixin','compatibilityLevel':'JAVA_21','client':['OfflineMcefMixin'],'injectors':{'defaultRequire':1}}))
        overlays['horse']=overlay
        return result
    runtime.compile_agent=compile_agent
    originalCopy=runtime.copy_inputs
    def copy_inputs(home,mods,data):
        role=home.name;base=WORKSPACE/'bmc5server' if role=='server' else REFERENCE
        replacements=tuple(name+'-' for name in artifacts)
        pack=[jar for jar in sorted((base/'mods').glob('*.jar')) if not jar.name.startswith(replacements)]
        injected=[overlays['horse'] if jar.name.startswith('muxi-horse-racing-') else jar for jar in mods]
        originalCopy(home,[*pack,*injected],data)
        # Previously isolated configs contain no real auth keys; preserve full-pack dependency overrides.
        shutil.copytree(REFERENCE/'config',home/'config',dirs_exist_ok=True)
        # Parent explicitly requires PasterDream UI true for this first-login comparison.
        ui=home/'config/PasterDream-Client.toml'
        source=ui.read_bytes()
        changed,count=re.subn(rb'("enable mod ui"\s*=\s*)(true|false)',rb'\g<1>true',source)
        if count!=1:raise ValueError('Expected exactly one PasterDream UI setting')
        ui.write_bytes(changed)
        configReceipt[role]={'pasterDreamUi':True,'sourceSha256':hashlib.sha256(source).hexdigest(),'instanceSha256':hashlib.sha256(changed).hexdigest(),'reason':'Parent requires UI true; preserved historical pack had false'}
        for folder in ('defaultconfigs','kubejs','configureddefaults','tlm_custom_pack','CustomSkinLoader','resourcepacks','fancymenu_data'):
            if (REFERENCE/folder).is_dir():shutil.copytree(REFERENCE/folder,home/folder)
        if role!='server':
            shutil.copytree(WORKSPACE/'_client_test/game/mods/mcef-libraries',home/'mods/mcef-libraries')
            (home/'config/mcef').mkdir(exist_ok=True)
            (home/'config/mcef/mcef.properties').write_text('skip-download=true\nuse-cache=false\ndownload-mirror=https://mcef-download.cinemamod.com\nuser-agent=MCEF\n')
        inventory[role]={j.name:runtime.sha256(j) for j in (home/'mods').glob('*.jar')}
    runtime.copy_inputs=copy_inputs
    originalArgs=runtime.argfile
    def argfile(file,args):
        if file.name=='launch.args':
            # The generic runner writes its minimal fml.toml after copy_inputs.
            shutil.copy2(REFERENCE/'config/fml.toml',file.parent/'config/fml.toml')
            if options.allow_focused_host and file.parent.name=='host':args=['-Dmuxi.horse.allowFocusedQa=true',*args]
            runtime.write_json(file.parent/'config/muxi-game-core.json',{'schema':1,'features':{'identity':{'enabled':False},'login':{'enabled':False},'opSync':{'enabled':False},'terminalSso':{'enabled':False}}})
            args=['-Dmuxi.horse.unifiedQa=true','-Dmuxi.horse.cefQa=true','-Dmuxi.horse.model=wine_fox/01_taisho_maid','-Dmuxi.horse.texture=skin','-Dhttp.proxyHost=127.0.0.1','-Dhttp.proxyPort=9','-Dhttps.proxyHost=127.0.0.1','-Dhttps.proxyPort=9',*args]
        originalArgs(file,args)
    runtime.argfile=argfile
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    inputs={'lab':str(lab),'control':str(control),'port':port,'sourceManifest':json.loads(options.source_manifest.read_text(encoding='utf-8')),'normalArtifacts':artifacts,'unifiedRunner':str(PROJECT/'scripts/local_mc_debug.py'),'genuineClients':2,'productionWrites':False,'physicalOSInput':False,'SSOAcceptance':False,'cpuAffinityMask':options.cpu_affinity_mask,'pasterDreamUi':True,'nativeExitDiagnostic':options.capture_native_exit,'focusedHostAuthorized':options.allow_focused_host,'focusedPhaseBudgetSeconds':45,'privateCefPathsQaOverlay':True}
    runtime.write_json(OUTPUT/'horse-unified-latest.json',inputs)
    def native_helpers():
        rows=[]
        expected={str((lab/role/'mods/mcef-libraries/windows_amd64/jcef_helper.exe').resolve()).lower():role for role in ('host','guest')}
        for process in psutil.process_iter(['pid','ppid','name']):
            if (process.info['name'] or '').lower()!='jcef_helper.exe':continue
            try:
                exe=str(Path(process.exe()).resolve()).lower()
                if exe in expected:
                    command=process.cmdline();rows.append({'pid':process.pid,'createdAt':process.create_time(),'parentPid':process.ppid(),'role':expected[exe],'exe':exe,'nativeFlags':[a for a in command if a.startswith(('--type=','--user-data-dir=','--log-file='))]})
            except psutil.Error:continue
        return rows
    def drive():
        try:
            sys.path.insert(0,str(ROOT));from horse_unified_scenario import run
            run(lab,control,inputs)
        except BaseException as failure:
            runtime.write_json(control/'scenario-result.json',{'success':False,'error':str(failure)})
        finally:
            if (lab/'local-mc-owner.json').exists():
                _,marker=runtime.owned_lab(lab)
                if options.capture_native_exit:
                    from native_exit_observer import observe_owned
                    for role in ('host','guest'):
                        try:
                            observer=observe_owned(lab,role,control/('native-exit-'+role));exitObservers.append(observer)
                            observer.ready.wait(5)
                        except Exception as error:runtime.write_json(control/('native-exit-attach-'+role+'.json'),{'error':str(error),'normalStopStillRequired':True})
                # Stop our polling/render callbacks and close terminal-owned views through their normal API.
                # Attach the optional exit observer first so a fault during view cleanup is captured too.
                runtime.write_json(control/'native-helpers-before-cleanup.json',native_helpers())
                cleanup={}
                for role in ('host','guest'):
                    try:
                        item=marker.get('processes',{}).get(role)
                        if not item:cleanup[role]={'skipped':'No marked client was launched'};continue
                        try:process=psutil.Process(item['pid'])
                        except psutil.NoSuchProcess:cleanup[role]={'skipped':'Marked client already exited'};continue
                        expected=str((lab/role/'launch.args').resolve()).replace('\\','/').lower()
                        command=' '.join(process.cmdline()).replace('\\','/').lower()
                        if abs(process.create_time()-item['processCreateTime'])>.01 or process.ppid()!=os.getpid() or expected not in command:
                            cleanup[role]={'skipped':'Exact marked child identity no longer active'};continue
                        number=1_000_000_000
                        runtime.write_json(lab/'coordinator'/('horse-client-'+role+'.json'),{'id':number,'type':'quiesce-terminal'})
                        deadline=time.monotonic()+10;reply=None
                        while time.monotonic()<deadline:
                            reply=runtime.read_json(lab/'coordinator'/('horse-client-'+role+'-result-'+str(number)+'.json'))
                            if reply:break
                            time.sleep(.1)
                        cleanup[role]=reply or {'ok':False,'error':'No owned cleanup callback; normal stop remains required'}
                        if reply and reply.get('ok'):
                            deadline=time.monotonic()+3
                            while time.monotonic()<deadline:
                                view=runtime.read_json(lab/'coordinator'/('horse-status-'+role+'.json')) or {}
                                if view.get('qaQuiesced') and view.get('ticks',0)>=reply.get('quiescedAtTick',0)+10:break
                                time.sleep(.1)
                    except Exception as error:cleanup[role]={'ok':False,'error':str(error)}
                runtime.write_json(control/'normal-view-cleanup.json',cleanup)
                debug.stop_files(lab,marker)
    thread=threading.Thread(target=drive,daemon=True);thread.start()
    args=['run','--project-root',str(PROJECT),'--instance-root',str(lab),'--server-runtime',str(WORKSPACE/'bmc5server'),'--client-game',str(WORKSPACE/'_client_test/game'),'--java-home',str(JDK),'--clients','2','--client-name','10000','--client-name','10001','--port',str(port),'--mode','hold','--hold-seconds','1800','--boot-timeout','1200','--command-timeout','60','--shutdown-timeout','180','--server-memory-mb','8192','--client-memory-mb','8192','--reserve-memory-mb','8192','--unix-temp',str(options.unix_temp.resolve()),'--accept-eula']
    for jar in candidates:args+=['--mod',str(jar)]
    code=debug.main(args)
    thread.join(timeout=45)
    for observer in exitObservers:
        if not observer.done.wait(2):observer.cancel.set();observer.done.wait(2)
    residue=native_helpers();runtime.write_json(control/'native-helpers-after-stop.json',residue)
    privatePaths={role:runtime.read_json(lab/role/'mcef-private-paths.json') for role in ('host','guest')}
    backend=fixture.close()
    result=runtime.read_json(control/'scenario-result.json') or {'success':False,'error':'No scenario result'}
    runtime.write_json(control/'fullpack-inputs.json',{**inputs,'actualInventories':inventory,'privateConfigReceipts':configReceipt})
    summary={'success':not thread.is_alive() and code==0 and result.get('success',False) and backend['success'] and not residue and all(privatePaths.values()),'scenario':result,'backend':backend,'runtime':runtime.read_json(lab/'run-result.json'),'inputs':inputs,'nativeExitDiagnostics':[o.report for o in exitObservers],'nativeHelpersAfterStop':residue,'privateNativePaths':privatePaths,'coordinatorReleased':not thread.is_alive()}
    runtime.write_json(control/'summary.json',summary)
    print(json.dumps({'success':summary['success'],'lab':str(lab),'control':str(control),'counts':{r:len(v) for r,v in inventory.items()},'runtimeCode':code}),flush=True)
    return 0 if summary['success'] else 1
if __name__=='__main__':raise SystemExit(main())
