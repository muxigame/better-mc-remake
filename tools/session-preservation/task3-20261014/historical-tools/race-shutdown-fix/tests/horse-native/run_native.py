"""Bounded 131 engine QA. Existing dependencies only; private world, no deployment or asset upload."""
from pathlib import Path
import argparse, datetime, hashlib, json, os, shutil, subprocess, sys, zipfile

def split_framework(repo, lab, supplied=None):
    shared=repo.resolve().parent/'muxi-minigames'
    meta=json.loads((shared/'mod.json').read_text(encoding='utf-8'))
    jar=supplied.resolve() if supplied else shared/'build/libs'/('muxi-minigames-'+meta['version']+'.jar')
    if not jar.is_file():raise RuntimeError('Build sibling muxi-minigames before running game QA')
    if (lab/'mods').is_dir():shutil.copy2(jar,lab/'mods'/jar.name)
    return jar

def main():
    p=argparse.ArgumentParser();p.add_argument('--core-jar',type=Path);p.add_argument('--framework-jar',type=Path);p.add_argument('--start-qa',action='store_true');p.add_argument('--jar',type=Path);p.add_argument('--stadium-qa',action='store_true');p.add_argument('--lifecycle-qa',action='store_true');p.add_argument('--reuse-qa',action='store_true');p.add_argument('--reuse-fault-qa',action='store_true');p.add_argument('--asset-qa',action='store_true');p.add_argument('--course-qa',action='store_true');p.add_argument('--disabled-smoke',action='store_true');p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--server',type=Path,default=Path(r'C:\Users\ranzh\workspace\dev\muxigame\bmc5server'));p.add_argument('--client',type=Path,default=Path(r'C:\Users\ranzh\workspace\dev\muxigame\_client_test\game'));p.add_argument('--jdk',type=Path,default=Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-<PRIVATE_HOST>+1'));a=p.parse_args()
    if a.reuse_fault_qa:a.reuse_qa=True
    if sum((a.asset_qa,a.course_qa,a.disabled_smoke,a.stadium_qa,a.reuse_qa,a.lifecycle_qa,a.start_qa))>1:p.error('Select exactly one private QA mode')
    repo=a.repo.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    lab=out/('horse-native-'+datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S'));lab.mkdir();(lab/'HORSE_QA_ONLY').write_text('Generated horses and synthetic players only\n');(lab/'mods').mkdir();(lab/'config').mkdir()
    # Server bootstrap locates mapped jars under gameDir/libraries. Own junction, target is read-only input.
    subprocess.run(['powershell','-NoProfile','-Command',"New-Item -ItemType Junction -Path '"+str(lab/'libraries').replace("'","''")+"' -Target '"+str(a.server/'libraries').replace("'","''")+"' | Out-Null"],check=True)
    framework=split_framework(repo,lab,a.framework_jar)
    jar=a.jar.resolve() if a.jar else repo/'build/libs'/('muxi-horse-racing-'+json.loads((repo/'mod.json').read_text(encoding='utf-8'))['version']+'.jar');target=lab/'mods'/jar.name;shutil.copy2(jar,target)
    dependencies=list(dict.fromkeys([a.client/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-client.jar',a.client/'libraries/net/minecraft/client/1.21.1-20240808.144430/client-1.21.1-20240808.144430-srg.jar']+list((a.server/'libraries').rglob('*.jar'))))
    classes=lab/'qa-classes';classes.mkdir();sources=list((repo/'tests/horse-native').glob('*.java'));cp=os.pathsep.join(map(str,[framework,target]+dependencies))
    args=['--release','21','-encoding','UTF-8','-proc:none','-cp',cp,'-d',str(classes)]+list(map(str,sources))
    argfile=lab/'compile.args';argfile.write_text('\n'.join('"'+x.replace('\\','/')+'"' for x in args),encoding='utf-8');subprocess.run([str(a.jdk/'bin/javac.exe'),'-J-XX:ActiveProcessorCount=2','@'+str(argfile)],check=True)
    with zipfile.ZipFile(target,'a',zipfile.ZIP_DEFLATED) as z:
        for f in classes.rglob('*.class'):z.write(f,f.relative_to(classes).as_posix())
    for directory,pattern in ([] if a.disabled_smoke else [(a.server/'mods','*touhoulittlemaid-1.5.3*'),(a.client/'mods','*ysm-2.6.5*')]):
        sourcejar=next(directory.glob(pattern));shutil.copy2(sourcejar,lab/'mods'/sourcejar.name)
    if a.core_jar:
        shutil.copy2(a.core_jar,lab/'mods'/a.core_jar.name)
        installed=a.server/'mods'
        for pattern in ('waystones-*.jar','balm-*.jar'):
            sourcejar=next(installed.glob(pattern));shutil.copy2(sourcejar,lab/'mods'/sourcejar.name)
    if a.course_qa:
        for pattern in ('Oh-The-Biomes-*.jar','Oh-The-Trees-*.jar','Corgilib-*.jar','TerraBlender-*.jar','geckolib-*.jar'):
            sourcejar=next((a.server/'mods').glob(pattern));shutil.copy2(sourcejar,lab/'mods'/sourcejar.name)
    (lab/'config/fml.toml').write_text('versionCheck=false\ndisableConfigWatcher=true\nmaxThreads=2\n',encoding='utf-8')
    (lab/'eula.txt').write_text('eula=true\n')
    (lab/'server.properties').write_text('server-ip=127.0.0.1\nserver-port=0\nonline-mode=false\nlevel-name=horse-qa-world\nlevel-type=minecraft:flat\ngenerator-settings={"layers":[{"block":"minecraft:stone","height":64},{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains","structure_overrides":[]}\nspawn-protection=0\nview-distance=2\nsimulation-distance=2\nsync-chunk-writes=true\nmax-tick-time=60000\n',encoding='utf-8')
    launch=lab/'launch.args';launch.write_text('-Xms512M\n-Xmx3G\n-XX:ActiveProcessorCount=2\n-Dfile.encoding=UTF-8\n-Dmuxi.horse.prototype=true\n-Dmuxi.horse.nativeQa=true\n'+(a.server/'libraries/net/neoforged/neoforge/21.1.250/win_args.txt').read_text(encoding='utf-8')+'\n--nogui\n',encoding='utf-8')
    if a.disabled_smoke:launch.write_text(launch.read_text(encoding='utf-8').replace('-Dmuxi.horse.prototype=true','-Dmuxi.horse.prototype=false\n-Dmuxi.horse.disabledQa=true'),encoding='utf-8')
    if a.course_qa:launch.write_text(launch.read_text(encoding='utf-8').replace('-Dmuxi.horse.nativeQa=true','-Dmuxi.horse.nativeQa=false\n-Dmuxi.horse.courseQa=true'),encoding='utf-8')
    if a.stadium_qa:launch.write_text(launch.read_text(encoding='utf-8').replace('-Dmuxi.horse.prototype=true\n','').replace('-Dmuxi.horse.nativeQa=true','-Dmuxi.horse.nativeQa=false\n-Dmuxi.horse.stadiumQa=true'),encoding='utf-8')
    if a.start_qa:launch.write_text(launch.read_text(encoding='utf-8').replace('-Dmuxi.horse.prototype=true\n','').replace('-Dmuxi.horse.nativeQa=true','-Dmuxi.horse.nativeQa=false\n-Dmuxi.horse.startQa=true'),encoding='utf-8')
    if a.lifecycle_qa:launch.write_text(launch.read_text(encoding='utf-8').replace('-Dmuxi.horse.prototype=true\n','').replace('-Dmuxi.horse.nativeQa=true','-Dmuxi.horse.nativeQa=false\n-Dmuxi.horse.lifecycleQa=true'),encoding='utf-8')
    if a.reuse_qa:launch.write_text(launch.read_text(encoding='utf-8').replace('-Dmuxi.horse.prototype=true\n','').replace('-Dmuxi.horse.nativeQa=true','-Dmuxi.horse.nativeQa=false\n-Dmuxi.horse.reuseQa=true'),encoding='utf-8')
    if a.reuse_fault_qa:launch.write_text('-Dmuxi.horse.reuseFaultQa=true\n'+launch.read_text(encoding='utf-8'),encoding='utf-8')
    if not a.stadium_qa and not a.reuse_qa and not a.lifecycle_qa and not a.start_qa:launch.write_text('-Dmuxi.horse.course=prototype-loop\n'+launch.read_text(encoding='utf-8'),encoding='utf-8')
    if a.asset_qa:launch.write_text(launch.read_text(encoding='utf-8').replace('-Dmuxi.horse.nativeQa=true','-Dmuxi.horse.nativeQa=false\n-Dmuxi.horse.assetQa=true\n-Dmuxi.horse.assetLab=true'),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.startswith('MUXI_') and k not in ('JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS')}
    receipt={'lab':str(lab),'server':str(a.server),'existingMods':[f.name for f in (lab/'mods').glob('*.jar')],'inputJarSha256':hashlib.sha256(jar.read_bytes()).hexdigest(),'qaJarSha256':hashlib.sha256(target.read_bytes()).hexdigest(),'heapMax':'3G','network':'loopback ephemeral port','thirdPartyAssetsCopied':False,'productionWrites':False}
    (lab/'inputs.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');(out/'latest.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    with (lab/'boot.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([str(a.jdk/'bin/java.exe'),'@'+str(launch)],cwd=str(lab),env=env,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        (lab/'pid.json').write_text(json.dumps({'pid':proc.pid,'owner':'horse-native-start-regression','instanceRoot':str(lab)}),encoding='utf-8');print(json.dumps({'launched':proc.pid,'lab':str(lab)}),flush=True)
        import time,psutil
        (lab/'pid.json').write_text(json.dumps({'pid':proc.pid,'processCreateTime':psutil.Process(proc.pid).create_time(),'parentPid':os.getpid(),'owner':'horse-native-start-regression','instanceRoot':str(lab)}),encoding='utf-8')
        deadline=time.monotonic()+(1200 if a.reuse_qa else 600);requested=False
        while proc.poll() is None:
            if not requested and ((lab/'normal-stop.request').exists() or time.monotonic()>=deadline):
                proc.stdin.write(b'stop\n');proc.stdin.flush();requested=True;deadline=time.monotonic()+60
            if requested and time.monotonic()>=deadline:raise RuntimeError('Own server normal stop timed out; no forced termination performed')
            time.sleep(.2)
        code=proc.returncode
    result=json.loads((lab/'result.json').read_text(encoding='utf-8')) if (lab/'result.json').exists() else {'success':False,'error':'No engine report'}
    if result.get('success') and code==0 and not a.disabled_smoke and not a.course_qa and not a.stadium_qa and not a.lifecycle_qa and not a.start_qa:
        (lab/'result-first.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        launch.write_text(launch.read_text(encoding='utf-8').replace('-Dmuxi.horse.nativeQa=true','-Dmuxi.horse.nativeQa=true\n-Dmuxi.horse.resumeQa=true'),encoding='utf-8')
        if a.reuse_qa:launch.write_text('-Dmuxi.horse.reuseResumeQa=true\n'+launch.read_text(encoding='utf-8'),encoding='utf-8')
        if a.asset_qa:launch.write_text(launch.read_text(encoding='utf-8')+'\n',encoding='utf-8');launch.write_text('-Dmuxi.horse.assetResumeQa=true\n'+launch.read_text(encoding='utf-8'),encoding='utf-8')
        (lab/'result.json').unlink()
        with (lab/'restart-boot.log').open('w',encoding='utf-8') as log:
            resume=subprocess.Popen([str(a.jdk/'bin/java.exe'),'@'+str(launch)],cwd=str(lab),env=env,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            (lab/'restart-pid.json').write_text(json.dumps({'pid':resume.pid}),encoding='utf-8');print(json.dumps({'restartLaunched':resume.pid,'lab':str(lab)}),flush=True)
            try:code=resume.wait(timeout=180)
            except subprocess.TimeoutExpired:
                resume.stdin.write(b'stop\n');resume.stdin.flush();code=resume.wait(timeout=60)
        second=json.loads((lab/'result.json').read_text(encoding='utf-8')) if (lab/'result.json').exists() else {'success':False,'error':'No restart engine report'}
        if a.reuse_qa:result['postRestartPool']=second.get('pool')
        result['checks'].extend(second.get('checks',[]));result.update(success=second.get('success',False),error=second.get('error',''),actualJvmRestart=True)
    result.update(exitCode=code,cleanExit=code==0,lab=str(lab));(lab/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'success':result.get('success'),'checks':len(result.get('checks',[])),'error':result.get('error'),'exitCode':code,'lab':str(lab)},ensure_ascii=False),flush=True)
    if not result.get('success') or code:sys.exit(1)
if __name__=='__main__':main()
