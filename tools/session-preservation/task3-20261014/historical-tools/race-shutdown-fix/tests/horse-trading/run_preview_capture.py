"""One hidden private client captures six actual vanilla HorseRenderer stills; no YSM/MCEF/backend."""
from pathlib import Path
import argparse,datetime,hashlib,json,os,shutil,subprocess,time,zipfile,sys
sys.path.insert(0,r'C:\Users\ranzh\workspace\dev\muxigame\.ops-venv\Lib\site-packages')


def allowed(rules):
    if not rules:return True
    result=False
    for r in rules:
        platform=r.get('os',{})
        if platform.get('name','windows')!='windows' or platform.get('arch','amd64') not in ('amd64','x86_64'):continue
        if any((k=='has_custom_resolution')!=v for k,v in r.get('features',{}).items()):continue
        result=r.get('action')=='allow'
    return result

def split_framework(repo, lab):
    shared=repo.resolve().parent/'muxi-minigames'
    meta=json.loads((shared/'mod.json').read_text(encoding='utf-8'))
    jar=shared/'build/libs'/('muxi-minigames-'+meta['version']+'.jar')
    if not jar.is_file():raise RuntimeError('Build sibling muxi-minigames before running game QA')
    if (lab/'mods').is_dir():shutil.copy2(jar,lab/'mods'/jar.name)
    return jar

def main():
    p=argparse.ArgumentParser();p.add_argument('--jar',type=Path);p.add_argument('--stadium-qa',action='store_true');p.add_argument('--cef-qa',action='store_true');p.add_argument('--terminal-jar',type=Path);p.add_argument('--points-qa',action='store_true');p.add_argument('--performance-qa',action='store_true');p.add_argument('--backend-source',type=Path);p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.repo=a.repo.resolve();a.output=a.output.resolve()
    game=Path(r'C:\Users\ranzh\workspace\dev\muxigame\_client_test\game');server=Path(r'C:\Users\ranzh\workspace\dev\muxigame\bmc5server');jdk=Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-<PRIVATE_HOST>+1')
    lab=a.output/('horse-preview-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'));lab.mkdir(parents=True);(lab/'HORSE_QA_ONLY').write_text('Generated horses and private world only\n')
    for name in ('mods','config','natives','qa-classes'):(lab/name).mkdir()
    meta=json.loads((game/'versions/BatterMC5Remake/BatterMC5Remake.json').read_text(encoding='utf-8'))
    libs=list(dict.fromkeys([game/'libraries'/x['downloads']['artifact']['path'] for x in meta['libraries'] if allowed(x.get('rules')) and x.get('downloads',{}).get('artifact')]+[game/'versions/BatterMC5Remake/BatterMC5Remake.jar']))
    shutil.copytree(game/'versions/BatterMC5Remake/BatterMC5Remake-natives',lab/'natives',dirs_exist_ok=True)
    framework=split_framework(a.repo,lab)
    jar=a.jar.resolve() if a.jar else next((a.repo/'build/libs').glob('muxi-horse-racing-*.jar'));target=lab/'mods'/jar.name
    for directory,pattern in [(server/'mods','*touhoulittlemaid-1.5.3*')]:
        source=next(directory.glob(pattern));shutil.copy2(source,lab/'mods'/source.name)
    if a.cef_qa:
        if not a.stadium_qa or not a.terminal_jar:p.error('CEF QA needs arena mode and immutable guarded terminal jar')
        with zipfile.ZipFile(a.terminal_jar) as z:
            if 'net/muxigame/terminal/client/compat/McefShutdownGuard.class' not in z.namelist() or 'muxi_terminal.compat.mixins.json' not in z.namelist():raise ValueError('75715da guarded terminal required')
        shutil.copy2(a.terminal_jar,lab/'mods'/a.terminal_jar.name)
        mcef=next((game/'mods').glob('*mcef-neoforge-2.1.6-1.21.1.jar'));shutil.copy2(mcef,lab/'mods'/mcef.name)
        shutil.copytree(game/'mods/mcef-libraries',lab/'mods/mcef-libraries',dirs_exist_ok=True)
        (lab/'config/mcef').mkdir();(lab/'config/mcef/mcef.properties').write_text('skip-download=true\nuse-cache=false\n')
    mapped=[game/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-client.jar',game/'libraries/net/minecraft/client/1.21.1-20240808.144430/client-1.21.1-20240808.144430-srg.jar']
    cp=os.pathsep.join(map(str,list(dict.fromkeys([framework,jar]+mapped+libs+list((server/'libraries').rglob('*.jar'))+list((lab/'mods').glob('*.jar'))))))
    sources=[a.repo/'tests/horse-client/java/net/muxigame/minigames/horseclientqa/mixin'/name for name in ['HiddenWindowMixin.java','SkipHardwareProbeMixin.java','SkipGlxCpuProbeMixin.java']]+[Path(__file__).with_name('HorsePreviewCaptureQA.java')]
    args=['--release','21','-encoding','UTF-8','-proc:none','-cp',cp,'-d',str(lab/'qa-classes')]+list(map(str,sources))
    (lab/'compile.args').write_text('\n'.join('"'+v.replace('\\','/')+'"' for v in args),encoding='utf-8')
    subprocess.run([str(jdk/'bin/javac.exe'),'@'+str(lab/'compile.args')],check=True)
    with zipfile.ZipFile(jar) as original,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for item in original.infolist():
            data=original.read(item.filename)
            if item.filename=='META-INF/neoforge.mods.toml':data+=b'\n[[mixins]]\nconfig="horse_client_qa.mixins.json"\n'
            z.writestr(item,data)
        z.writestr('horse_client_qa.mixins.json',json.dumps({'required':True,'minVersion':'0.8','package':'net.muxigame.minigames.horseclientqa.mixin','compatibilityLevel':'JAVA_21','client':['HiddenWindowMixin','SkipHardwareProbeMixin','SkipGlxCpuProbeMixin']+(['OfflineMcefMixin'] if a.cef_qa else []),'injectors':{'defaultRequire':1}}))
        for f in (lab/'qa-classes').rglob('*.class'):z.write(f,f.relative_to(lab/'qa-classes').as_posix())
    (lab/'config/fml.toml').write_text('earlyWindowControl=false\nearlyWindowProvider=""\nversionCheck=false\ndisableConfigWatcher=true\nmaxThreads=2\n')
    (lab/'options.txt').write_text('lang:zh_cn\nguiScale:2\nmaxFps:30\nenableVsync:false\nonboardAccessibility:false\nsoundCategory_master:0.0\nfullscreen:false\npauseOnLostFocus:false\nrenderDistance:2\ntutorialStep:none\nsimulationDistance:5\ngraphicsMode:0\n')
    subs={'auth_player_name':'HorseClientQA131','auth_uuid':'b292eb5df4804df1809d1a1968e95f7d','auth_access_token':'0','version_name':'BatterMC5Remake','game_directory':str(lab),'assets_root':str(game/'assets'),'assets_index_name':meta['assetIndex']['id'],'clientid':'','auth_xuid':'','user_type':'legacy','version_type':'release','resolution_width':'1280','resolution_height':'720','natives_directory':str(lab/'natives'),'launcher_name':'horse-private-qa','launcher_version':'1','classpath':os.pathsep.join(map(str,libs)),'library_directory':str(game/'libraries'),'classpath_separator':os.pathsep}
    if False:
        if not a.stadium_qa or not a.backend_source:p.error('Points fixture requires arena and backend source')
        import uuid
        subs['auth_player_name']='10000';subs['auth_uuid']=str(uuid.UUID(bytes=hashlib.md5(b'OfflinePlayer:10000').digest(),version=3)).replace('-','')
    def expand(items):
        out=[]
        for item in items:
            if isinstance(item,dict):
                if not allowed(item.get('rules')):continue
                values=item['value'] if isinstance(item['value'],list) else [item['value']]
            else:values=[item]
            for v in values:
                for k,replacement in subs.items():v=v.replace('${'+k+'}',replacement)
                if '${' in v:raise ValueError(v)
                out.append(v)
        return out
    args=['-Xms512M','-Xmx2G','-XX:ActiveProcessorCount=2','-Dfile.encoding=UTF-8','-Dmuxi.horse.prototype=true','-Dmuxi.horse.clientQa=true','-Dhttp.proxyHost=127.0.0.1','-Dhttp.proxyPort=9','-Dhttps.proxyHost=127.0.0.1','-Dhttps.proxyPort=9']+expand(meta['arguments']['jvm'])+[meta['mainClass']]+expand(meta['arguments']['game'])
    if a.stadium_qa:args=[v.replace('-Dmuxi.horse.clientQa=true','-Dmuxi.horse.clientQa=false') for v in args if v!='-Dmuxi.horse.prototype=true'];args.insert(0,'-Dmuxi.horse.stadiumClientQa=true')
    args=[v for v in args if v!='-Dmuxi.horse.stadiumClientQa=true']
    args=[v for v in args if v not in ['-Dmuxi.horse.clientQa=true','-Dmuxi.horse.prototype=true']];args[:0]=['-Dmuxi.horse.previewCaptureQa=true']
    if a.cef_qa:args.insert(0,'-Dmuxi.horse.cefQa=true')
    if a.points_qa:args.insert(0,'-Dmuxi.horse.pointsQa=true')
    if a.performance_qa:args.insert(0,'-Dmuxi.horse.performanceQa=true')
    (lab/'launch.args').write_text('\n'.join('"'+v.replace('\\','/').replace('"','\\"')+'"' for v in args),encoding='utf-8')
    inputs={'lab':str(lab),'existingMods':[f.name for f in (lab/'mods').glob('*.jar')],'inputJarSha256':hashlib.sha256(jar.read_bytes()).hexdigest(),'heapMax':'2G','hiddenNativeWindow':True,'MCEF':a.cef_qa,'terminalJar':str(a.terminal_jar) if a.cef_qa else None,'terminalJarSha256':hashlib.sha256(a.terminal_jar.read_bytes()).hexdigest() if a.cef_qa else None,'productionWrites':False,'thirdPartyAssetsUploaded':False}
    (lab/'inputs.json').write_text(json.dumps(inputs,indent=2));(a.output/'client-latest.json').write_text(json.dumps(inputs,indent=2))
    env={k:v for k,v in os.environ.items() if not k.startswith('MUXI_') and k not in ('JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS')}
    current_java=int(subprocess.check_output(['powershell','-NoProfile','-Command',"@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'java.exe' }).Count"],text=True).strip())
    if current_java>=3:raise RuntimeError('Three other Java instances occupy the cap; capture not launched')
    with (lab/'boot.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([str(jdk/'bin/java.exe'),'@'+str(lab/'launch.args')],cwd=lab,env=env,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        (lab/'pid.json').write_text(json.dumps({'pid':proc.pid}));print(json.dumps({'pid':proc.pid,'lab':str(lab)}),flush=True)
        deadline=time.monotonic()+600
        while proc.poll() is None:
            if time.monotonic()>deadline-60:(lab/'request-normal-close.json').write_text('{}')
            if time.monotonic()>deadline:raise RuntimeError('Own client normal close timed out; no force kill')
            time.sleep(1)
    report=json.loads((lab/'preview-result.json').read_text(encoding='utf-8')) if (lab/'preview-result.json').exists() else {'success':False,'error':'No native report'}
    report.update(exitCode=proc.returncode,cleanExit=proc.returncode==0,lab=str(lab));(lab/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:report.get(k) for k in ('success','error','cleanExit','lab','mountedMaidDraws','MCEF','visualAppAcceptance','stablePodiumFrameP95Millis','frameIntervalP95Millis')}),flush=True)
    if not report.get('success') or proc.returncode:raise SystemExit(1)
if __name__=='__main__':main()
