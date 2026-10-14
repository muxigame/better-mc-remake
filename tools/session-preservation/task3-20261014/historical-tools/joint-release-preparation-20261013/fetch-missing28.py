from pathlib import Path
import json,hashlib,urllib.request,urllib.parse,concurrent.futures,datetime
n=Path('joint-release-preparation-20261013');out=n/'outbreak20-test-inputs';receipt=Path(r'C:/Users/ranzh/Documents/Codex/2026-10-09/task-4/delivery-school4-candidate20-offline/input-preparation-receipt.json')
def get(url):return urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache'}),timeout=45).read()
api=json.loads(get('https://mc.muxigame.com/api/v1/manifest'));raw=get(api['manifestUrl']);assert hashlib.sha256(raw).hexdigest()=='046f7e3e1fa8c22999d7620038ac4bfa4bfc948277ea36381507be6f12e4f0d6';m=json.loads(raw);assert m['pack']['version']=='1.4.37'
files={x['path']:x for x in m['files']};missing=json.loads(receipt.read_text())['missing']
def download(item):
 rel=item['path'].replace('\\','/');f=files[rel];url=f.get('url') or api['filesBaseUrl'].rstrip('/')+'/'+urllib.parse.quote(rel,safe='/');data=get(url)
 assert len(data)==f['size'] and hashlib.sha1(data).hexdigest()==f['sha1'],rel
 p=out/'supplement-client'/rel;assert p.resolve().is_relative_to((out/'supplement-client').resolve());p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 return {'path':rel,'bytes':len(data),'sha1':f['sha1'],'sha256':hashlib.sha256(data).hexdigest(),'policy':f['policy'],'sourceUrl':url}
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:rows=list(pool.map(download,missing))
result={'checkedUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'liveVersion':'1.4.37','liveManifestSha256':hashlib.sha256(raw).hexdigest(),'originalInputLockSha256':hashlib.sha256((out/'input-lock.json').read_bytes()).hexdigest(),'all28Exact':len(rows)==28,'files':rows,'ignoredFiles':[],'cachePolicy':'Both MCEF cache entries remain exact pre-start baseline inputs because the live manifest tracks them as Managed. No ignore exception added. Runtime mutation is not evidence of baseline mismatch after startup.','musicCloudCalls':0,'minecraftStarted':False}
(out/'supplement-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'verified':len(rows),'bytes':sum(x['bytes'] for x in rows),'manifest':result['liveManifestSha256'],'originalLockUnchanged':result['originalInputLockSha256']}))
