from pathlib import Path
from datetime import datetime,timezone
import sys,os,json,shutil,socket,subprocess,time,uuid,argparse,hashlib,threading
W=Path('C:\\Users\\ranzh\\workspace\\dev\\muxigame\\muxi-zombie-challenge\\build\\native-network-work')
OUT=Path('C:\\Users\\ranzh\\Documents\\Codex\\2026-10-11\\task-3\\next-release-20261012\\zombie-fifth-candidate')
PROJECT=Path('C:\\Users\\ranzh\\Documents\\Codex\\2026-10-11\\task-3\\q')
LOCK=json.loads((OUT/"FIVE-MODULE-PRODUCT-LOCK.json").read_text(encoding="utf-8"))
TOOL=Path(r'C:\Users\ranzh\workspace\dev\muxigame\better-mc-remake')
sys.path.insert(0,str(TOOL/'scripts'))
import local_mc_runtime as rt
import local_mc_debug as common
parser=argparse.ArgumentParser();parser.add_argument('--window-approved',action='store_true');parser.add_argument('--product-inputs',type=Path,default=W/'ai-native-product-inputs.json');parser.add_argument('--fullpack-base',type=Path);parser.add_argument('--suite',choices=['zombie','joint'],default='joint');parser.add_argument('--qa-fixture',choices=['corrected','equipment-path','grenade-pair','ai','historical'],default='corrected');parser.add_argument('--core-jar',type=Path);parser.add_argument('--core-sha256');parser.add_argument('--outbreak-only',action='store_true');parser.add_argument('--lab-prefix',default='zqa');args=parser.parse_args()
if args.window_approved and '--product-inputs' not in sys.argv:raise SystemExit('Supply parent-approved current unified-pack input hashes via --product-inputs')
if args.window_approved and args.fullpack_base is None:raise SystemExit('Supply parent-approved healthy private full-pack baseline via --fullpack-base')
if args.window_approved and (args.core_jar is None or not args.core_sha256):raise SystemExit('Supply parent-approved current Core JAR and full hash explicitly')
if not args.window_approved:raise SystemExit("MC window must be explicitly assigned before running this follow-up fixture")
if args.qa_fixture!="ai" or args.suite!="zombie":raise SystemExit("Follow-up runner supports only zombie AI")
qaReceipt=json.loads((W/"ammo-inventory-offline/BUILD-RECEIPT.json").read_text())
inputs=json.loads(args.product_inputs.read_text(encoding='utf-8'))
assert inputs['muxi-zombie-challenge']['sha256']==qaReceipt['candidate']['sha256'],'The native fixture must run the exact compiled ammunition candidate'
for row in qaReceipt["qaArtifacts"].values():
 assert hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest()==row["sha256"],"QA fixture artifact drift"
publishedRosterPath=W/'ammo-inventory-offline/current-pack/PUBLISHED-MOD-ROSTER.json'
auditPath=W/'ammo-inventory-offline/dependency-audit/AUDIT.json'
audit=json.loads(auditPath.read_text(encoding='utf-8'))
assert audit['offlineAuditPassed'] and hashlib.sha256(publishedRosterPath.read_bytes()).hexdigest()==audit['rosterSha256'],'Frozen dependency/channel audit invalid or roster drifted'
prior=rt.read_json(W/'zombie-full-network-prepared.json');old=args.fullpack_base or Path(prior['home'])
previous=rt.read_json(old/'run-result.json')
if not previous or previous.get('exitCodes',{}).get('server') is None:raise SystemExit('Own previous server must exit before copying its private world')
import psutil
if not previous.get('normalExit'):raise SystemExit('Archived own full pack lacks complete normal exit receipt')
S=Path(r'C:\Users\ranzh\workspace\dev\muxigame\bmc5server');G=Path(r'C:\Users\ranzh\workspace\dev\muxigame\_client_test\game')
J=Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-<PRIVATE_HOST>+1')
capacity=rt.resources();capacity['cpuSamples']=[psutil.cpu_percent(interval=1) for _ in range(3)];capacity['cpuPercent']=sum(capacity['cpuSamples'])/len(capacity['cpuSamples']);required=int((6144+2*12288)*1.25)+2048
if capacity['minecraftProcesses'] or any((p.info['name'] or '').lower()=='jcef_helper.exe' for p in psutil.process_iter(['name'])):raise SystemExit('Assigned MC window is occupied; no new Java process started')
if capacity['cpuPercent']>=85:raise SystemExit('Measured CPU average >=85%; defer full-pack startup without changing watchdog: '+json.dumps(capacity['cpuSamples']))
assert capacity['availableMemoryMiB']>=required,'Insufficient measured MC resource reservation'
print(json.dumps({'capacity':capacity,'requestedClients':2,'reservedMemoryMiB':required}),flush=True)
# Keep QA listeners outside the Windows dynamic client-port range; verify TCP and Sable UDP.
for attempt in range(100):
 port=30000+uuid.uuid4().int%15000
 try:
  with socket.socket() as probe,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
   probe.bind(('127.0.0.1',port));udp.bind(('127.0.0.1',port))
  break
 except OSError:continue
else:raise RuntimeError('No verified loopback QA TCP/UDP port available')
assert args.lab_prefix and all(c.isalnum() or c=='-' for c in args.lab_prefix),'Invalid owned lab prefix'
root=PROJECT/'build/local-mc-debug'/(args.lab_prefix+'-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
project,root=rt.new_lab(PROJECT,root,[old,S,G,J]);coordinator=root/'coordinator';coordinator.mkdir();zco=root/'zombie-coordinator';zco.mkdir()
# Compile canonical QA sources directly; no copy or source edits in the shared tool.
metadata=rt.client_metadata(G,'BatterMC5Remake');libraries=rt.client_libraries(G,'BatterMC5Remake',metadata)
agent=rt.compile_agent(TOOL,root,J,rt.javac_classpath(S,G,'21.1.250',libraries))
marker={'schema':1,'runId':uuid.uuid4().hex,'projectRoot':str(PROJECT),'instanceRoot':str(root),'createdAt':time.time(),'roles':['server','host','guest'],'clientNames':{'host':'ZombieHostQA','guest':'ZombieGuestQA'},'hidden':True,'serverPort':port,'productionMutation':False,'offlineLoopback':True,'processes':{},'resources':capacity,'mode':'hold','toolSourceCommit':subprocess.check_output(['git','-c','safe.directory='+str(TOOL),'-C',str(TOOL),'rev-parse','HEAD'],text=True).strip(),'qaAgentSha256':rt.sha256(agent),'fullPackAdapter':True,'physicalOSInput':False,'SSOAcceptance':False}
rt.write_json(root/'local-mc-owner.json',marker)
labs={}
for role in marker['roles']:
 lab=root/role;lab.mkdir();labs[role]=lab
 for name in ['mods','config','tacz','defaultconfigs','kubejs','resourcepacks','CustomSkinLoader','dynamic-data-pack-cache']:
  source=old/role/name
  if source.is_dir():shutil.copytree(source,lab/name)
 rows=LOCK['sides']['server' if role=='server' else 'client']
 for stale in (lab/'mods').glob('*.jar'):
  if 'QA-ONLY' not in stale.name:
   assert stale.resolve().is_relative_to(root.resolve());stale.unlink()
 for row in rows:
  source=Path(row['path']);assert rt.sha256(source)==row['sha256'],'Five-module input drift: '+row['name']
  shutil.copy2(source,lab/'mods'/row['name'])
 marker.setdefault('fiveModuleProducts',{})[role]={'count':len(rows),'lockSha256':rt.sha256(OUT/'FIVE-MODULE-PRODUCT-LOCK.json'),'fiveModuleNativeAcceptancePending':True}
 for name,version in [('muxi-zombie-challenge',json.loads((W.parent.parent/'mod.json').read_text(encoding='utf-8'))['version']),('muxi-minigames','0.2.1-equipment.1'),('muxi-outbreak','0.4.0-equipment.1'),('muxi-terminal','0.2.4-task14-social-qa.1')]:
  for stale in (lab/'mods').glob(name+'-*.jar'):
   assert stale.resolve().is_relative_to(root.resolve());stale.unlink()
  moduleSource=Path(r'C:\Users\ranzh\workspace\dev\muxigame')/name/'build/libs'/(name+'-'+version+'.jar')
  if args.qa_fixture=='grenade-pair':
   frozen=Path(r'C:\Users\ranzh\workspace\dev\muxigame\better-mc-remake\build\local-mc-debug\zqa-20261003-122943\server\mods')
   moduleSource=frozen/(name+'-'+version+'.jar') if name!='muxi-outbreak' else Path(r'C:\Users\ranzh\workspace\dev\muxigame\muxi-outbreak\build\entity-admission-repair\muxi-outbreak-0.4.0-equipment.1.jar')
   expected={'muxi-zombie-challenge':'51c3cb7714a054f53ed8e4cdbdebf3c48dcb6a585d1c566ac634caade17445de','muxi-minigames':'75aead1ee9f55fafeac46cb394c2e3b759737befc8cee1a6efb7852e114342b7','muxi-outbreak':'ccb99f2a5d42b4c965b54da18fcce7ca6d9acc963b4f7aebe1dd2b21e174869e'}[name]
   if name=='muxi-zombie-challenge' and (W/'grenade-pair-product-repair.json').is_file():
    repair=rt.read_json(W/'grenade-pair-product-repair.json');moduleSource=Path(repair['artifact']);expected=repair['sha256']
   if name=='muxi-outbreak' and (W/'grenade-pair-outbreak-repair.json').is_file():
    repair=rt.read_json(W/'grenade-pair-outbreak-repair.json');moduleSource=Path(repair['artifact']);expected=repair['sha256']
   assert rt.sha256(moduleSource)==expected,'Frozen module hash mismatch: '+name
  if args.qa_fixture=='ai':
   frozen=rt.read_json(args.product_inputs)[name];moduleSource=Path(frozen['artifact']);assert rt.sha256(moduleSource)==frozen['sha256'],'AI frozen artifact drift: '+name
  shutil.copy2(moduleSource,lab/'mods'/moduleSource.name)
 core=rt.read_json(W/'zombie-core-input-snapshot.json');coreJar=args.core_jar or Path(core['artifact']);expectedCore=args.core_sha256 or core['sha256'];assert rt.sha256(coreJar)==expectedCore,'Core input hash mismatch'
 for stale in (lab/'mods').glob('muxi-game-core-*.jar'):
  assert stale.resolve().is_relative_to(root.resolve());stale.unlink()
 shutil.copy2(coreJar,lab/'mods'/coreJar.name)
 ownqa=lab/'mods'/('zombie-full-network-server-QA-ONLY.jar' if role=='server' else 'zombie-full-network-client-QA-ONLY.jar')
 assert ownqa.is_file()
 if args.suite=='zombie' and args.qa_fixture in ('corrected','equipment-path','grenade-pair','ai'):
  fixture='zombie-ammo-inventory-'+('server' if role=='server' else 'client')+'-QA-ONLY.jar' if args.qa_fixture=='ai' else 'zombie-grenade-pair-server-QA-ONLY.jar' if args.qa_fixture=='grenade-pair' and role=='server' else 'zombie-equipment-path-server-QA-ONLY.jar' if args.qa_fixture=='equipment-path' and role=='server' else 'zombie-fixture-corrected-'+('server' if role=='server' else 'client')+'-QA-ONLY.jar'
  if args.qa_fixture=='equipment-path' and role=='server':
   receipt=rt.read_json(W/'equipment-path-build-receipt.json');assert rt.sha256(W/fixture)==receipt['artifactSha256']
 else:
  fixture=('joint-server-existing-profile-QA-ONLY.jar' if args.suite=='joint' else 'zombie-server-existing-profile-QA-ONLY.jar') if role=='server' else 'joint-client-QA-ONLY.jar'
 shutil.copy2(W/fixture,ownqa)
 shutil.copy2(agent,lab/'mods'/agent.name)
 if role=='server':
  sourceWorld=old/'server/qa-world' if (old/'server/qa-world').is_dir() else old/'server/private-network-world';shutil.copytree(sourceWorld,lab/'qa-world')
  props='\n'.join(line for line in (old/'server/server.properties').read_text(encoding='utf-8').splitlines() if not line.startswith(('level-name=','server-port=','server-ip=')))+'\nlevel-name=qa-world\nserver-ip=127.0.0.1\nserver-port='+str(port)+'\n'
  if args.qa_fixture=='ai':
   props='\n'.join([line for line in props.splitlines() if not line.startswith('spawn-animals=')])+ '\nspawn-animals=true\n'
  (lab/'server.properties').write_text(props,encoding='utf-8');(lab/'eula.txt').write_text('eula=true\n')
 else:
  shutil.copytree(old/role/'natives',lab/'natives');shutil.copy2(old/role/'options.txt',lab/'options.txt')
  shutil.copytree(Path(r'C:\Users\ranzh\Documents\Codex\2026-10-02\task-9\full-pack-prep\instance-baseline\shaderpacks'),lab/'shaderpacks')
 prefix=['-Xlog:gc*,safepoint:file=qa-gc.log:time,uptime,level,tags']+(["-Dqa.zombie.ai=true","-Dqa.zombie.ammo=true"] if args.qa_fixture=='ai' and role=='server' else [])+(["-Dqa.grenade.outbreakOnly=true"] if args.outbreak_only and role=='server' else [])+(["-Dqa.grenade.pair=true"] if args.qa_fixture=='grenade-pair' and role=='server' else [])+[f'-Dqa.local.root={root}',f'-Dqa.local.runId={marker["runId"]}',f'-Dqa.local.role={role}',f'-Dqa.zombie.coordinator={zco}',f'-Dqa.zombie.role={role}',f'-Dqa.zombie.port={port}']
 if role=='server':launch=[*prefix,*rt.server_arguments(S,'21.1.250',6144,Path(os.environ['TEMP']))]
 else:launch=[*prefix,f'-Dqa.local.port={port}','-Dqa.local.hidden=true','-Dhttp.proxyHost=127.0.0.1','-Dhttp.proxyPort=9','-Dhttps.proxyHost=127.0.0.1','-Dhttps.proxyPort=9',*rt.client_arguments(G,'BatterMC5Remake',metadata,libraries,lab,marker['clientNames'][role],12288,lab/'natives',Path(os.environ['TEMP']))]
 launch=[value.replace('ActiveProcessorCount=3','ActiveProcessorCount=3' if role=='server' else 'ActiveProcessorCount=2') for value in launch]
 rt.argfile(lab/'launch.args',launch)
marker['coreCandidate']={'artifact':str(coreJar),'sha256':expectedCore,'sourceCommit':'71d3d3e6abbe00a29b1f01113fde9b04992e261c' if expectedCore=='c05a6bdb6cb97befb6e1d76e9120c7d1d6916028a0c9d759d21814ff728febbd' else 'see explicit input'}
marker['uiConfiguration']={role:{str(p.relative_to(lab/'config')):{'sha256':rt.sha256(p),'uiSettingLines':[line.strip() for line in p.read_text(encoding='utf-8',errors='replace').splitlines() if 'enable mod ui' in line]} for p in (lab/'config').rglob('*.toml') if 'paster' in str(p).lower()} for role,lab in labs.items() if role!='server'}
props=(labs['server']/'server.properties').read_text(encoding='utf-8');watchdog=int(next((line.split('=',1)[1] for line in props.splitlines() if line.startswith('max-tick-time=')),'60000'));assert watchdog>0,'Native watchdog must stay enabled';marker['watchdogMs']=watchdog
if args.qa_fixture=='ai':
 for role,lab in labs.items():
  assert not any('fastsuite' in p.name.lower() for p in (lab/'mods').glob('*.jar')),'FastSuite is excluded from standard AI QA'
  assert (lab/'config/yes_steve_model/builtin/wine_fox/01_taisho_maid/ysm.json').is_file(),'Actual Winefox metadata missing: '+role
marker['inputArtifacts']={role:{p.name:rt.sha256(p) for p in (lab/'mods').glob('*.jar')} for role,lab in labs.items()};rt.write_json(root/'local-mc-owner.json',marker)
rt.write_json(OUT/(args.suite+'-fullpack-local-debug-prepared.json'),{'root':str(root),'port':port,'toolCommit':marker['toolSourceCommit'],'copiedPrivateWorldOnly':True,'preexistingTestProfiles':True,'firstBirthAcceptance':False})
for role,lab in labs.items():
 expected={r['name']:r['sha256'] for r in LOCK['sides']['server' if role=='server' else 'client']}
 actual={p.name:rt.sha256(p) for p in (lab/'mods').glob('*.jar') if 'QA-ONLY' not in p.name}
 assert actual==expected,'Installed product set differs from frozen five-module lock: '+role
 if role!='server':assert '-Xmx12288M' in (lab/'launch.args').read_text(),'Default12GiB not honored'
rt.write_json(root/'installed-products-verified.json',{'roles':{role:len(LOCK['sides']['server' if role=='server' else 'client']) for role in labs},'clientHeapMiB':12288,'qaArtifactsUnchanged':True})
processes={};logs={};stopIssued=False;resourceTimeline=[]
stopMonitor=threading.Event()
def monitorResources():
 while not stopMonitor.wait(5):
  row={'time':time.time(),'cpuPercent':psutil.cpu_percent(interval=None),'availableMemoryMiB':psutil.virtual_memory().available//1048576,'ownedProcesses':[]}
  for role,proc in list(processes.items()):
   item={'role':role,'pid':proc.pid,'running':proc.poll() is None}
   try:
    native=psutil.Process(proc.pid);item.update(cpuSeconds=sum(native.cpu_times()[:2]),rssMiB=native.memory_info().rss//1048576)
   except (psutil.NoSuchProcess,psutil.AccessDenied):pass
   row['ownedProcesses'].append(item)
  resourceTimeline.append(row)
  try:rt.write_json(root/'resource-timeline.json',resourceTimeline)
  except OSError:pass
threading.Thread(target=monitorResources,name='owned-qa-resource-observer',daemon=True).start()
result={'runId':marker['runId'],'instanceRoot':str(root),'passed':False,'physicalOSInput':False,'visualAcceptance':False,'SSOAcceptance':False,'productionMutation':False,'nativeFCallback':True,'normalStopOnly':True,'qaFixture':args.qa_fixture if args.suite=='zombie' else 'historical-joint'}
env={k:v for k,v in os.environ.items() if not k.startswith('MUXI_')}
env['RAYON_NUM_THREADS']='2'
result['nativeWorkerResourceHint']={'RAYON_NUM_THREADS':2,'JavaServerProcessors':3,'JavaClientProcessors':2,'physicsLogicUnchanged':True}
def start(role):
 lab=labs[role];logs[role]=(lab/'boot.log').open('w',encoding='utf-8');p=subprocess.Popen([str(J/'bin/java.exe'),'@'+str(lab/'launch.args')],cwd=lab,stdin=subprocess.PIPE if role=='server' else subprocess.DEVNULL,stdout=logs[role],stderr=subprocess.STDOUT,text=True,encoding='utf-8',env=env,creationflags=subprocess.CREATE_NO_WINDOW);processes[role]=p
 marker['processes'][role]={'pid':p.pid,'root':str(lab),'startedAt':time.time()};rt.write_json(root/'local-mc-owner.json',marker);print(json.dumps({'started':role,'pid':p.pid,'root':str(lab)}),flush=True)
try:
 start('server')
 common.wait_condition('full pack server Done and canonical native tick',lambda: 'Done (' in (labs['server']/'boot.log').read_text(encoding='utf-8',errors='replace') and bool(rt.read_json(coordinator/'status-server.json')),900,processes)
 for role in ['host','guest']:start(role)
 deadline=time.monotonic()+1500;last=0
 while processes['server'].poll() is None:
  if any(list((lab/'crash-reports').glob('crash-*.txt')) or list(lab.glob('hs_err_pid*.log')) for lab in labs.values()):raise RuntimeError('Native crash report observed; stop this one attempt and preserve diagnostics')
  if time.monotonic()>deadline:raise TimeoutError('Full pack native acceptance deadline')
  shutdownRequest=rt.read_json(labs['server']/('outbreak-network-shutdown-request.json' if args.outbreak_only else 'zombie-network-shutdown-request.json'))
  if shutdownRequest and not stopIssued:
   processes['server'].stdin.write('stop\n');processes['server'].stdin.flush();stopIssued=True
  acceptance=rt.read_json(labs['server']/'zombie-network-result.json')
  if acceptance and all(processes[r].poll() is not None for r in ['host','guest']) and (rt.read_json(coordinator/'status-server.json') or {}).get('players')==0:
   processes['server'].stdin.write('stop\n');processes['server'].stdin.flush();break
  for clientRole in ['host','guest']:
   state=rt.read_json(coordinator/('status-'+clientRole+'.json')) or {}
   if state.get('screen')=='ModMismatchDisconnectedScreen':raise RuntimeError('Client '+clientRole+' rejected during mod-channel handshake; normal stop and preserve client boot.log')
  if time.monotonic()-last>20:print(json.dumps({'progress':rt.read_json(labs['server']/'zombie-network-progress.json'),'nativeServerPlayers':(rt.read_json(coordinator/'status-server.json') or {}).get('players')}),flush=True);last=time.monotonic()
  if any(p.poll() is not None for r,p in processes.items() if r!='server') and not (labs['server']/'zombie-network-result.json').exists():raise RuntimeError('Own full pack client exited before native result: '+str({r:p.poll() for r,p in processes.items()}))
  time.sleep(.5)
 result['acceptance']=rt.read_json(labs['server']/'zombie-network-result.json');result['passed']=bool(result['acceptance'] and result['acceptance'].get('passed'))
 receipts=rt.read_json(labs['server']/'zombie-network-restore-receipts.json') or [];result['exactRestoreReceipts']=len(receipts);result['passed']&=len(receipts)==2 and all(r['passed'] for r in receipts)
 if stopIssued:
  result['activeShutdown']=rt.read_json(labs['server']/('outbreak-network-active-shutdown.json' if args.outbreak_only else 'zombie-network-active-shutdown.json'));receipts=rt.read_json(labs['server']/('outbreak-network-restore-receipts.json' if args.outbreak_only else 'zombie-network-restore-receipts.json')) or [];result['exactRestoreReceipts']=len(receipts);result['passed']&=bool(result['activeShutdown'] and result['activeShutdown'].get('actualConnectedPlayers')==2 and result['activeShutdown'].get('bothRestoredBeforeOtherModsTeardown') and len(receipts)==5 and all(r['passed'] for r in receipts))
except Exception as failure:result['error']=str(failure);print('JOINT_FULLPACK_FAILED '+str(failure),flush=True)
finally:
 common.stop_files(root,marker)
 p=processes.get('server')
 if p and p.poll() is None:
  try:p.stdin.write('stop\n');p.stdin.flush()
  except OSError:pass
 deadline=time.monotonic()+240
 while any(p.poll() is None for p in processes.values()) and time.monotonic()<deadline:time.sleep(.2)
 result['exitCodes']={r:p.poll() for r,p in processes.items()};result['normalExit']=len(processes)==3 and all(c==0 for c in result['exitCodes'].values());result['normalStopBlocked']=[{'role':r,'pid':p.pid} for r,p in processes.items() if p.poll() is None];result['passed']&=result['normalExit']
 result['nativeCrashReports']=[str(p.relative_to(root)) for lab in labs.values() for p in list((lab/'crash-reports').glob('crash-*.txt'))+list(lab.glob('hs_err_pid*.log'))];result['passed']&=not result['nativeCrashReports']
 result['coreCandidate']=marker['coreCandidate'];result['uiConfiguration']=marker['uiConfiguration'];result['watchdogMs']=marker['watchdogMs'];result['fixtureState']=rt.read_json(labs['server']/'zombie-network-fixture-state.json');result['aiBoundary']=rt.read_json(labs['server']/'zombie-ai-boundary-observations.json');result['throwAudit']=rt.read_json(labs['server']/'zombie-network-throw-audit.json');result['exactRestoreReceiptsAfterClose']=rt.read_json(labs['server']/'zombie-network-restore-receipts.json')
 result['clientRenderers']={r:(rt.read_json(coordinator/f'status-{r}.json') or {}).get('glRenderer') for r in ['host','guest']}
 for log in logs.values():log.close()
 stopMonitor.set();rt.write_json(root/'run-result.json',result);rt.write_json(OUT/(args.suite+'-fullpack-local-debug-result.json'),result);print('FULLPACK_LOCAL_DEBUG_RESULT '+json.dumps(result),flush=True)
if not result['passed']:raise SystemExit(1)
