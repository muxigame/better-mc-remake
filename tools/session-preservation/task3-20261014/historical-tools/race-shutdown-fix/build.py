"""Offline build using installed Minecraft/NeoForge libraries; never launches Minecraft."""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess, tempfile, zipfile

ROOT=Path(__file__).resolve().parent
def build(server,client_game,java_home=None,framework_jar=None):
    meta=json.loads((ROOT/'mod.json').read_text(encoding='utf-8'))
    javac=Path(java_home)/'bin/javac.exe' if java_home else Path(shutil.which('javac') or 'C:/Program Files/Java/jdk-21/bin/javac.exe')
    java=javac.with_name('java.exe')
    neo=server/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-server.jar'
    mapped=server/'libraries/net/minecraft/server/1.21.1-20240808.144430/server-1.21.1-20240808.144430-srg.jar'
    client=[client_game/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-client.jar',client_game/'libraries/net/minecraft/client/1.21.1-20240808.144430/client-1.21.1-20240808.144430-srg.jar']
    libraries=client+[neo,mapped]+[p for p in sorted((server/'libraries').rglob('*.jar')) if '/net/minecraft/' not in p.as_posix() and p!=neo]
    if not javac.is_file() or any(not p.is_file() for p in libraries):raise ValueError('JDK 21 and installed server/client libraries required')
    shared=ROOT.parent/'muxi-minigames'
    shared_version=json.loads((shared/'mod.json').read_text(encoding='utf-8'))['version'] if framework_jar is None else None
    framework_jar=framework_jar or shared/'build/libs'/('muxi-minigames-'+shared_version+'.jar')
    if not framework_jar.is_file():raise ValueError('Build sibling muxi-minigames first or supply --framework-jar')
    libraries.append(framework_jar)
    libraries.extend(sorted((client_game/'libraries/org/lwjgl').rglob('*.jar')))
    # Existing optional adapters are compile inputs only, never bundled.
    for pattern in ('*touhoulittlemaid-1.5.3*','*champions-neoforge-<PRIVATE_HOST>*'):
        libraries.append(next((server/'mods').glob(pattern)))
    output=ROOT/'build';(output/'libs').mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output,prefix='compile-') as raw:
        temp=Path(raw);classes=temp/'classes';classes.mkdir()
        cp=os.pathsep.join(map(str,libraries))
        def compile(sources,out,classpath):
            out.mkdir(exist_ok=True);args=['--release','21','-encoding','UTF-8','-proc:none','-cp',classpath,'-d',str(out),*map(str,sources)]
            file=temp/(out.name+'.args');file.write_text('\n'.join('"'+v.replace('\\','/').replace('"','\\"')+'"' for v in args),encoding='utf-8')
            subprocess.run([str(javac),'-J-XX:ActiveProcessorCount=2','@'+str(file)],check=True)
        compile(sorted((ROOT/'src/main/java').rglob('*.java')),classes,cp)
        entries={'META-INF/LICENSE':(ROOT/'LICENSE').read_bytes()}
        for base in (classes,ROOT/'src/main/resources'):
            for file in base.rglob('*'):
                if file.is_file():entries[file.relative_to(base).as_posix()]=file.read_bytes().replace(b'${mod_version}',meta['version'].encode()) if file.suffix=='.toml' else file.read_bytes()
        target=output/'libs'/f"muxi-horse-racing-{meta['version']}.jar"
        with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,data in sorted(entries.items()):
                info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(info,data)
        release={**meta,'artifact':target.name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'size':target.stat().st_size}
        (output/'release.json').write_text(json.dumps(release,indent=2)+'\n',encoding='utf-8');print(json.dumps(release));return target

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--server',type=Path,required=True);parser.add_argument('--client-game',type=Path,required=True);parser.add_argument('--java-home',type=Path);parser.add_argument('--framework-jar',type=Path);args=parser.parse_args()
    build(args.server,args.client_game,args.java_home,args.framework_jar)
