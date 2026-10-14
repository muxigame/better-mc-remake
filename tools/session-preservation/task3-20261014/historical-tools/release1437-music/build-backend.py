from pathlib import Path
import json,subprocess,base64,hashlib
N=Path(__file__).resolve().parent;D=N/'frozen/backend-delta'
files={str(p.relative_to(D)).replace('\\','/'):base64.b64encode(p.read_bytes()).decode() for p in D.rglob('*') if p.is_file() and (str(p.relative_to(D)).startswith('app') or p.name=='requirements-music.txt')}
baseline=json.loads((N/'backend-host-baseline.json').read_text())
docker='''FROM muxi-auth:local
USER root
COPY requirements-music.txt /tmp/music-requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r /tmp/music-requirements.txt
COPY app/ /app/app/
ENV MUSIC_OSS_ENABLE=0
USER muxi
CMD ["uvicorn","app.music_production_entry:app","--host","0.0.0.0","--port","9000","--proxy-headers","--forwarded-allow-ips=*"]
'''
files['Dockerfile']=base64.b64encode(docker.encode()).decode()
payload={'files':files,'baseImage':baseline['imageId'],'tag':'muxi-auth:music-private-0.3.2-20261013'}
source='payload='+repr(payload)+'\n'+r'''
import pathlib,json,base64,hashlib,subprocess,time
root=pathlib.Path('/opt/muxi-auth/.releases/music-private-0.3.2-20261013')
def run(a):return subprocess.check_output(a,text=True)
current=json.loads(run(['docker','inspect','muxi-auth-muxi-auth-1']))[0]
assert current['Image']==payload['baseImage'],'Base image drift'
assert json.loads(run(['docker','image','inspect','muxi-auth:local']))[0]['Id']==payload['baseImage']
assert not root.exists(),'Release path already exists; inspect before reuse'
assert subprocess.run(['docker','image','inspect',payload['tag']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0,'Tag occupied'
root.mkdir(parents=True);context=root/'context';context.mkdir()
for rel,data in payload['files'].items():
 p=(context/rel).resolve();assert p.is_relative_to(context.resolve());p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(data))
(root/'baseline.json').write_text(json.dumps({'imageId':payload['baseImage'],'imageTag':'muxi-auth:local','newTag':payload['tag'],'enabled':False},indent=2))
with (root/'build.log').open('w') as log:r=subprocess.run(['docker','build','-t',payload['tag'],str(context)],stdout=log,stderr=subprocess.STDOUT,timeout=1200)
if r.returncode:
 print((root/'build.log').read_text()[-5000:]);raise SystemExit(r.returncode)
image=json.loads(run(['docker','image','inspect',payload['tag']]))[0]
assert json.loads(run(['docker','inspect','muxi-auth-muxi-auth-1']))[0]['Image']==payload['baseImage']
out={'imageId':image['Id'],'tag':payload['tag'],'baseImageId':payload['baseImage'],'productionContainerChanged':False,'musicEnabled':False,'builtAt':time.time(),'contextHashes':{n:hashlib.sha256(base64.b64decode(b)).hexdigest() for n,b in payload['files'].items()}}
(root/'built.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
'''
hop=r'C:\Windows\System32\OpenSSH\ssh.exe -T -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes root@<PRIVATE_HOST> python3 -'
with (N/'backend-build-output.log').open('w',encoding='utf-8') as log:
 r=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','administrator@<PRIVATE_HOST>',hop],input=source.encode(),stdout=log,stderr=subprocess.STDOUT,timeout=1500)
print('Backend build exit',r.returncode);raise SystemExit(r.returncode)
