from pathlib import Path
import json,hashlib,urllib.request,urllib.parse,concurrent.futures,datetime
N=Path(__file__).resolve().parent
plan=json.loads((N/'execution-plan.json').read_text(encoding='utf-8'))
def get(url):return urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache'}),timeout=40).read()
api=json.loads(get('https://mc.muxigame.com/api/v1/manifest'))
raw=get(api['manifestUrl']);manifest=json.loads(raw)
assert hashlib.sha256(raw).hexdigest()==plan['manifestSha256'] and manifest['pack']['version']=='1.4.40'
files={f['path']:f for f in manifest['files']}
ysm=[f for f in files.values() if '/11_umamusume_player/' in f['path']]
assert len(ysm)==127 and all(f['policy']=='Managed' for f in ysm)
assert all(p not in files for p in plan['removedClientPaths'])
rows=[f for f in plan['items'] if f['side']=='client']
expected={f['path']:f['sha256'] for f in rows}
samples=[files[f['path']] for f in rows]+[f for f in files.values() if f['path'].endswith('/ysm.json') and any(c in f['path'] for c in ['/11_umamusume_player/','/10_umamusume/'])]
def verify(f):
 url=f.get('url') or api['filesBaseUrl'].rstrip('/')+'/'+urllib.parse.quote(f['path'],safe='/')
 b=get(url);sha256=hashlib.sha256(b).hexdigest()
 assert hashlib.sha1(b).hexdigest()==f['sha1'] and len(b)==f['size'],f['path']
 if f['path'] in expected:assert sha256==expected[f['path']]
 return {'path':f['path'],'sha256':sha256,'sha1':f['sha1'],'verified':True}
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:checks=list(pool.map(verify,samples))
site=json.loads(get('https://mc.muxigame.com/api/v1/site'))
assert site['pack']['version']=='1.4.40','API site mirror stale'
result={'atUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'version':'1.4.40','manifestSha256':plan['manifestSha256'],'apiManifestUrl':api['manifestUrl'],'apiSiteVersion':site['pack']['version'],'verifiedPublicObjects':checks,'ysmManagedFiles':127,'removedTrackedPaths':plan['removedClientPaths'],'sameNameManagedReplacements':plan['sameNameHashReplacements']}
(N/'public-final-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='verifiedPublicObjects'},ensure_ascii=False));print('Public sampled objects:',len(checks))
