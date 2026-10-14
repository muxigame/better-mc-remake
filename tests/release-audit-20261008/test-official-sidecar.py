import json,pathlib,subprocess,threading,queue,time
O=pathlib.Path(__file__).resolve().parent;root=O.parent/'launcher128-local-pack-test'
r={'baseUrl':'https://mc.muxigame.com'};p=root/'launcher/settings.json';s=json.loads(p.read_bytes());s['UpdateBaseUrl']=r['baseUrl'];p.write_text(json.dumps(s,indent=2),encoding='utf-8')
assert not (root/'launcher/account.bin').exists()
exe=next(root.glob('*backend*.exe'))
proc=subprocess.Popen([str(exe)],cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace')
q=queue.Queue()
def read():
 for line in proc.stdout:
  try:q.put(json.loads(line))
  except ValueError:pass
threading.Thread(target=read,daemon=True).start()
responses=[]
try:
 for i,method in [(1,'init'),(2,'install')]:
  proc.stdin.write(json.dumps({'id':i,'method':method,'params':{}})+'\n');proc.stdin.flush();deadline=time.monotonic()+40
  while time.monotonic()<deadline:
   try:msg=q.get(timeout=1)
   except queue.Empty:continue
   if msg.get('id')==i:responses.append(msg);break
  else:raise RuntimeError('Sidecar response timeout')
finally:
 proc.stdin.close();proc.wait(timeout=15)
# Isolated profile has no credentials; retain only requested response state.
(O/'official-sidecar-postpublish.json').write_text(json.dumps({'binary':exe.name,'candidateBase':r['baseUrl'],'responses':responses,'mcStarted':False,'existingAccountAccessed':False},indent=2),encoding='utf-8')
print(json.dumps({'launcher':responses[0].get('result',{}).get('launcherVersion'),'source':responses[0].get('result',{}).get('updateServer'),'pack':responses[0].get('result',{}).get('pack',{}).get('version'),'installResult':responses[1]},ensure_ascii=True))
