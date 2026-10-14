from pathlib import Path
import json,hashlib,zipfile,urllib.request,urllib.parse,shutil,datetime
out=Path('next-release-20261012');out.mkdir(exist_ok=True)
root=Path('C:/Users/ranzh/workspace/dev/muxigame');shop=Path('C:/Users/ranzh/Documents/Codex/2026-10-02/task/shop-ui-20261011/published-five-v3')
sha=lambda b:hashlib.sha256(b).hexdigest()
handoff=json.loads((root/'muxi-game-core/build/teleport-network-20261012-020620/handoff.json').read_text(encoding='utf-8-sig'))
sh=json.loads((shop/'HANDOFF.json').read_text(encoding='utf-8-sig'))
inputs=[{'component':'muxi-game-core',**handoff['candidate']},*sh['artifacts'],{'component':'muxi-tower-defense','path':str(root/'muxi-tower-defense/build/review/compact2-20261011/muxi-tower-defense-0.3.0-compact.2.jar'),'sha256':'fdcd6d9114f626d18ac623ab31a88fd2345e00bbbf58f365308c78e2ee07cc63'}]
a=json.load(urllib.request.urlopen('https://mc.muxigame.com/api/v1/manifest',timeout=30));raw=urllib.request.urlopen(a['manifestUrl'],timeout=30).read();m=json.loads(raw);(out/'baseline-manifest.json').write_bytes(raw)
reports=[]
for x in inputs:
 p=Path(x['path']);b=p.read_bytes();assert sha(b)==x['sha256'],str(p)
 dst=out/'frozen-inputs'/p.name;dst.parent.mkdir(exist_ok=True);dst.write_bytes(b)
 f=next(f for f in m['files'] if f['path'].startswith('mods/'+x['component']+'-'))
 u=f.get('url') or a['manifestUrl'].rsplit('/',1)[0]+'/files/'+urllib.parse.quote(f['path']);old=urllib.request.urlopen(u,timeout=50).read();assert hashlib.sha1(old).hexdigest()==f['sha1']
 oldp=out/'comparison-baseline'/Path(f['path']).name;oldp.parent.mkdir(exist_ok=True);oldp.write_bytes(old)
 with zipfile.ZipFile(oldp) as za,zipfile.ZipFile(dst) as zb:
  aa={n:sha(za.read(n)) for n in za.namelist() if not n.endswith('/')};bb={n:sha(zb.read(n)) for n in zb.namelist() if not n.endswith('/')}
  added=sorted(bb.keys()-aa.keys());removed=sorted(aa.keys()-bb.keys());changed=sorted(n for n in aa.keys()&bb.keys() if aa[n]!=bb[n])
  meta={n:zb.read(n).decode('utf-8',errors='replace') for n in ['META-INF/neoforge.mods.toml','META-INF/MANIFEST.MF'] if n in bb}
 r={'component':x['component'],'source':str(p),'frozenPath':str(dst.resolve()),'candidateSha256':sha(b),'baselinePath':f['path'],'baselineSha256':sha(old),'added':added,'removed':removed,'changed':changed,'metadata':meta};reports.append(r)
 (out/(x['component']+'-zip-diff.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
 print(x['component'], 'added',len(added),'removed',len(removed),'changed',len(changed));print('REMOVED',removed[:30]);print('CHANGED',changed[:45])
result={'createdUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':'PREPARATION_ONLY_NOT_APPROVED_FOR_DEPLOYMENT','baselineVersion':m['pack']['version'],'baselineManifestSha256':sha(raw),'inputs':[{k:v for k,v in r.items() if k not in ['metadata','added','removed','changed']} for r in reports],'excluded':['zombie ammunition until native reload passes','YSM memory candidate','Outbreak full-mechanics candidate','network music OSS','unapproved five-character background v3'],'newBackupCreated':False,'productionWrites':False,'mcStarted':False}
(out/'input-lock.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('BASELINE',m['pack']['version'],sha(raw))
