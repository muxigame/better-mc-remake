from pathlib import Path
import json,hashlib,shutil,datetime,zipfile,copy
N=Path(__file__).resolve().parent;W=N.parent
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
base=read(N/'production-baseline.json');oldplan=read(W/'release1435/execution-plan.json');m=read(N/'baseline-manifest.json')
assert m['pack']['version']=='1.4.35' and h(N/'baseline-manifest.json')==oldplan['manifestSha256']
assert {x['name']:x['sha256'] for x in base['mods']}=={x['name']:x['sha256'] for x in oldplan['afterMods']}
D=Path(r'C:\Users\ranzh\Documents\Codex\2026-10-09\task-4\delivery-school4-candidate11-release')
handoff=read(D/'handoff.json');compat=read(D/'compatibility-1.4.35.json')
assert all(any(x['sha256']==d['sha256'] for x in base['mods']) for d in compat['directDependencies'])
bundle=D.parent/'outbreak-school4-candidate11-release-handoff.zip'
assert h(bundle)=='2a871677db57396033e87e8286b4c81e0b9805c8bdb3c40108c10cc4c542f296'
src=D.parent/'outbreak-fix/build/libs'/handoff['jar'];assert h(src)==handoff['sha256']
oldname=handoff['replaceBothSides']['name'];oldrow=next(x for x in base['mods'] if x['name']==oldname);assert oldrow['sha256']==handoff['replaceBothSides']['sha256']
for f in ['handoff.json','compatibility-1.4.35.json','RELEASE-HANDOFF.md']:shutil.copy2(D/f,N/f)
version='1.4.36';url=oldplan['objectBase'].replace('1.4.35',version);rel='mods/'+src.name
row=dict(path=rel,size=src.stat().st_size,sha1=hashlib.sha1(src.read_bytes()).hexdigest(),policy='Managed',url=url+'files/'+rel)
files=[x for x in m['files'] if x['path']!='mods/'+oldname];files.append(row);m['files']=sorted(files,key=lambda x:x['path'])
m['pack'].update(version=version,released=datetime.datetime.now(datetime.timezone.utc).isoformat(),changelog='1.4.36：更新求生校园章节候选，供玩家实玩验收。保留1.4.35全部更新与六角色YSM。已知AI西梯可能受到跌落伤害；检查点请用原生右键关门，F键关门尚有问题；自然三章尚未完成验收。')
save(N/'manifest.json',m)
items=[]
for side in ['client','server']:
 dest=N/(side+'-overlay')/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
 items.append(dict(side=side,path=rel,sha256=h(src),sha1=row['sha1'],size=row['size'],url=row['url'],uploadRequired=side=='client'))
after=[x for x in base['mods'] if x['name']!=oldname]+[dict(name=src.name,sha256=h(src),size=src.stat().st_size)]
plan=dict(version=version,baseVersion='1.4.35',host=base['hostname'],productionRoot=base['productionRoot'],baselinePid=base['listenerIdentity'][0]['pid'],baseManifestSha256=h(N/'baseline-manifest.json'),manifestSha256=h(N/'manifest.json'),publicManifestUrl=oldplan['publicManifestUrl'],objectPrefix='bmc/release/releases/'+version+'/',objectBase=url,beforeMods=base['mods'],afterMods=sorted(after,key=lambda x:x['name'].lower()),startup=base['startup'],models=base['models'],items=items,remove=[dict(path='mods/'+oldname,sha256=oldrow['sha256'])],changedClientFiles=1,removedClientPaths=['mods/'+oldname],sameNameHashReplacements=[],newBackupCreated=False,backupPolicy='none-explicit-current-user',scopeEvidence='handoff.json',knownRisks=handoff['knownRisks'])
save(N/'execution-plan.json',plan)
for name in ['controller.py','remote-release.ps1','verify-public.py','verify-health.py']:
 s=(W/'release1435'/name).read_text(encoding='utf-8').replace('production1435-five','production1436-outbreak').replace('task3-five1435','task3-outbreak1436').replace('1.4.35','1.4.36').replace('objectsVerified=5','objectsVerified=1')
 (N/name).write_text(s,encoding='utf-8')
with zipfile.ZipFile(N/'payload.zip','w',zipfile.ZIP_DEFLATED) as z:
 for name in ['execution-plan.json','manifest.json','baseline-manifest.json','handoff.json','compatibility-1.4.35.json']:z.write(N/name,name)
 for side in ['client','server']:z.write(N/(side+'-overlay')/rel,side+'-overlay/'+rel)
L=N/'launcher-smoke';L.mkdir(exist_ok=True)
shutil.copy2(next((W/'release1435/launcher-smoke').glob('*.csproj')),L/'Smoke.csproj')
s=(W/'release1435/launcher-smoke/Program.cs').read_text(encoding='utf-8').replace('1.4.35','1.4.36').replace('1.4.34','1.4.35')
s=s.replace('"muxi-terminal-0.2.4-task14-social-qa.1.jar","muxi-horse-racing-0.2.2-race.5.jar","muxi-tower-defense-0.2.0-world.1.jar"','"'+oldname+'"').replace('Downloads.Count!=5','Downloads.Count!=1').replace('Deletions.Count!=3','Deletions.Count!=1').replace('Expected5downloads3deletions','Expected1download1deletion').replace('downloads=5','downloads=1').replace('oldTrackedRemoved=3','oldTrackedRemoved=1').replace('sameNameManagedReplacements=2','sameNameManagedReplacements=0')
(L/'Program.cs').write_text(s,encoding='utf-8')
assert len(m['files'])==9256 and len(after)==350
print(json.dumps(dict(version=version,manifestSha256=plan['manifestSha256'],payloadSha256=h(N/'payload.zip'),candidateSha256=h(src),changed=1,removed=1)))
