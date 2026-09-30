"""Boot disposable loopback-only lab using installed jars. Never modifies the live server."""
from pathlib import Path
from datetime import datetime
import json, os, shlex, shutil, socket, subprocess, sys
from zipfile import ZipFile, ZIP_DEFLATED
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT/'muxi-game-core'))
import build as core_build

def main():
    server=ROOT/'bmc5server'; lab=HERE/'build'/('smoke-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    lab.mkdir(parents=True);(lab/'mods').mkdir();(lab/'config').mkdir()
    dependencies=[]
    for pattern in ['*champions*.jar','architectury-*.jar','muxi-game-core-*.jar','kubejs-*.jar','rhino-*.jar']:
        found=list((server/'mods').glob(pattern))
        if len(found)!=1:raise RuntimeError('Ambiguous lab dependency '+pattern)
        dependencies+=found;shutil.copy2(found[0],lab/'mods'/found[0].name)
    companion=HERE/'build/muxi-champion-companions-1.0.4.jar';shutil.copy2(companion,lab/'mods'/companion.name)
    shutil.copy2(HERE/'config-examples/muxi-champion-companions-server.toml',
        lab/'config/muxi-champion-companions-server.toml')
    health=ROOT/'better-mc-remake/pack/patches/champions-health/muxi_champions_health.js'
    (lab/'kubejs/server_scripts').mkdir(parents=True);shutil.copy2(health,lab/'kubejs/server_scripts'/health.name)
    golem=ROOT/'better-mc-remake/pack/patches/combat-balance/muxi_golem_combat_balance.js'
    shutil.copy2(golem,lab/'kubejs/server_scripts'/golem.name)
    (lab/'config/muxi-game-core.json').write_text('{"schema":1,"features":{}}')
    (lab/'config/neoforge-server.toml').write_text('advertiseDedicatedServerToLan = false\n')
    shutil.copy2(server/'eula.txt',lab/'eula.txt')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    (lab/'server.properties').write_text('server-ip=127.0.0.1\nserver-port='+str(port)+'\nonline-mode=false\nlevel-name=qa-world\n'
       'level-type=minecraft:flat\ngenerator-settings={"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains"}\nview-distance=2\nsimulation-distance=2\nmax-players=1\nenable-rcon=false\nspawn-protection=0\n')
    compiler,runtime=core_build.java_tools(None)
    java_override=os.environ.get('MUXI_SMOKE_JAVA')
    java21=ROOT/'perf-lab/java21/jdk-21.0.2/bin/java.exe'
    runtime=Path(java_override) if java_override else java21
    if not runtime.exists():runtime=java21
    jars=sorted((server/'libraries').rglob('*.jar'));neo=server/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-server.jar'
    mapped=next(p for p in jars if p.name=='server-1.21.1-20240808.144430-srg.jar')
    cp=os.pathsep.join(map(str,[companion,neo,mapped,*[p for p in jars if '/net/minecraft/' not in p.as_posix()],*dependencies]))
    classes=lab/'test-classes'
    core_build.compile_java(compiler,sorted((HERE/'tests/java').rglob('*.java')),classes,cp,lab/'compile.args')
    with ZipFile(lab/'mods/muxi-companions-smoke-only.jar','w',ZIP_DEFLATED) as z:
        z.writestr('META-INF/neoforge.mods.toml','modLoader="javafml"\nloaderVersion="[4,)"\nlicense="MIT"\n[[mods]]\nmodId="muxi_companions_smoke"\nversion="1.0.0"\n[[mixins]]\nconfig="muxi_companions_smoke.mixins.json"\n')
        z.writestr('muxi_companions_smoke.mixins.json',json.dumps({
            'required':True,'minVersion':'0.8','package':'net.muxigame.championcompanions.smoke.mixin',
            'compatibilityLevel':'JAVA_21','mixins':['DisableNetworkMixin']},indent=2)+'\n')
        for p in classes.rglob('*.class'):z.write(p,p.relative_to(classes).as_posix())
    launch=shlex.split((server/'libraries/net/neoforged/neoforge/21.1.250/win_args.txt').read_text())
    libraries=(server/'libraries').as_posix()
    launch=[s.replace('libraries/',libraries+'/').replace('-DlibraryDirectory=libraries','-DlibraryDirectory='+libraries) for s in launch]
    selector_provider=os.environ.get('MUXI_SMOKE_SELECTOR_PROVIDER')
    jvm_options=['-Xms512M','-Xmx2G','-XX:ActiveProcessorCount=4','-Dfile.encoding=UTF-8']
    if selector_provider:jvm_options.append('-Djava.nio.channels.spi.SelectorProvider='+selector_provider)
    command=[str(runtime),*jvm_options,*launch,'--nogui']
    print('ISOLATED LAB',lab,flush=True)
    with (lab/'boot.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen(command,cwd=lab,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        try:code=process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            process.terminate();process.wait(timeout=15);code=999
    resultpath=lab/'companions-smoke-result.json'
    result=json.loads(resultpath.read_text()) if resultpath.exists() else {'success':False,'error':'No result file'}
    result['exitCode']=code;result['lab']=str(lab);print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result.get('success') or code:
        print('\n'.join((lab/'boot.log').read_text(encoding='utf-8',errors='replace').splitlines()[-65:]));raise SystemExit(1)
    (HERE/'build/last-smoke-result.json').write_text(json.dumps(result,indent=2))

if __name__=='__main__':main()
