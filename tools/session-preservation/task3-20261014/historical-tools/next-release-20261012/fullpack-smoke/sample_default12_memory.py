from pathlib import Path
import psutil,json,time,datetime
root=Path(__file__).resolve().parent
lab=root.parents[1]/'q/build/local-mc-debug/r9-default12g'
out=root/'default12-memory-samples.jsonl'
deadline=time.monotonic()+1800
while time.monotonic()<deadline:
 try:owner=json.loads((lab/'local-mc-owner.json').read_text())
 except (FileNotFoundError,json.JSONDecodeError):time.sleep(2);continue
 row={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'availableMiB':psutil.virtual_memory().available//1048576,'roles':{}}
 for role,info in owner['processes'].items():
  try:
   p=psutil.Process(info['pid'])
   if abs(p.create_time()-info['processCreateTime'])>1:continue
   m=p.memory_info();row['roles'][role]={'pid':p.pid,'rssMiB':m.rss//1048576,'vmsMiB':m.vms//1048576}
  except (psutil.NoSuchProcess,psutil.AccessDenied):pass
 with out.open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
 if (lab/'run-result.json').exists():break
 time.sleep(5)
print('Memory sampler finished; only owned PIDs inspected.')
