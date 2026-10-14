"""Latest local six YSM models, real client renderer and existing terminal APP. No production writes."""
from pathlib import Path
import datetime,hashlib,json,os,shutil,socket,subprocess,sys,threading,time,zipfile
ROOT=Path(__file__).resolve().parents[3];OWN=ROOT/'muxi-horse-racing';SHARED=ROOT/'better-mc-remake';TEST=Path(__file__).parent
sys.path[:0]=[str(ROOT/'.ops-venv/Lib/site-packages'),str(SHARED/'scripts')]
from trade_fixture import TradeFixture
import local_mc_debug as debug
import psutil
JDK=Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-<PRIVATE_HOST>+1');SERVER=ROOT/'bmc5server';GAME=ROOT/'_client_test/game'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S');prep=OWN/'build/qa'/('shop-input-'+stamp);prep.mkdir(parents=True);lab=SHARED/'build/local-mc-debug'/('horse-shop-client-'+stamp)
 release=ROOT/'muxi-ysm-tools/build/release-six-approved-20261007';handoff=json.loads((release/'display-metadata-update-handoff.json').read_text(encoding='utf-8'));archive=Path(handoff['runtimeArchive']['path']);assert sha(archive)==handoff['runtimeArchive']['sha256']
 modelroot=prep/'models/10_umamusume';modelroot.mkdir(parents=True)
 with zipfile.ZipFile(archive) as z:
  for name in z.namelist():
   parts=Path(name).parts
   if parts[0]!='赛马娘' or '..' in parts:raise ValueError(name)
   target=modelroot.joinpath(*parts[1:])
   if name.endswith('/'):target.mkdir(parents=True,exist_ok=True)
   else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
 for model in handoff['models']:
  for name,h in model['sha256'].items():assert sha(modelroot/model['leaf']/name)==h
 catalog=json.loads((OWN/'src/main/resources/umamusume_catalog_v1.json').read_text(encoding='utf-8'));assert (SHARED/'server/app/umamusume_catalog_v1.json').read_bytes()==(OWN/'src/main/resources/umamusume_catalog_v1.json').read_bytes()
 for b in catalog['catalog']:
  model=next(m for m in handoff['models'] if m['runtimeModelId']==b['modelId']);assert b['name']==model['displayName']
 source=OWN/'build/libs'/json.loads((OWN/'build/release.json').read_text())['artifact'];qa=prep/source.name;shutil.copy2(source,qa)
 framework=ROOT/'muxi-minigames/build/libs/muxi-minigames-0.2.1-equipment.1.jar';assert sha(framework)=='75aead1ee9f55fafeac46cb394c2e3b759737befc8cee1a6efb7852e114342b7'
 comp=SHARED/'build/local-mc-debug/dim7-material/server/mods/muxi-champion-companions-1.0.5.jar';assert sha(comp)=='8a60586141326e6d61450ccd0e291a1f75414bab4a8b3eb45f11dd9eacce7d3e'
 mods=[qa,framework,comp,SHARED/'build/local-mc-debug/dim7-material/server/mods/muxi-game-core-1.12.3-task14-qa.1.jar',ROOT/'muxi-terminal/build/libs/muxi-terminal-0.2.4-task14-social-qa.1.jar']
 for pattern in ['*champions-neoforge-<PRIVATE_HOST>*','*touhoulittlemaid-1.5.3*','architectury-*.jar','kubejs-*.jar','rhino-*.jar','*waystones*21.1.42*','*balm*jar']:
  candidates=list((SERVER/'mods').glob(pattern));assert len(candidates)==1,(pattern,candidates);mods.append(candidates[0])
 mods.append(next((GAME/'mods').glob('*ysm-2.6.5*')));mcef=next((SERVER/'mods').glob('*mcef*jar'))
 mapped=[GAME/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-client.jar',GAME/'libraries/net/minecraft/client/1.21.1-20240808.144430/client-1.21.1-20240808.144430-srg.jar'];cp=os.pathsep.join(map(str,[*mapped,*mods,mcef,*[p for p in (SERVER/'libraries').rglob('*.jar') if '/net/minecraft/' not in p.as_posix()],*list((GAME/'libraries/org/lwjgl').rglob('*.jar'))]));classes=prep/'classes';classes.mkdir()
 args=['--release','21','-encoding','UTF-8','-proc:none','-cp',cp,'-d',str(classes),str(TEST/'UmamusumeShopServerQA.java'),str(TEST/'UmamusumeShopClientQA.java'),str(TEST/'mixin/ShopBrowserProbeMixin.java'),str(TEST/'mixin/ShopEntityRemovalProbeMixin.java'),str(TEST/'mixin/ShopCefProfileMixin.java')];argfile=prep/'compile.args';argfile.write_text('\n'.join('"'+v.replace('\\','/')+'"' for v in args),encoding='utf-8');subprocess.run([str(JDK/'bin/javac.exe'),'-J-XX:ActiveProcessorCount=2','@'+str(argfile)],check=True)
 with zipfile.ZipFile(qa) as z:entries={n:z.read(n) for n in z.namelist()}
 for p in classes.rglob('*.class'):entries[p.relative_to(classes).as_posix()]=p.read_bytes()
 entries['horse_shop_client_qa.mixins.json']=json.dumps(dict(required=True,minVersion='0.8',package='net.muxigame.minigames.horseqa.mixin',compatibilityLevel='JAVA_21',client=['ShopBrowserProbeMixin','ShopCefProfileMixin'],mixins=['ShopEntityRemovalProbeMixin'],injectors=dict(defaultRequire=1))).encode()
 entries['META-INF/neoforge.mods.toml']+=b'\n[[mixins]]\nconfig="horse_shop_client_qa.mixins.json"\n'
 with zipfile.ZipFile(qa,'w',zipfile.ZIP_DEFLATED) as z:
  for n,data in entries.items():z.writestr(n,data)
 if '--prepare-only' in sys.argv:
  print(json.dumps(dict(preparedOnly=True,mcStarted=False,qaJar=str(qa),normalJar=str(source),normalModSha256=sha(source),runtimeArchiveSha256=sha(archive)),ensure_ascii=False),flush=True);return 0
 os.environ['BMC_UMAMUSUME_REGISTRATION_VERIFIED']='1';fixture=TradeFixture(SHARED/'server/app',SHARED/'server/app',prep);os.environ.update(MUXI_GAME_PLATFORM_URL=fixture.url,MUXI_GAME_PLATFORM_KEY=fixture.key)
 original_copy=debug.rt.copy_inputs
 cache=SHARED/'build/local-mc-debug/music-next-20261004-142451/host/mcef-libraries'
 from fastapi.responses import Response
 @fixture.app.get('/java-cef-builds/{commit}/{filename}')
 def existing_checksum(commit:str,filename:str):
  if filename!='windows_amd64.tar.gz.sha256':return Response(status_code=404)
  return Response((cache/filename).read_bytes(),media_type='text/plain')
 mirror=fixture.url.split('/api/internal/')[0]
 def isolated_copy(home,jars,data):
  original_copy(home,jars,data);shutil.copytree(prep/'models',home/'config/yes_steve_model/custom');shutil.copytree(cache,home/'mcef-libraries');shutil.copytree(cache,home/'mods/mcef-libraries');(home/'config/mcef').mkdir();(home/'config/mcef/mcef.properties').write_text('skip-download=true\nuse-cache=false\nuser-agent=MuxiHorseLocalQA\ndownload-mirror='+mirror+'\n')
  if home.name=='server':
   (lab/'shop-client.js').write_text((TEST/'shop-client.js').read_text(encoding='utf-8-sig'),encoding='utf-8');(lab/'input-evidence.json').write_text(json.dumps(dict(productionWrites=False,latestChineseCandidateSha256=sha(archive),registeredRuntimePrefix='10_umamusume',normalModSha256=sha(source),qaCopySha256=sha(qa),sourceJar=str(source)),indent=2))
 debug.rt.copy_inputs=isolated_copy
 original_argfile=debug.rt.argfile
 def isolated_argfile(path,values):
  original_argfile(path,(['-XX:ErrorFile='+str(lab/'host/hs_err_pid%p.log')]+list(values)) if path.resolve()==(lab/'host/launch.args').resolve() else values)
  if path.resolve()==(lab/'server/launch.args').resolve():
   assert (lab/'local-mc-owner.json').is_file()
   props=lab/'server/server.properties';text=props.read_text();assert text.count('spawn-animals=false')==1
   props.write_text(text.replace('spawn-animals=false','spawn-animals=true'))
   evidence=debug.rt.read_json(lab/'input-evidence.json');evidence['isolatedAnimalEntitiesEnabled']=True;debug.rt.write_json(lab/'input-evidence.json',evidence)
 debug.rt.argfile=isolated_argfile
 sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
 argv=['run','--project-root',str(SHARED),'--instance-root',str(lab),'--server-runtime',str(SERVER),'--client-game',str(GAME),'--java-home',str(JDK),'--port',str(port),'--clients','1','--client-name','10000','--accept-eula','--mode','hold','--hold-seconds','420','--server-memory-mb','3072','--client-memory-mb','3072','--approved-heap-budget-mb','6144','--boot-timeout','240','--shutdown-timeout','120','--client-max-fps','30','--client-simulation-distance','5','--client-mod',str(mcef)]
 for m in mods:argv+=['--mod',str(m)]
 for prop in ['muxi.horse.shopClientQa=true','muxi.horse.trading.enabled=true','muxi.horse.maid.registrationVerified=true']:argv+=['--jvm-property',prop]
 def monitor():
  deadline=time.monotonic()+780;observed={}
  while time.monotonic()<deadline:
   for helper in owned_helpers(lab):
    observed[helper['pid']]=helper
   if observed:debug.rt.write_json(lab/'cef-profile-observed.json',dict(helpers=list(observed.values()),allPrivateProfiles=all(h['privateProfile'] for h in observed.values())))
   client_now=debug.rt.read_json(lab/'shop-client-result.json') or {};server_now=debug.rt.read_json(lab/'shop-server-evidence.json') or {};native_ready=len(server_now.get('purchased',[]))==1 and server_now['purchased'][0].get('unlocked') and server_now.get('customHorseNamePreserved')
   if (client_now and (not client_now.get('success') or native_ready)) or server_now.get('originalRemoved') or (lab/'shop-server-error.txt').exists():
    time.sleep(2);marker=debug.rt.read_json(lab/'local-mc-owner.json')
    if marker:debug.rt.write_json(lab/'stop-request.json',dict(runId=marker['runId'],normalStopOnly=True))
    return
   time.sleep(.5)
 threading.Thread(target=monitor,daemon=True).start();print(json.dumps(dict(lab=str(lab),prep=str(prep)),ensure_ascii=False),flush=True)
 try:code=debug.main(argv)
 finally:backend=fixture.close();os.environ.pop('MUXI_GAME_PLATFORM_KEY',None);os.environ.pop('MUXI_GAME_PLATFORM_URL',None);cleanup_helpers(lab)
 return summarize(lab,backend,sha(source),sha(archive))

def owned_helpers(lab):
 rows=[];profile=(lab/'host/cef-profile').resolve()
 for proc in psutil.process_iter(['pid','name','exe','cmdline','ppid']):
  try:
   if proc.info['name']!='jcef_helper.exe' or not proc.info['exe'] or not Path(proc.info['exe']).resolve().is_relative_to((lab/'host').resolve()):continue
   dirs=[v.split('=',1)[1].strip('"') for v in proc.info['cmdline'] if v.startswith('--user-data-dir=')]
   rows.append(dict(pid=proc.pid,parentPid=proc.info['ppid'],executable=proc.info['exe'],userDataDirs=dirs,privateProfile=len(dirs)==1 and Path(dirs[0]).resolve().is_relative_to(profile)))
  except (psutil.NoSuchProcess,psutil.AccessDenied):continue
 return rows

def cleanup_helpers(lab):
 marker=debug.rt.read_json(lab/'local-mc-owner.json') or {};run=debug.rt.read_json(lab/'run-result.json') or {};host=marker.get('processes',{}).get('host',{});rows=[]
 if run.get('exitCodes',{}).get('host') is None:return
 for row in owned_helpers(lab):
  if row['parentPid']!=host.get('pid'):raise RuntimeError('Private CEF parent mismatch')
  try:
   proc=psutil.Process(row['pid']);assert Path(proc.exe()).resolve()==Path(row['executable']).resolve();proc.terminate();proc.wait(timeout=10);rows.append(row)
  except psutil.NoSuchProcess:pass
 debug.rt.write_json(lab/'cef-helper-cleanup.json',dict(javaExitCodes=run.get('exitCodes'),terminatedOwnOrphans=rows,remainingOwnHelpers=owned_helpers(lab)))

def summarize(lab,backend,mod_sha,archive_sha):
 client=debug.rt.read_json(lab/'shop-client-result.json') or {};previews=debug.rt.read_json(lab/'preview-evidence.json') or {};server=debug.rt.read_json(lab/'shop-server-evidence.json') or {};run=debug.rt.read_json(lab/'run-result.json') or {};profile=next(p for p in backend['tables']['player_profiles'] if p['uid']==10000)
 captures=[v for v in previews.values() if isinstance(v,dict) and 'pngSha256' in v];preview_valid=len(captures)==6 and all(v.get('resolvedRegistryModelMatches') for v in captures) and len({v['pngSha256'] for v in captures})==6
 cef=debug.rt.read_json(lab/'cef-profile-observed.json') or {};cef_private=bool(cef.get('helpers')) and all(len(h['userDataDirs'])==1 and Path(h['userDataDirs'][0]).resolve().is_relative_to((lab/'host/cef-profile').resolve()) for h in cef['helpers'])
 visible=client.get('dom',{}).get('visiblePreviews',[]);six_visible=len(visible)==6 and all(v.get('visible') for v in visible)
 success=six_visible and cef_private and preview_valid and client.get('success') and run.get('normalExit') and len(server.get('purchased',[]))==1 and server['purchased'][0]['unlocked'] and profile['points']==9880 and profile['balance_cents']==321 and server.get('customHorseNamePreserved')
 result=dict(success=bool(success),lab=str(lab),normalModSha256=mod_sha,runtimeArchiveSha256=archive_sha,actualClientRenderer=bool(previews.get("completed")),resolvedSixModels=preview_valid,actualMcefApp=client.get('actualMcefApp',False),normalExit=run.get('normalExit'),exitCodes=run.get('exitCodes'),privateBalance=profile['points'],privateCashCents=profile['balance_cents'],privateCefProfile=bool(cef_private),sixPreviewCardsVisible=six_visible,purchased=server.get('purchased'),productionWrites=False,clientError=client.get('error'),serverError=(lab/'shop-server-error.txt').read_text() if (lab/'shop-server-error.txt').exists() else None)
 debug.rt.write_json(lab/'shop-summary.json',result);debug.rt.write_json(OWN/'build/qa/shop-client-latest.json',result);print(json.dumps(result,ensure_ascii=False),flush=True);return 0 if success else 1
if __name__=='__main__':
 if '--collect-only' in sys.argv:
  lab=Path(sys.argv[sys.argv.index('--collect-only')+1]).resolve();assert lab.parent==(SHARED/'build/local-mc-debug').resolve() and lab.name.startswith('horse-shop-client-')
  assert debug.rt.read_json(lab/'run-result.json').get('normalExit')
  inputs=debug.rt.read_json(lab/'input-evidence.json');backend=debug.rt.read_json(OWN/'build/qa'/lab.name.replace('horse-shop-client-','shop-input-')/'trade-fixture.json')
  raise SystemExit(summarize(lab,backend,inputs['normalModSha256'],inputs['latestChineseCandidateSha256']))
 raise SystemExit(main())
