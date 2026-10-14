import pathlib,re,json,collections,datetime,gzip
R=pathlib.Path(r'C:\Users\Administrator\WorkSpace\muxigame\.releases\production1432-r2');S=pathlib.Path(r'C:\Users\Administrator\WorkSpace\muxigame\bmc5server')
def scan(path):
 errors=collections.Counter();done=[];fatal=[];mods=[]
 with (gzip.open(path,'rt',encoding='utf-8',errors='replace') if path.suffix=='.gz' else path.open(encoding='utf-8',errors='replace')) as f:
  for line in f:
   if 'Done (' in line:done.append(line.strip())
   if any(x in line for x in ['FATAL','ModLoadingException','Exception in server tick loop','Failed to start the minecraft server']):fatal.append(line.strip())
   if '/ERROR]' in line:
    msg=re.sub(r'^\[[^]]*\]\s*\[[^]]*\]\s*','',line.strip());errors[msg]+=1
   if any(x in line for x in ['muxi-tower-defense','muxi-flight','muxi-outbreak','muxi-game-core']) and any(x in line.lower() for x in ['loaded','initializ','version','register']):mods.append(line.strip())
 return errors,done,fatal,mods
old,_,_,_=scan(R/'cold-backup-bmc5server/logs/latest.log')
for archive in (R/'cold-backup-bmc5server/logs').glob('*.log.gz'):
 if archive.name.startswith('debug-'):continue
 previous,_,_,_=scan(archive);old.update(previous)
new,done,fatal,mods=scan(S/'logs/latest.log');added=[{'message':m,'count':n} for m,n in new.items() if m not in old]
report={'atUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'doneLines':done,'fatalLines':fatal,'baselineErrorKinds':len(old),'currentErrorKinds':len(new),'newExactErrorMessages':added,'moduleStartupLines':mods[-20:]};(R/'startup-log-review.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps({'done':done,'fatal':fatal,'baselineErrorKinds':len(old),'currentErrorKinds':len(new),'newExactErrorMessages':added[:12]},ensure_ascii=True))
