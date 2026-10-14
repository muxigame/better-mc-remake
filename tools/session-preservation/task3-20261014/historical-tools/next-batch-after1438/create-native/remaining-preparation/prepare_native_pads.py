"""Run only after next explicit window; does not launch any Minecraft."""
import argparse,json,os,time,hashlib,psutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--instance-root',type=Path,required=True);p.add_argument('--expected-run-id',required=True);p.add_argument('--window-authorized',action='store_true');a=p.parse_args()
if not a.window_authorized:raise SystemExit('next exclusive window required; no action performed')
root=a.instance_root.resolve();owner=json.loads((root/'local-mc-owner.json').read_text());assert owner['runId']==a.expected_run_id and owner['offlineLoopback'] and not owner['productionMutation'];assert not (root/'stop-request.json').exists() and not (root/'run-result.json').exists()
owned={v['pid'] for v in owner['processes'].values()};other=[p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower() in ['java.exe','javaw.exe'] and p.pid not in owned];assert not other,('other JVM occupies window',other)
for v in owner['processes'].values():assert abs(psutil.Process(v['pid']).create_time()-v['processCreateTime'])<.01
c=root/'coordinator';records=[]
def command(text):
 f=c/'command-server.json';old=json.loads(f.read_text()) if f.exists() else {};i=old.get('id',0)+1;tmp=f.with_suffix('.pending');tmp.write_text(json.dumps({'id':i,'runId':owner['runId'],'type':'execute','command':text}));end=time.monotonic()+3
 while True:
  try:os.replace(tmp,f);break
  except PermissionError:
   if time.monotonic()>end:raise
   time.sleep(.05)
 response=c/('result-server-'+str(i)+'.json');end=time.monotonic()+30
 while not response.exists():
  if time.monotonic()>end:raise TimeoutError(text)
  time.sleep(.1)
 row=json.loads(response.read_text());assert row['ok'];records.append({'command':text,'receipt':i,'warning':'execute receipt alone does not prove vanilla command success; inspect server/native observer'})
D='execute in muxi_game_core:overworld run '
for text in ['forceload add 0 0 95 31','fill 0 119 0 95 119 31 minecraft:stone']:command(D+text)
command('execute in muxi_game_core:overworld run tp 10000 4.5 120 4.5 0 0')
Path(__file__).with_name('pad-preparation-result.json').write_text(json.dumps({'runId':owner['runId'],'assistedOwnedWorldFixture':True,'commands':records,'productionWrites':False},indent=2));print('supported pads placed; check native health/floor before every case')
