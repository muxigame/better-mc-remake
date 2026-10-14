from pathlib import Path
import importlib.util,json,os,subprocess,zipfile,hashlib
root=Path(__file__).resolve().parent
project=Path(r'C:\Users\ranzh\Documents\Codex\2026-10-09\task-2\nmbl-playlist-compat')
spec=importlib.util.spec_from_file_location('readonly_private_builder',project/'build.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
for path,expected in builder.PINNED.items():assert builder.sha(path)==expected
core=root/'build/published-dependency/muxi-game-core-1.12.3-task14-qa.1.jar'
assert builder.sha(core)=='60c103c3cbd31dd647c7a5ebb0818aaae49ca72d1692e53df98d5f12224b5aeb'
out=root/'build/release-policy-candidate';classes=out/'classes';classes.mkdir(parents=True,exist_ok=True)
names=['MusicCompat','BurnCoordinator','BurnSources','OssBurnBackend','BurnCommitGuard','BurnScreenRuntime','BurnHttp','BurnProtocol','BurnProofVerifier','BurnNetwork','BurnServer','BurnPermissionScreen','BurnClientSession','BoundBurnBackend','PlatformDownloadSource','PlatformAccountSource','BetterLoginClientSource','BurnScreenMixin','BurnGateMixin','OssResolverMixin','OssEnderMixin','BurnMenuAccess','PrivatePlaybackProtocol','PrivatePlayback','PrivateStreamMixin','PrivateMediaReader','PrivatePlaybackTest','BurnSourcesPrivateTest','BoundBurnBackendTest','PlatformAccountSourceTest']
sources=[root/(n+'.java') for n in names];cp=builder.classpath()+os.pathsep+str(core)
for name in names:
    if name.endswith('Test'):continue
    folder='' if name=='MusicCompat' else 'mixin/' if name in {'BurnScreenMixin','BurnGateMixin','OssResolverMixin','OssEnderMixin','BurnMenuAccess','PrivateStreamMixin'} else 'burn/'
    installed=project/f'src/main/java/net/muxigame/musiccompat/{folder}{name}.java'
    assert installed.read_bytes()==(root/(name+'.java')).read_bytes(),'native source differs from reviewed staging'
args=out/'compile.args';builder.rt.argfile(args,['--release','21','-encoding','UTF-8','-proc:none','-classpath',cp,'-d',classes,*sources])
run=subprocess.run([str(builder.JDK/'bin/javac.exe'),'@'+str(args)],capture_output=True,text=True,encoding='utf-8',errors='replace')
(out/'compile.log').write_text(run.stdout+run.stderr,encoding='utf-8')
if run.returncode:print(run.stderr);raise SystemExit(run.returncode)
run=subprocess.run([str(builder.JDK/'bin/java.exe'),'-cp',str(classes)+os.pathsep+cp,'net.muxigame.musiccompat.burn.PrivatePlaybackTest'],capture_output=True,text=True,encoding='utf-8')
(out/'tests.log').write_text(run.stdout+run.stderr,encoding='utf-8');print(run.stdout)
assert run.returncode==0,run.stderr
tests=json.loads(run.stdout)
run=subprocess.run([str(builder.JDK/'bin/java.exe'),'-cp',str(classes)+os.pathsep+cp,'net.muxigame.musiccompat.burn.BurnSourcesPrivateTest'],capture_output=True,text=True,encoding='utf-8')
print(run.stdout);assert run.returncode==0,run.stderr;source_tests=json.loads(run.stdout)
run=subprocess.run([str(builder.JDK/'bin/java.exe'),'-cp',str(classes)+os.pathsep+cp,'net.muxigame.musiccompat.burn.BoundBurnBackendTest'],capture_output=True,text=True,encoding='utf-8')
print(run.stdout);assert run.returncode==0,run.stderr;binding_tests=json.loads(run.stdout)
run=subprocess.run([str(builder.JDK/'bin/java.exe'),'-cp',str(classes)+os.pathsep+cp,'net.muxigame.musiccompat.burn.PlatformAccountSourceTest'],capture_output=True,text=True,encoding='utf-8')
(out/'platform-tests.log').write_text(run.stdout+run.stderr,encoding='utf-8')
print(run.stdout);assert run.returncode==0,run.stderr;platform_tests=json.loads(run.stdout)
jar=out/'muxi-music-compat-0.4.0-release-candidate.1.jar'
metadata=builder.META
mixins={'required':True,'minVersion':'0.8','compatibilityLevel':'JAVA_21','package':'net.muxigame.musiccompat.mixin','mixins':['BurnMenuAccess','BurnGateMixin','OssResolverMixin','OssEnderMixin'],'client':['BurnScreenMixin','PrivateStreamMixin'],'injectors':{'defaultRequire':1}}
with zipfile.ZipFile(jar,'w',zipfile.ZIP_DEFLATED) as archive:
    def put(n,data):
        z=zipfile.ZipInfo(n,(2026,10,12,0,0,0));z.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(z,data)
    put('META-INF/neoforge.mods.toml',metadata);put('muxi_music_compat.mixins.json',json.dumps(mixins))
    for p in classes.rglob('*.class'):
        if not any(p.stem.startswith(n) for n in names if n.endswith('Test')):put(p.relative_to(classes).as_posix(),p.read_bytes())
    for n in ['LICENSE','NOTICE.txt']:put(n,(project/n).read_bytes())
record={'artifact':str(jar),'version':'0.4.0-release-candidate.1','sha256':hashlib.sha256(jar.read_bytes()).hexdigest(),'bytes':jar.stat().st_size,'compiledSources':len(names),'tests':tests,'sourceTests':source_tests,'bindingTests':binding_tests,'platformTests':platform_tests,'nativeMcAccepted':False,'realOssCalls':0,'productionDeployed':False,'candidate2Preserved':True,'previousPrivateCandidatePreserved':True}
(out/'artifact.json').write_text(json.dumps(record,indent=2),encoding='utf-8');print(json.dumps(record,indent=2))
