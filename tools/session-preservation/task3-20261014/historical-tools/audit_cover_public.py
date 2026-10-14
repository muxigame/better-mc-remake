from pathlib import Path
import json,hashlib,urllib.request,zipfile,concurrent.futures,datetime
out=Path('cover-readonly-audit');out.mkdir(exist_ok=True)
sha=lambda b:hashlib.sha256(b).hexdigest()
get=lambda u:urllib.request.urlopen(u,timeout=40).read()
a=json.loads(get('https://mc.muxigame.com/api/v1/manifest')); raw=get(a['manifestUrl']);m=json.loads(raw)
(out/'live-manifest.json').write_bytes(raw)
h=json.loads(Path('C:/Users/ranzh/Documents/Codex/2026-10-09/task-3/new-release/ysm-handoff.json').read_text(encoding='utf-8-sig'))
zpath=Path(h['total']['path']); z=zipfile.ZipFile(zpath)
prefix='config/yes_steve_model/custom/11_umamusume_player/'
files=[x for x in m['files'] if x['path'].startswith(prefix)]
expected={prefix+model['archiveDirectory'].split('/')[-1]+'/'+f['path']:f['sha256'] for model in h['models'] for f in model['fileManifest']}
def check(f):
 b=get(f['url']);rel=f['path'][len(prefix):]; dest=out/'public'/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(b)
 overlay=Path('release1434-revised/client-overlay')/f['path']
 return {'path':f['path'],'url':f['url'],'policy':f.get('policy'),'sha256':sha(b),'matchesManifest':hashlib.sha1(b).hexdigest()==f['sha1'],'matchesReleaseHandoff':sha(b)==expected.get(f['path']),'matchesReleaseOverlay':b==overlay.read_bytes(),'matchesCurrentCandidateZip':b==z.read('grounding_review_20261011/'+rel)}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: rows=list(pool.map(check,files))
old=[]
for f in m['files']:
 if '/10_umamusume/' in f['path'] and f['path'].endswith('/ysm.json'):
  u=f.get('url'); old.append({'path':f['path'],'policy':f.get('policy'),'sha1':f['sha1'],'url':u})
configs=[]
for p in sorted((out/'public').glob('*/ysm.json')):
 c=json.loads(p.read_text(encoding='utf-8-sig'));configs.append({'directory':p.parent.name,'name':c.get('metadata',{}).get('name'),'properties':c.get('properties')})
result={'atUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'manifestUrl':a['manifestUrl'],'publicVersion':m['pack']['version'],'manifestSha256':sha(raw),'candidateZipSha256':sha(zpath.read_bytes()),'releaseCandidateZipSha256':h['total']['sha256'],'count':len(rows),'allManaged':all(r['policy']=='Managed' for r in rows),'allMatch':all(all(r[k] for k in ['matchesManifest','matchesReleaseHandoff','matchesReleaseOverlay','matchesCurrentCandidateZip']) for r in rows),'testCategoryEntries':[f['path'] for f in m['files'] if 'grounding_review_20261011' in f['path']],'configs':configs,'legacyConfigs':old,'files':rows,'visualScope':'Prior release screenshots show in-world models, not selector cover backgrounds. User installation and selected model not inspected.'}
(out/'public-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ['files','configs','legacyConfigs']},ensure_ascii=False));print(json.dumps(configs,ensure_ascii=False));print(json.dumps(old,ensure_ascii=False))
