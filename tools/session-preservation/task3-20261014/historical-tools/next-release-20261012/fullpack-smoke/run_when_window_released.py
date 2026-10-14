"""Prepared only. Invoke after the parent explicitly releases the isolated MC window."""
from pathlib import Path
import sys,json,hashlib,psutil,os,zipfile,shutil
if sys.argv[1:] not in [['--window-approved'],['--window-approved','--recovery'],['--window-approved','--warm-retry'],['--window-approved','--default-12g']]:
 raise SystemExit('Not started: explicit parent test-window approval is required.')
root=Path(__file__).resolve().parent;workspace=root.parents[1];project=workspace/'q'
for p in psutil.process_iter(['pid','name']):
 if (p.info['name'] or '').lower() in ['java.exe','javaw.exe','jcef_helper.exe']:
  raise SystemExit(f'Window occupied by PID {p.pid}; inspect ownership, do not terminate.')
f=json.loads((root/'frozen-fullpack-inputs.json').read_text(encoding='utf-8'))
for side,info in f['sides'].items():
 for row in info['files']:
  p=root/side/row['path']
  if hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise SystemExit('Frozen input drift: '+str(p))
sys.path.insert(0,str(project/'scripts'));import local_mc_debug as qa
original_template=qa.copy_template
def template_with_private_cache(template,lab):
 original_template(template,lab)
 if lab.name=='host' and ('--warm-retry' in sys.argv or '--recovery' in sys.argv):
  source=project/'build/local-mc-debug/r3/host/config/yes_steve_model/cache'
  dest=lab/'config/yes_steve_model/cache'
  shutil.copytree(source,dest,dirs_exist_ok=True)
  (lab.parent/'private-cache-fixture.json').write_text(json.dumps(dict(source=str(source),sourceRun='7f79f130865b46e68b3847ee6d3ebfa2',userCacheAccess=False,productionMutation=False,files=[dict(path=str(p.relative_to(source)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in source.rglob('*') if p.is_file()]),indent=2))
qa.copy_template=template_with_private_cache
original_compile=qa.rt.compile_agent
def compile_combined(proj,instance,jdk,classpath):
 extra=list((root/'client/mods').glob('*.jar'));nested=root/'nested-compile';nested.mkdir(exist_ok=True)
 for p in extra[:]:
  if not any(x in p.name.lower() for x in ['mcef','xaero']):continue
  with zipfile.ZipFile(p) as z:
   for n in z.namelist():
    if n.endswith('.jar'):
     dest=nested/Path(n).name;dest.write_bytes(z.read(n));extra.append(dest)
 jar=original_compile(proj,instance,jdk,classpath+os.pathsep+os.pathsep.join(map(str,extra)))
 with zipfile.ZipFile(jar,'a') as z:
  meta=z.read('META-INF/neoforge.mods.toml').decode()+'\n[[mixins]]\nconfig="combined.shopqa.mixins.json"\n'
  z.writestr('META-INF/neoforge.mods.toml',meta)
  z.writestr('combined.shopqa.mixins.json',json.dumps({'required':True,'package':'net.muxigame.minigames.horseqa.mixin','compatibilityLevel':'JAVA_21','client':['ShopBrowserProbeMixin','ShopCefProfileMixin'],'mixins':[],'injectors':{'defaultRequire':1}}))
 shutil.copy2(root/'shop-client.js',instance/'shop-client.js')
 return jar
qa.rt.compile_agent=compile_combined
repo=Path('C:/Users/ranzh/workspace/dev/muxigame');java=Path('C:/Users/ranzh/Documents/Codex/terminal-integration-task6-20261001/tools/jdk/jdk-<PRIVATE_HOST>+1')
args=['run','--project-root',str(project),'--instance-root',str(project/'build/local-mc-debug/core-tower-fullpack-20261012'),'--server-runtime',str(repo/'bmc5server'),'--client-game',str(repo/'_client_test/game'),'--java-home',str(java),'--unix-temp',str(Path(os.environ['TEMP'])/'core-tower-qa'),'--port','25847','--clients','1','--mode','hold','--hold-seconds','900','--accept-eula','--client-memory-mb','8192','--server-memory-mb','6144','--client-max-fps','30','--shutdown-timeout','180','--boot-timeout','900','--client-template',str(root/'client'),'--server-template',str(root/'server'),'--world',str(workspace/'runtime-tools/build/local-mc-debug/release1434-shutdownfix-smoke1/server/qa-world'),'--server-view-distance','20','--server-simulation-distance','8','--client-render-distance','10','--client-simulation-distance','8','--diagnostic-logs','--jvm-property','local.debug.lightweightStatus=true','--jvm-property','muxi.materialMapPresize=true']
for side in ['client','server']:
 for p in sorted((root/side/'mods').glob('*.jar')):args+=['--'+side+'-mod',str(p)]
args[args.index('--hold-seconds')+1]='5400'
recovery='--recovery' in sys.argv
args[args.index('--instance-root')+1]=str(project/'build/local-mc-debug'/('r8' if recovery else 'r7' if '--warm-retry' in sys.argv else 'r3'))
args[args.index('--client-memory-mb')+1]='16384'
if '--default-12g' in sys.argv:
 args[args.index('--instance-root')+1]=str(project/'build/local-mc-debug/r9-default12g')
 args[args.index('--client-memory-mb')+1]='12288'
 if Path(args[args.index('--instance-root')+1]).exists():raise SystemExit('r9 already exists; preserve evidence and choose a new reviewed instance name.')
if recovery:args[args.index('--world')+1]=str(project/'build/local-mc-debug/r7/server/qa-world')
args+=['--jvm-property','qa.tower.assets='+str(root/'server/config/yes_steve_model/builtin/wine_fox')]
args+=['--client-name','10000','--mod',str(root/'tower-visual-qa.jar'),'--jvm-property','muxi.horse.shopClientQa=true']
raise SystemExit(qa.main(args))
