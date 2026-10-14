from pathlib import Path
import json,hashlib,urllib.request,concurrent.futures,datetime
N=Path(__file__).resolve().parent
plan=json.loads((N/'execution-plan.json').read_text(encoding='utf-8'))
api=json.load(urllib.request.urlopen('https://mc.muxigame.com/api/v1/manifest',timeout=30))
raw=urllib.request.urlopen(api['manifestUrl'],timeout=30).read();m=json.loads(raw)
assert m['pack']['version']=='1.4.34'
assert hashlib.sha256(raw).hexdigest()==plan['manifestSha256']
files={x['path']:x for x in m['files']}
ysm=[x for x in m['files'] if '/11_umamusume_player/' in x['path']]
assert len(ysm)==127 and all(x['policy']=='Managed' for x in ysm)
assert not any('grounding_review_20261011' in x['path'] for x in m['files'])
for x in plan['remove']:assert x['path'] not in files
rows=[x for x in plan['items'] if x['side']=='client']
def verify(x):
 f=files[x['path']];b=urllib.request.urlopen(f['url'],timeout=40).read()
 assert hashlib.sha256(b).hexdigest()==x['sha256'] and hashlib.sha1(b).hexdigest()==f['sha1'],x['path']
 return {'path':x['path'],'sha256':x['sha256'],'verified':True}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:checks=list(pool.map(verify,rows))
endpoints=json.load(urllib.request.urlopen('https://mc.muxigame.com/api/v1/tunnel/endpoints',timeout=30))
o={'atUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'version':m['pack']['version'],'manifestSha256':plan['manifestSha256'],'clientObjectsVerified':len(checks),'ysmManagedFiles':127,'files':checks,'endpointResponseReceived':True}
(N/'public-final-verification.json').write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in o.items() if k!='files'},ensure_ascii=False))
