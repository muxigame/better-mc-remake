from pathlib import Path
import json,hashlib,urllib.request,urllib.error,urllib.parse,datetime,concurrent.futures
N=Path(__file__).resolve().parent
D=Path(r'C:\Users\ranzh\Documents\Codex\2026-10-02\task-2\music-oss-candidate\build\publish-handoff-20261012')
handoff=json.loads((D/'manifest.json').read_text(encoding='utf-8'))
manifest=json.loads((N/'manifest.json').read_text(encoding='utf-8'));files={x['path']:x for x in manifest['files']}
def check(item):
 rel,row=item;name=Path(rel).name;entry=files['mods/'+name]
 with urllib.request.urlopen(entry.get('url') or json.loads((N/'api-baseline.json').read_text())['filesBaseUrl'].rstrip('/')+'/'+urllib.parse.quote(entry['path'],safe='/'),timeout=30) as r:b=r.read()
 assert hashlib.sha256(b).hexdigest()==row['sha256']
 return dict(path=entry['path'],sha256=row['sha256'],alreadyPublic=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 checks=list(pool.map(check,[(k,v) for k,v in handoff['files'].items() if k.startswith('minimal-legacy/client/')]))
url='https://account.muxigame.com/api/music/capabilities'
try:
 with urllib.request.urlopen(url,timeout=20) as r:status=r.status;body=r.read(4096).decode()
except urllib.error.HTTPError as e:status=e.code;body=e.read(4096).decode()
out=dict(atUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),publicLegacyObjects=checks,capabilities=dict(url=url,status=status,body=body),ossCandidate=handoff['ossArtifact'],productionWrites=False,cloudWrites=False,qaBudgetUnchanged='45/60',blockers=['Generic production storage/provider bootstrap absent from handoff; QA UID10000/two-job dispatcher must not be deployed','Persistent anonymous GET scope requires explicit choice and approval; prior grant covered only two exact QA keys','Candidate2 rejects non-OSS sources; legacy CD compatibility must be resolved before replacing already-live legacy adapter'],deploymentOrder=['Freeze reviewed provider and legacy compatibility candidate with exact hashes','Review chosen music-only public GET policy; retain private incoming and existing identities','Apply authorized backend and chosen policy, verify real capabilities enabled and basic upload/redeem/playback','Publish matching client/server delta through normal production release'])
(N/'music-production-readiness.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
