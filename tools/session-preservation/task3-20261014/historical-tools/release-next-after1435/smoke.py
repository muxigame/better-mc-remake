from pathlib import Path
import json,sys,shutil,hashlib,subprocess,time,os,socket,psutil
N=Path(__file__).resolve().parent;W=N.parent
sys.path.insert(0,r'C:\Users\ranzh\workspace\dev\muxigame\better-mc-remake\scripts')
import local_mc_runtime as rt
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert not [p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower() in ['java.exe','javaw.exe','jcef_helper.exe']], 'MC window occupied'
assert psutil.virtual_memory().available>10*1024**3
port=38836
for kind in [socket.SOCK_STREAM,socket.SOCK_DGRAM]:
 with socket.socket(socket.AF_INET,kind) as s:s.bind(('127.0.0.1',port))
old=W/'q/build/local-mc-debug/five-zombie-20261012-131445/server'
lab=N/'startup-smoke';lab.mkdir();(lab/'mods').mkdir()
lock=json.loads((W/'next-release-20261012/zombie-fifth-candidate/FIVE-MODULE-PRODUCT-LOCK.json').read_text(encoding='utf-8'))
source=Path(r'C:\Users\ranzh\Documents\Codex\2026-10-09\task-4\outbreak-fix\build\libs\muxi-outbreak-0.4.5-school.4-candidate.11.jar')
assert h(source)=='bca8254f29f60bd8eaeca77f1360ff47378fc2be476809312e1e7679e8d418e8'
expected={}
for row in lock['sides']['server']:
 p=source if row['name'].startswith('muxi-outbreak-') else Path(row['path'])
 digest=h(p);assert p==source or digest==row['sha256']
 shutil.copy2(p,lab/'mods'/p.name);expected[p.name]=digest
assert len(expected)==350
for name in ['config','defaultconfigs','kubejs','tacz','dynamic-data-pack-cache']:
 if (old/name).is_dir():shutil.copytree(old/name,lab/name)
# Only our previously closed private world is used; no production world access.
shutil.copytree(old/'qa-world',lab/'qa-world')
props=(old/'server.properties').read_text(encoding='utf-8')
props='\n'.join(x for x in props.splitlines() if not x.startswith(('server-port=','server-ip=','enable-rcon=','level-name=')))
(lab/'server.properties').write_text(props+f'\nserver-port={port}\nserver-ip=127.0.0.1\nenable-rcon=false\nlevel-name=qa-world\n',encoding='utf-8')
(lab/'eula.txt').write_text('eula=true\n')
J=Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-<PRIVATE_HOST>+1')
rt.argfile(lab/'launch.args',rt.server_arguments(Path(r'C:\Users\ranzh\workspace\dev\muxigame\bmc5server'),'21.1.250',6144,Path(os.environ['TEMP'])))
result={'version':'1.4.36','productHashes':expected,'productionMutation':False,'fullGameplayTest':False,'port':port}
with (lab/'console.log').open('w',encoding='utf-8') as log:
 p=subprocess.Popen([str(J/'bin/java.exe'),'@launch.args'],cwd=lab,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
 result['pid']=p.pid;(N/'smoke-owner.json').write_text(json.dumps(result,indent=2));print('private server PID',p.pid,flush=True)
 try:
  until=time.time()+900
  while time.time()<until:
   assert p.poll() is None,'Private JVM exited before ready'
   text=(lab/'console.log').read_text(encoding='utf-8',errors='replace')
   if 'Done (' in text:break
   time.sleep(3)
  else:raise RuntimeError('startup timeout')
  p.stdin.write(b'list\ntime query gametime\n');p.stdin.flush();time.sleep(5)
  p.stdin.write(b'time query gametime\nhelp outbreak\n');p.stdin.flush();time.sleep(5)
  result['startupPassed']=True
 finally:
  if p.poll() is None:p.stdin.write(b'save-all flush\nstop\n');p.stdin.flush()
  result['exitCode']=p.wait(timeout=600)
assert result['exitCode']==0
assert {f.name:h(f) for f in (lab/'mods').glob('*.jar')}==expected
result['normalStop']=True
(N/'startup-smoke-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print('350-mod startup and normal exit passed',flush=True)
