from pathlib import Path
import sys,json,hashlib,psutil,os
R=Path('C:\\Users\\ranzh\\Documents\\Codex\\2026-10-09\\task-3');O=R/'race-native';P=Path(__file__).resolve().parent/'runtime-tools'
sys.path.insert(0,str(P/'scripts'));import local_mc_debug as qa
freeze=json.loads((O/'input-freeze.json').read_text(encoding='utf-8'));candidate=freeze['candidate'];label=sys.argv[1]
newplan=json.loads((R/'new-release/execution-plan.json').read_text(encoding='utf-8'))
for proc in psutil.process_iter(['pid','name','create_time','cmdline']):
 if proc.info['name'].lower() in ('java.exe','javaw.exe','jcef_helper.exe'):
  assert proc.pid==45600 and abs(proc.create_time()-1791687116.951152)<.01 and 'fullpack-entry-release1434-smoke2' in ' '.join(proc.cmdline()), 'Unexpected existing MC; inspect resource ownership'

original=qa.copy_template
release=json.loads((R/'release-entry3/execution-plan.json').read_text(encoding='utf-8'))
def copy(template,lab):
 original(template,lab)
 side='server' if template==R/'fullpack-frozen/server' else 'client'
 for row in release['items']:
  if row['side']==side and not row['path'].endswith('.jar'):
   import shutil
   src=R/'joint-final-entry3-stage'/(side+'-overlay')/row['path'];assert hashlib.sha256(src.read_bytes()).hexdigest()==row['sha256'];dst=lab/row['path'];dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
oldcopy=copy
def combined_copy(template,lab):
 oldcopy(template,lab)
 side='server' if template==R/'fullpack-frozen/server' else 'client'
 import shutil
 for row in newplan['items']:
  if row['side']==side and not row['path'].endswith('.jar'):
   src=R/'new-release'/(side+'-overlay')/row['path'];dst=lab/row['path'];dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
qa.copy_template=combined_copy
S=Path('C:/Users/ranzh/workspace/dev/muxigame');J=Path('C:/Users/ranzh/Documents/Codex/terminal-integration-task6-20261001/tools/jdk/jdk-<PRIVATE_HOST>+1')
args=['run','--project-root',str(P),'--instance-root',str(P/'build/local-mc-debug'/label),'--server-runtime',str(S/'bmc5server'),'--client-game',str(S/'_client_test/game'),'--java-home',str(J),'--unix-temp',str(Path(os.environ['TEMP'])/'mc34'),'--port','25844','--clients','1','--mode','hold','--hold-seconds','900','--accept-eula','--client-memory-mb','8192','--server-memory-mb','6144','--client-max-fps','15','--shutdown-timeout','180','--boot-timeout','600','--client-template',str(R/'fullpack-frozen/client'),'--server-template',str(R/'fullpack-frozen/server'),'--world',str(R/'fullpack-initialized-world'),'--server-view-distance','20','--server-simulation-distance','8','--client-render-distance','10','--client-simulation-distance','8','--diagnostic-logs','--jvm-property','local.debug.lightweightStatus=true','--jvm-property','muxi.materialMapPresize=true']
for side in ['server','client']:
 replacements=[x for x in newplan['items'] if x['side']==side and x['path'].endswith('.jar')]
 retired={Path(x['path']).name for x in newplan['remove']}
 for row in freeze['baseline'+side.capitalize()]:
  if row['name'] in retired:continue
  assert hashlib.sha256(Path(row['source']).read_bytes()).hexdigest()==row['sha256'];args+=['--'+side+'-mod',row['source']]
 for row in replacements:
  source=R/'new-release'/(side+'-overlay')/row['path'];assert hashlib.sha256(source.read_bytes()).hexdigest()==row['sha256'];
  if 'muxi-horse-racing-' in source.name:source=Path(__file__).resolve().parent/'race-shutdown-fix/build/libs/muxi-horse-racing-0.2.2-race.5.jar'
  args+=['--'+side+'-mod',str(source)]
raise SystemExit(qa.main(args))
