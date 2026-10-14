from pathlib import Path
import json,hashlib,shutil,datetime,zipfile
base=Path('C:/Users/ranzh/Documents/Codex/2026-10-09/task-3');here=Path(__file__).resolve().parent;out=here/'next-release-20261012/fullpack-smoke';out.mkdir(exist_ok=True)
read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
freeze=read(base/'race-native/input-freeze.json');p33=read(base/'release-entry3/execution-plan.json');p34=read(here/'release1434-revised/execution-plan.json');live=read(here/'next-release-20261012/baseline-manifest.json');lookup={f['path']:f for f in live['files']};inputs=read(here/'next-release-20261012/input-lock.json');reports={}
for side in ['client','server']:
 dst=out/side;dst.mkdir(exist_ok=True)
 for src in (base/'fullpack-frozen'/side).iterdir():
  if src.name=='mods':continue
  target=dst/src.name
  if src.is_dir():shutil.copytree(src,target,dirs_exist_ok=True)
  else:shutil.copy2(src,target)
 for plan,folder in [(p33,base/'joint-final-entry3-stage'),(p34,here/'release1434-revised')]:
  for f in plan['items']:
   if f['side']!=side or f['path'].endswith('.jar'):continue
   src=folder/(side+'-overlay')/f['path'];assert sha(src)==f['sha256'],str(src);target=dst/f['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
 mods={f['name']:{'source':f['source'],'sha256':f['sha256']} for f in freeze['baseline'+side.capitalize()]}
 for f in p34['remove']:mods.pop(Path(f['path']).name,None)
 for f in p34['items']:
  if f['side']==side and f['path'].endswith('.jar'):mods[Path(f['path']).name]={'source':str(here/'release1434-revised'/(side+'-overlay')/f['path']),'sha256':f['sha256']}
 baseline=[]
 for name,f in mods.items():
  src=Path(f['source']);assert sha(src)==f['sha256'],str(src)
  if side=='client':assert hashlib.sha1(src.read_bytes()).hexdigest()==lookup['mods/'+name]['sha1'],name
  baseline.append({'name':name,'sha256':f['sha256']})
  target=dst/'mods'/name;target.parent.mkdir(exist_ok=True);shutil.copy2(src,target)
 changes=[]
 for f in inputs['inputs']:
  if f['component'] not in ['muxi-game-core','muxi-tower-defense']:continue
  old=dst/f['baselinePath'];assert sha(old)==f['baselineSha256'];old.unlink();src=Path(f['frozenPath']);target=dst/'mods'/src.name;shutil.copy2(src,target);changes.append({'old':f['baselinePath'],'new':'mods/'+src.name,'sha256':sha(target)})
 files=[];mismatches=[]
 for p in dst.rglob('*'):
  if not p.is_file():continue
  rel=p.relative_to(dst).as_posix();b=p.read_bytes();files.append({'path':rel,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
  if side=='client' and not rel.startswith('mods/') and rel in lookup and hashlib.sha1(b).hexdigest()!=lookup[rel]['sha1']:mismatches.append(rel)
 reports[side]={'baselineMods':baseline,'modCount':len(list((dst/'mods').glob('*.jar'))),'changes':changes,'files':files,'clientConfigMismatchesWithLive':mismatches}
 print(side,'mods',reports[side]['modCount'],'files',len(files),'mismatches',mismatches,flush=True)
report={'state':'OFFLINE_FROZEN_NO_MC_STARTED','baseline':'1.4.34','manifestSha256':sha(here/'next-release-20261012/baseline-manifest.json'),'sides':reports,'worldPolicy':'Use existing isolated initialized test world copy at run time; never production or user world','limitations':['Server configuration reconstructed from previously frozen release templates and applied release overlays, not a fresh complete production-config comparison','No combined native runtime acceptance yet'],'mcStarted':False,'productionChanged':False}
(out/'frozen-fullpack-inputs.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
