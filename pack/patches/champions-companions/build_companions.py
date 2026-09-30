"""Offline scoped build. Does not rebuild or overwrite muxi-game-core's WIP."""
from pathlib import Path
import hashlib, json, os, subprocess, sys, tempfile
from zipfile import ZipFile, ZIP_DEFLATED

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT/'muxi-game-core'))
import build as core_build

def main():
    subprocess.run([sys.executable,str(HERE/'generate_mixins.py')],check=True)
    server=ROOT/'bmc5server'
    jars=sorted((server/'libraries').rglob('*.jar'))
    mapped=next(p for p in jars if p.name=='server-1.21.1-20240808.144430-srg.jar')
    neo=server/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-server.jar'
    mods=[]
    for pattern in ['*champions*.jar','architectury-*.jar','muxi-game-core-*.jar','*touhoulittlemaid*.jar']:
        found=list((server/'mods').glob(pattern))
        if len(found)!=1: raise RuntimeError('Ambiguous installed dependency: '+pattern)
        mods+=found
    compiler,runtime=core_build.java_tools(None)
    target=HERE/'build'; target.mkdir(exist_ok=True)
    cp=os.pathsep.join(map(str,[neo,mapped,*[p for p in jars if '/net/minecraft/' not in p.as_posix()],*mods]))
    with tempfile.TemporaryDirectory(prefix='compile-',dir=target) as tmp:
        tmp=Path(tmp); classes=tmp/'classes'
        core_build.compile_java(compiler,sorted((HERE/'src/main/java').rglob('*.java')),classes,cp,tmp/'args.txt')
        artifact=target/'muxi-champion-companions-1.0.4.jar'
        with ZipFile(artifact,'w',ZIP_DEFLATED) as z:
            for base in [classes,HERE/'src/main/resources']:
                for p in sorted(base.rglob('*')):
                    if p.is_file(): z.write(p,p.relative_to(base).as_posix())
    print(json.dumps({'artifact':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),'size':artifact.stat().st_size}))

if __name__=='__main__': main()
