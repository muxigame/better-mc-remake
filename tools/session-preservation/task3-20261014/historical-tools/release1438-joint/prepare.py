from pathlib import Path
import json,hashlib,shutil,zipfile,datetime
N=Path(__file__).resolve().parent; W=N.parent; R=Path('C:/Users/ranzh/workspace/dev/muxigame')
read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
base=read(N/'production-baseline.json');prev=read(W/'release1437-music/execution-plan.json');m=read(N/'baseline-manifest.json')
assert m['pack']['version']=='1.4.37' and sha(N/'baseline-manifest.json')==prev['manifestSha256']
assert {x['name']:x['sha256'] for x in base['mods']}=={x['name']:x['sha256'] for x in prev['afterMods']}
h=read(R/'muxi-terminal/build/native-final/final-handoff.json');assert h['targetedNativeAcceptancePassed'] and not h['mcRunning'] and not h['remainingBlockers']
assert h['nativeNormalShutdown']['cefState']=='TERMINATED' and not h['nativeNormalShutdown']['forcedCleanup']
save(N/'native-admission.json',h)
version='1.4.38';url=prev['objectBase'].replace('1.4.37',version)
inputs=[(Path(h['terminalCandidate']),h['terminalSha256'],'muxi-terminal-0.2.4-shop-ui-qa.3.jar',['client','server']),
 (Path(h['localizationCandidate']),h['localizationSha256'],None,['client']),
 (W/'music-repair-review-20261013/client/muxi-music-compat-0.4.1-repair-candidate.2.jar','a54c8f96346865134fdf32ecfb986be45b97a0e9f9bc2565e928652258a82a38','muxi-music-compat-0.4.0-release-candidate.1.jar',['client','server'])]
files={x['path']:x for x in m['files']};after={x['name']:x for x in base['mods']};items=[];remove=[];removed=[]
for src,digest,old,sides in inputs:
 assert sha(src)==digest,(src,sha(src));rel='mods/'+src.name;oldrel='mods/'+old if old else None
 if oldrel:
  assert oldrel in files;files.pop(oldrel);removed.append(oldrel)
 row=dict(path=rel,size=src.stat().st_size,sha1=hashlib.sha1(src.read_bytes()).hexdigest(),policy='Managed',url=url+'files/'+rel);files[rel]=row
 for side in sides:
  dst=N/(side+'-overlay')/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
  items.append(dict(side=side,path=rel,sha256=digest,sha1=row['sha1'],size=row['size'],url=row['url'],uploadRequired=side=='client'))
 if 'server' in sides:
  oldrow=after.pop(old);remove.append(dict(path=oldrel,sha256=oldrow['sha256']));after[src.name]=dict(name=src.name,sha256=digest,size=src.stat().st_size)
m['files']=sorted(files.values(),key=lambda x:x['path']);m['pack'].update(version=version,released=datetime.datetime.now(datetime.timezone.utc).isoformat(),changelog='1.4.38：音乐上传读取与M4A修复；终端窗口全屏/还原及CEF正常退出；设置汉化自动加载和Iris标签修复。保留现有求生与六角色YSM。电脑单曲新入口、真实平台VIP及批量歌单尚未验收。')
save(N/'manifest.json',m)
plan=dict(version=version,baseVersion='1.4.37',host=base['hostname'],productionRoot=base['productionRoot'],baselinePid=base['listenerIdentity'][0]['pid'],baseManifestSha256=sha(N/'baseline-manifest.json'),manifestSha256=sha(N/'manifest.json'),publicManifestUrl=prev['publicManifestUrl'],objectPrefix='bmc/release/releases/'+version+'/',objectBase=url,beforeMods=base['mods'],afterMods=sorted(after.values(),key=lambda x:x['name'].lower()),startup=base['startup'],models=base['models'],items=items,remove=remove,changedClientFiles=3,removedClientPaths=removed,sameNameHashReplacements=[],newBackupCreated=False,backupPolicy='none-explicit-current-user',releaseGate='Native admitted; backend repair must be healthy before Minecraft activation',knownRisks=['Music real account/VIP/platform and file chooser original exception not accepted','Computer entry0.4.2 and batch playlists excluded','Outbreak20/21,Create addons,new parkour,YSM upstream excluded'])
save(N/'execution-plan.json',plan)
for name in ['remote-release.ps1','verify-public.py','verify-health.py','controller.py']:
 s=(W/'release1437-music'/name).read_text(encoding='utf-8').replace('production1437-music','production1438-joint').replace('task3-music1437','task3-joint1438').replace('1.4.37','1.4.38')
 if name=='controller.py':
  start=s.index("if args.phase in {'Stage'");end=s.index("plan=json.loads",start);s=s[:start]+s[end:]
 if name=='remote-release.ps1':s=s.replace('objectsVerified=1','objectsVerified=3')
 (N/name).write_text(s,encoding='utf-8')
with zipfile.ZipFile(N/'payload.zip','w',zipfile.ZIP_DEFLATED) as z:
 for name in ['execution-plan.json','manifest.json','baseline-manifest.json']:z.write(N/name,name)
 for x in items:z.write(N/(x['side']+'-overlay')/x['path'],x['side']+'-overlay/'+x['path'])
oldfiles={x['path']:x for x in read(N/'baseline-manifest.json')['files']}
assert len(after)==350 and len(files)==9257 and len(oldfiles)==9256
assert all(oldfiles[k]==files[k] for k in oldfiles.keys()&files.keys())
assert len([x for x in files.values() if '/11_umamusume_player/' in x['path'] and x['policy']=='Managed'])==127
print(json.dumps({'version':version,'manifestSha256':plan['manifestSha256'],'clientChanges':3,'serverChanges':2,'unchangedEntries':9254,'nativeAdmitted':True}))
