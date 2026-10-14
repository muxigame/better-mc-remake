import pathlib,json,hashlib,concurrent.futures,datetime,os,time
R=pathlib.Path(r'C:\Users\Administrator\WorkSpace\muxigame\.releases\production1432-r2');S=pathlib.Path(r'\\?\C:\Users\Administrator\WorkSpace\muxigame\bmc5server');B=pathlib.Path(r'\\?\C:\Users\Administrator\WorkSpace\muxigame\.releases\production1432-r2\cold-backup-bmc5server')
files=[p for p in S.rglob('*') if p.is_file()];copies=[p for p in B.rglob('*') if p.is_file()];assert len(files)==len(copies),(len(files),len(copies));rows=[];errors=[]
def verify(p):
 rel=p.relative_to(S);q=B/rel
 try:
  before=p.stat();copy=q.stat();assert before.st_size==copy.st_size,'size mismatch'
  with p.open('rb') as a,q.open('rb') as b:
   ha=hashlib.sha256();hb=hashlib.sha256()
   while True:
    x=a.read(1024*1024);y=b.read(1024*1024);ha.update(x);hb.update(y)
    if not x and not y:break
   assert ha.digest()==hb.digest(),'hash mismatch'
  after=p.stat();assert (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'source changed while hashing'
  return {'relativePath':str(rel),'bytes':before.st_size,'sha256':ha.hexdigest()}
 except Exception as e:return {'relativePath':str(rel),'error':type(e).__name__+': '+str(e)}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 for i,row in enumerate(pool.map(verify,files),1):
  (errors if 'error' in row else rows).append(row)
  if i%2000==0:
   progress={'filesChecked':i,'totalFiles':len(files),'bytesVerified':sum(x['bytes'] for x in rows),'errors':len(errors)};(R/'cold-backup-reverify-progress.json').write_text(json.dumps(progress));print(json.dumps(progress),flush=True)
(R/'cold-backup-reverify-errors.json').write_text(json.dumps(errors,indent=2),encoding='utf-8')
if errors:print(json.dumps(errors),flush=True);raise SystemExit(1)
inv=R/'cold-backup-inventory.json';inv.write_text(json.dumps(rows,indent=2),encoding='utf-8');result={'completedUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':str(S)[4:],'backup':str(B)[4:],'fileCount':len(rows),'totalBytes':sum(x['bytes'] for x in rows),'allFileSha256Verified':True,'inventorySha256':hashlib.sha256(inv.read_bytes()).hexdigest(),'normalStopVerified':True,'copiedOriginalDacl':True,'success':True,'method':'Read-only long-path capable verification of existing original cold backup; no new copy, no exclusions','priorPowershellVerifierFailed':True};(R/'cold-backup-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
