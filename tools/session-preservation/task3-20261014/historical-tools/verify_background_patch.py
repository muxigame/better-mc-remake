from pathlib import Path
import json,hashlib,zipfile,urllib.request,datetime
r=Path('C:/Users/ranzh/workspace/dev/muxigame/muxi-ysm-tools/build/background-fill-qa-20261011')
h=json.loads((r/'TEN-FILE-REPLACEMENT-HANDOFF.json').read_text(encoding='utf-8-sig'))
out=Path('background-patch-readonly');out.mkdir(exist_ok=True)
sha=lambda b:hashlib.sha256(b).hexdigest()
get=lambda u:urllib.request.urlopen(u,timeout=35).read()
a=json.loads(get('https://mc.muxigame.com/api/v1/manifest'));raw=get(a['manifestUrl']);m=json.loads(raw);lookup={f['path']:f for f in m['files']}
prefix='config/yes_steve_model/custom/'
checks=[]; zipdata={}
for k in ['runtimeZIP','patchZIP']:
 p=Path(h[k]['path']);z=zipfile.ZipFile(p);d={n:z.read(n) for n in z.namelist() if not n.endswith('/')};zipdata[k]=d
 checks.append({'kind':k,'sha256':sha(p.read_bytes()),'hashMatch':sha(p.read_bytes())==h[k]['sha256'],'count':len(d),'crcBadFile':z.testzip(),'paths':list(d) if k=='patchZIP' else None})
print(json.dumps(checks))
runtime=zipdata['runtimeZIP'];patch=zipdata['patchZIP'];rows=[]
for f in h['oldToNewFiles']:
 path=f['path'];entry=lookup[prefix+path];old=get(entry['url']);new=patch[path]
 rows.append({**f,'liveOldMatch':sha(old)==f['old134SHA256'],'newMatch':sha(new)==f['newSHA256'],'fullZipMatch':runtime[path]==new,'policy':entry['policy'],'bytes':len(new)})
diff=[]
for path,b in runtime.items():
 e=lookup[prefix+path]
 if hashlib.sha1(b).hexdigest()!=e['sha1']:diff.append(path)
result={'atUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'liveVersion':m['pack']['version'],'liveManifestSha256':sha(raw),'packages':checks,'files':rows,'runtimeDeltaPaths':diff,'exactTenFileDelta':set(diff)=={f['path'] for f in h['oldToNewFiles']},'allChecksPassed':all(x['hashMatch'] and x['crcBadFile'] is None for x in checks) and all(f['liveOldMatch'] and f['newMatch'] and f['fullZipMatch'] and f['policy']=='Managed' for f in rows),'productionWritten':False}
(out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');(out/'baseline-manifest.json').write_bytes(raw)
print(json.dumps({k:v for k,v in result.items() if k not in ['packages','files','runtimeDeltaPaths']}))
for name in ['final-self-verification.json','native-exit-verification.json','root-audit/background-only-preservation.json']:
 print(name,(r/name).read_text(encoding='utf-8-sig')[:13000])
