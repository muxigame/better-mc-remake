from pathlib import Path
import json,subprocess,base64,hashlib,sys
N=Path(__file__).resolve().parent;D=N/'backend-delta'
review=json.loads((N/'backend-review.json').read_text())
files={}
for x in review['backendModules']:
 p=D/x['path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==x['sha256'];files[x['path']]=base64.b64encode(p.read_bytes()).decode()
payload={'files':files,'phase':sys.argv[1],'expectedBase':'sha256:d4acc505e050814ba1eb80b3d05c24c32cd7fcefdb9d96450c2b8cca5b96d1a4'}
source='payload='+repr(payload)+'\n'+r'''
import pathlib,json,subprocess,base64,hashlib,time,urllib.request
root=pathlib.Path('/opt/muxi-auth/.releases/music-lyrics-1.4.39-20261014');old=pathlib.Path('/opt/muxi-auth/.releases/music-repair-0.4.1-20261013/enabled.compose.yaml');compose=pathlib.Path('/opt/muxi-auth/compose.prod.yaml');tag='muxi-auth:music-lyrics-1.4.39-20261014'
def run(a):return subprocess.check_output(a,text=True,cwd='/opt/muxi-auth')
def state():return json.loads(run(['docker','inspect','muxi-auth-muxi-auth-1']))[0]
assert hashlib.sha256(compose.read_bytes()).hexdigest()=='08ee503f55a22a4929945d9d010daf9ba8448f75c55b1a7e138846972cef92ee'
beforecmd=['docker','compose','-f',str(compose),'-f',str(old)]
if payload['phase']=='build':
 assert state()['Image']==payload['expectedBase'],'Production backend changed'
 assert not root.exists(),'Release root occupied'
 assert json.loads(run(['docker','image','inspect','muxi-auth:music-repair-0.4.1-20261013']))[0]['Id']==payload['expectedBase']
 root.mkdir();ctx=root/'context';ctx.mkdir()
 for rel,b in payload['files'].items():
  p=(ctx/rel).resolve();assert p.is_relative_to(ctx.resolve());p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(b))
 (ctx/'Dockerfile').write_text('FROM muxi-auth:music-repair-0.4.1-20261013\nUSER root\nCOPY app/ /app/app/\nRUN python -m compileall -q /app/app && python -c "import oss2" && test -x /usr/bin/ffmpeg\nUSER muxi\n')
 with (root/'build.log').open('w') as log:r=subprocess.run(['docker','build','-t',tag,str(ctx)],stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 assert r.returncode==0,'Backend build failed; inspect release build.log'
 image=json.loads(run(['docker','image','inspect',tag]))[0]['Id'];assert state()['Image']==payload['expectedBase']
 out={'imageId':image,'tag':tag,'baseImageId':payload['expectedBase'],'contextHashes':{n:hashlib.sha256(base64.b64decode(b)).hexdigest() for n,b in payload['files'].items()},'productionChanged':False}
 (root/'built.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
elif payload['phase']=='deploy':
 built=json.loads((root/'built.json').read_text());assert state()['Image']==payload['expectedBase'];assert json.loads(run(['docker','image','inspect',tag]))[0]['Id']==built['imageId']
 override=root/'enabled.compose.yaml';assert not override.exists();override.write_text(old.read_text().replace('muxi-auth:music-repair-0.4.1-20261013',tag));cmd=['docker','compose','-f',str(compose),'-f',str(override)]
 before=json.loads(run(beforecmd+['config','--format','json']));after=json.loads(run(cmd+['config','--format','json']));before['services']['muxi-auth']['image']=tag;assert before==after,'Unexpected compose change'
 def health():
  for i in range(45):
   try:
    with urllib.request.urlopen('http://127.0.0.1:9000/healthz',timeout=3) as r:assert r.status==200
    return True
   except Exception:time.sleep(2)
  return False
 try:
  subprocess.run(['docker','stop','--time','-1','muxi-auth-muxi-auth-1'],check=True,stdout=subprocess.DEVNULL,timeout=180)
  run(cmd+['up','-d','--no-deps','--no-build','muxi-auth']);assert health()
  with urllib.request.urlopen('http://127.0.0.1:9000/api/music/capabilities',timeout=5) as r:cap=json.load(r)
  assert cap.get('enabled') is True and state()['Image']==built['imageId']
  verify="import hashlib,json,pathlib; names="+repr(list(payload['files']))+";print(json.dumps({n:hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest() for n in names}))"
  actual=json.loads(run(['docker','exec','muxi-auth-muxi-auth-1','python','-c',verify]));assert actual==built['contextHashes']
  out={'status':'DEPLOYED_ENABLED_HEALTHY','imageId':built['imageId'],'rollbackImageId':payload['expectedBase'],'capabilities':cap,'moduleHashesVerified':actual,'cloudQaCalls':0,'configurationUnchangedExceptImage':True,'atUtc':time.time()};(root/'deployed.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
 except Exception:
  subprocess.run(['docker','stop','--time','-1','muxi-auth-muxi-auth-1'],stdout=subprocess.DEVNULL,timeout=180)
  run(beforecmd+['up','-d','--no-deps','--no-build','muxi-auth']);ok=health();(root/'rollback.json').write_text(json.dumps({'healthy':ok}));raise
else:raise ValueError('unknown phase')
'''
hop=r'C:\Windows\System32\OpenSSH\ssh.exe -T -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes root@<PRIVATE_HOST> python3 -'
r=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','administrator@<PRIVATE_HOST>',hop],input=source.encode(),capture_output=True,timeout=1500)
(N/('backend-'+sys.argv[1]+'.log')).write_bytes(r.stdout+r.stderr)
if r.returncode:raise RuntimeError('Backend phase failed; inspect local log')
out=json.loads(r.stdout.decode('utf-8-sig'));(N/('backend-'+sys.argv[1]+'.json')).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out))
