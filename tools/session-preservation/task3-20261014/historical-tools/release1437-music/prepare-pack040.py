from pathlib import Path
import json,hashlib,shutil,zipfile,datetime
N=Path(__file__).resolve().parent;W=N.parent
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
base=read(N/'production-baseline.json');prior=read(W/'release-next-after1435/execution-plan.json');m=read(N/'baseline-manifest.json')
assert m['pack']['version']=='1.4.36' and h(N/'baseline-manifest.json')==prior['manifestSha256']
assert {x['name']:x['sha256'] for x in base['mods']}=={x['name']:x['sha256'] for x in prior['afterMods']}
src=N/'frozen-release040/client/muxi-music-compat-0.4.0-release-candidate.1.jar';assert h(src)=='5f23380fba72fa8e9b77498285fe5d18b0e3a39da07ac63f7fd5f8e3bbe321e5'
old='muxi-music-compat-0.1.0-candidate.1.jar';oldrow=next(x for x in base['mods'] if x['name']==old);assert oldrow['sha256']=='c320c3636cb7dbe949089caef3f3372084caf6fc8183d6388e21ffbf7fca9dd1'
version='1.4.37';url=prior['objectBase'].replace('1.4.36',version);rel='mods/'+src.name
row=dict(path=rel,size=src.stat().st_size,sha1=hashlib.sha1(src.read_bytes()).hexdigest(),policy='Managed',url=url+'files/'+rel)
files=[x for x in m['files'] if x['path']!='mods/'+old]+[row];m['files']=sorted(files,key=lambda x:x['path'])
m['pack'].update(version=version,released=datetime.datetime.now(datetime.timezone.utc).isoformat(),changelog='1.4.37：私有OSS音乐上传、刻录与短期签名播放。支持本地文件和合法直链；真实VIP下载能力未确认，旧非OSS唱片不兼容。保留1.4.36全部模组、Outbreak和六角色YSM。')
save(N/'manifest.json',m);items=[]
for side in ['client','server']:
 dest=N/(side+'-overlay')/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
 items.append(dict(side=side,path=rel,sha256=h(src),sha1=row['sha1'],size=row['size'],url=row['url'],uploadRequired=side=='client'))
after=[x for x in base['mods'] if x['name']!=old]+[dict(name=src.name,sha256=h(src),size=src.stat().st_size)]
plan=dict(version=version,baseVersion='1.4.36',host=base['hostname'],productionRoot=base['productionRoot'],baselinePid=base['listenerIdentity'][0]['pid'],baseManifestSha256=h(N/'baseline-manifest.json'),manifestSha256=h(N/'manifest.json'),publicManifestUrl=prior['publicManifestUrl'],objectPrefix='bmc/release/releases/'+version+'/',objectBase=url,beforeMods=base['mods'],afterMods=sorted(after,key=lambda x:x['name'].lower()),startup=base['startup'],models=base['models'],items=items,remove=[dict(path='mods/'+old,sha256=oldrow['sha256'])],changedClientFiles=1,removedClientPaths=['mods/'+old],sameNameHashReplacements=[],newBackupCreated=False,backupPolicy='none-explicit-current-user',releaseGate='Do not stage/activate until backend enabled approval and minimal actual chain result are resolved',knownRisks=['VIP download authority unknown and unaccepted','Old non-OSS CDs unsupported','Signed URL remains usable until expiry <=720 seconds; not instantly revocable'])
save(N/'execution-plan.json',plan)
for name in ['remote-release.ps1','verify-public.py','verify-health.py']:
 s=(W/'release-next-after1435'/name).read_text(encoding='utf-8').replace('production1436-outbreak','production1437-music').replace('task3-outbreak1436','task3-music1437').replace('1.4.36','1.4.37')
 (N/name).write_text(s,encoding='utf-8')
with zipfile.ZipFile(N/'payload.zip','w',zipfile.ZIP_DEFLATED) as z:
 for name in ['execution-plan.json','manifest.json','baseline-manifest.json']:z.write(N/name,name)
 for side in ['client','server']:z.write(N/(side+'-overlay')/rel,side+'-overlay/'+rel)
L=N/'launcher-smoke';L.mkdir(exist_ok=True);shutil.copy2(W/'release-next-after1435/launcher-smoke/Smoke.csproj',L/'Smoke.csproj')
s=(W/'release-next-after1435/launcher-smoke/Program.cs').read_text(encoding='utf-8').replace('1.4.36','1.4.37').replace('1.4.35','1.4.36').replace('muxi-outbreak-0.4.4-mechanics.2.jar',old);(L/'Program.cs').write_text(s,encoding='utf-8')
a={x['path']:x for x in read(N/'baseline-manifest.json')['files']};b={x['path']:x for x in m['files']};assert all(a[k]==b[k] for k in a.keys()&b.keys()) and len(b)==9256
assert len(after)==350 and len([x for x in m['files'] if '/11_umamusume_player/' in x['path'] and x['policy']=='Managed'])==127
print('Prepared only:',version,'manifest',plan['manifestSha256'],'one JAR replacement;9255 other file entries unchanged')
