"""Offline deterministic build against supplied server libraries and merged Core."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess,sys,tempfile,zipfile
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT/'muxi-game-core'))
import build as core_build

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',type=Path,required=True)
    parser.add_argument('--java-home',type=Path,required=True)
    parser.add_argument('--core-jar',type=Path,required=True)
    args=parser.parse_args()
    subprocess.run([sys.executable,str(HERE/'generate_mixins.py')],check=True)
    server=args.server;jars=sorted((server/'libraries').rglob('*.jar'))
    mapped=next(p for p in jars if p.name=='server-1.21.1-20240808.144430-srg.jar')
    neo=server/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-server.jar'
    mods=[args.core_jar]
    for pattern in ['*champions*.jar','architectury-*.jar','*touhoulittlemaid*.jar']:
        found=[p for p in (server/'mods').glob(pattern) if not p.name.startswith('muxi-champion-companions-')]
        if len(found)!=1:raise RuntimeError('Ambiguous installed dependency: '+pattern)
        mods+=found
    compiler,_=core_build.java_tools(args.java_home)
    target=HERE/'build';target.mkdir(exist_ok=True)
    cp=os.pathsep.join(map(str,[neo,mapped,*[p for p in jars if '/net/minecraft/' not in p.as_posix()],*mods]))
    with tempfile.TemporaryDirectory(prefix='compile-',dir=target) as raw:
        tmp=Path(raw);classes=tmp/'classes'
        core_build.compile_java(compiler,sorted((HERE/'src/main/java').rglob('*.java')),classes,cp,tmp/'args.txt')
        artifact=target/'muxi-champion-companions-1.0.5.jar';entries={}
        for base in [classes,HERE/'src/main/resources']:
            for p in base.rglob('*'):
                if p.is_file():entries[p.relative_to(base).as_posix()]=p.read_bytes()
        with zipfile.ZipFile(artifact,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in sorted(entries.items()):
                info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(info,data)
    release={'id':'muxi_champion_companions','version':'1.0.5','artifact':artifact.name,'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),'size':artifact.stat().st_size}
    (target/'release.json').write_text(json.dumps(release,indent=2),encoding='utf-8');print(json.dumps(release))

if __name__=='__main__':main()
