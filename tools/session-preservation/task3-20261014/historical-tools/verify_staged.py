from pathlib import Path
import json,hashlib,urllib.request,concurrent.futures,time
r=Path(r'C:/Users/ranzh/Documents/Codex/2026-10-09/task-3/new-release');plan=json.loads((r/'execution-plan.json').read_text());m=json.loads((r/'manifest.json').read_text());files={x['path']:x for x in m['files']}
def verify(x):
 f=files[x['path']];b=urllib.request.urlopen(f['url'],timeout=40).read();return {'path':x['path'],'ok':hashlib.sha256(b).hexdigest()==x['sha256'] and hashlib.sha1(b).hexdigest()==f['sha1'],'size':len(b)}
rows=[x for x in plan['items'] if x['side']=='client']
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:out=list(pool.map(verify,rows))
Path('staged-public-objects-check.json').write_text(json.dumps({'checkedAt':time.time(),'passed':all(x['ok'] for x in out),'files':out},ensure_ascii=False,indent=2));print('staged public',len(out),'passed',all(x['ok'] for x in out))
