from pathlib import Path
import subprocess,json
N=Path(__file__).resolve().parent
source=r'''import pathlib,subprocess,json,time,hashlib,urllib.request
root=pathlib.Path('/opt/muxi-auth/.releases/music-release-0.4.0-20261013');base=json.loads((root/'baseline.json').read_text());built=json.loads((root/'built.json').read_text());compose=pathlib.Path('/opt/muxi-auth/compose.prod.yaml')
def run(a):return subprocess.check_output(a,text=True,cwd='/opt/muxi-auth')
assert hashlib.sha256(compose.read_bytes()).hexdigest()=='08ee503f55a22a4929945d9d010daf9ba8448f75c55b1a7e138846972cef92ee','Compose drift'
current=json.loads(run(['docker','inspect','muxi-auth-muxi-auth-1']))[0];assert current['Image']==built['imageId'],'Running image drift'
assert json.loads(run(['docker','image','inspect',base['imageTag']]))[0]['Id']==base['imageId'],'Rollback image drift'
assert json.loads(run(['docker','image','inspect',built['tag']]))[0]['Id']==built['imageId'],'Candidate image drift'
override=root/'enabled.compose.yaml';assert not override.exists(),'Already attempted; inspect'
override.write_text('services:\n  muxi-auth:\n    image: '+built['tag']+'\n    environment:\n      MUSIC_OSS_ENABLE: "1"\n      MUSIC_OSS_DEPLOYMENT_APPROVED: "PRIVATE_SIGNED_V1"\n      MUSIC_FFMPEG_EXECUTABLE: "/usr/bin/ffmpeg"\n')
cmd=['docker','compose','-f',str(compose),'-f',str(override)]
before=json.loads(run(['docker','compose','-f',str(compose),'config','--format','json']))['services']['muxi-auth']
after=json.loads(run(cmd+['config','--format','json']))['services']['muxi-auth']
assert {k:v for k,v in before.items() if k not in ['image','environment']}=={k:v for k,v in after.items() if k not in ['image','environment']}
assert {k:v for k,v in before['environment'].items() if k not in ['MUSIC_OSS_ENABLE','MUSIC_OSS_DEPLOYMENT_APPROVED','MUSIC_FFMPEG_EXECUTABLE']}=={k:v for k,v in after['environment'].items() if k not in ['MUSIC_OSS_ENABLE','MUSIC_OSS_DEPLOYMENT_APPROVED','MUSIC_FFMPEG_EXECUTABLE']}
assert after['environment']['MUSIC_OSS_ENABLE']=='1'
def health():
 for i in range(45):
  try:
   with urllib.request.urlopen('http://127.0.0.1:9000/healthz',timeout=3) as r:assert r.status==200
   return True
  except Exception:time.sleep(2)
 return False
try:
 # Indefinite Docker grace period: no forced kill of the account service.
 subprocess.run(['docker','stop','--time','-1','muxi-auth-muxi-auth-1'],check=True,stdout=subprocess.DEVNULL,timeout=180)
 run(cmd+['up','-d','--no-deps','--no-build','muxi-auth'])
 assert health(),'New account service health failed'
 with urllib.request.urlopen('http://127.0.0.1:9000/api/music/capabilities',timeout=5) as r:cap=json.load(r)
 assert cap.get('enabled') is True,'Unexpected enablement'
 state=json.loads(run(['docker','inspect','muxi-auth-muxi-auth-1']))[0];assert state['Image']==built['imageId']
 result={'status':'DEPLOYED_ENABLED_HEALTHY','imageId':built['imageId'],'rollbackImageId':base['imageId'],'capabilities':cap,'at':time.time(),'productionMusicEnabled':True,'cloudCalls':0,'bucketOrIamChanged':False,'originalConfigPreserved':True}
 (root/'enabled-deployed.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
except Exception:
 subprocess.run(['docker','stop','--time','-1','muxi-auth-muxi-auth-1'],stdout=subprocess.DEVNULL,check=False,timeout=180)
 run(['docker','compose','-f',str(compose),'-f','/opt/muxi-auth/.releases/music-release-0.4.0-20261013/disabled.compose.yaml','up','-d','--no-deps','--no-build','muxi-auth'])
 ok=health();(root/'enabled-rollback.json').write_text(json.dumps({'rollbackHealthy':ok,'at':time.time()}));raise
'''
hop=r'C:\Windows\System32\OpenSSH\ssh.exe -T -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes root@<PRIVATE_HOST> python3 -'
r=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','administrator@<PRIVATE_HOST>',hop],input=source.encode(),capture_output=True,timeout=600)
(N/'backend040-enabled-deploy-output.log').write_bytes(r.stdout+r.stderr)
if r.returncode:raise RuntimeError('Disabled backend update failed; inspect local log')
o=json.loads(r.stdout.decode('utf-8-sig'));(N/'backend040-enabled-deployed.json').write_text(json.dumps(o,indent=2),encoding='utf-8');print(json.dumps(o,indent=2))
