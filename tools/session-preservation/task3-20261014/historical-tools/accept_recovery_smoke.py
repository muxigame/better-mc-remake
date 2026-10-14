from pathlib import Path
import sys,json,time,traceback,uuid,hashlib
R=Path('C:\\Users\\ranzh\\Documents\\Codex\\2026-10-09\\task-3');O=Path(__file__).resolve().parent;P=O/'runtime-tools';lab=P/'build/local-mc-debug'/sys.argv[1]
sys.path.insert(0,str(P/'scripts'));sys.path.insert(0,str(R));import local_mc_debug as qa
from tick_action_driver import perform
receipt={'label':sys.argv[1],'completed':False,'checks':[],'stages':[],'events':[],'productionChanged':False,'raceActorTeleported':False};deadline=time.monotonic()+1500
out=O/(sys.argv[1]+'-acceptance-retry.json');out.parent.mkdir(exist_ok=True)
def save():out.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def check(name,value,detail=None):
 receipt['checks'].append({'name':name,'passed':bool(value),'detail':detail,'utc':time.time()});save()
 if not value:raise AssertionError(name)
def stage(name):receipt['stages'].append({'name':name,'utc':time.time()});save();print(name,flush=True)
def call(role,**body):
 if time.monotonic()>deadline:raise TimeoutError('overall matrix deadline')
 row=qa.command(lab,role,body,60);receipt['events'].append({'role':role,'body':body,'receipt':row,'utc':time.time()});save();return row
def action(action,value='',expect_failed=False):
 result=perform(lambda:call('server',type='game-write-state',player='DebugHost')['writeState'],lambda request:call('host',type='game-action',game='horse_racing',action=action,value=value,request=request),expect_failed=expect_failed,timeout=90)
 receipt['events'].append({'action':action,'receipt':result});save();return result
def snap():
 s=call('server',type='game-snapshot',player='DebugHost',game='horse_racing')['snapshot']
 return next(g['state'] for g in s['games'] if g['id']=='horse_racing')
def wait(fn,seconds,label):
 end=min(deadline,time.monotonic()+seconds)
 while time.monotonic()<end:
  result=fn()
  if result:return result
  time.sleep(3)
 raise TimeoutError(label)
def phase(target,seconds=300):
 def poll():
  s=snap();room=s['rooms'][0] if s['rooms'] else None
  if room and room['phase'] in ['BUILD_FAILED','START_FAILED','RACE_FAILED']:raise AssertionError('Native phase '+room['phase']+': '+room.get('startIssue',''))
  return room if room and room['phase']==target else None
 return wait(poll,seconds,'phase '+target)
def native():return call('server',type='horse-native')['horseNative']
def screenshot(label):
 result=call('host',type='screenshot');receipt['events'].append({'screenshotLabel':label,'result':result});save()
def execute(text):return call('server',type='execute',command=text)
def cleaned():
 s=snap();return s if not s['rooms'] and s['arenaPool']['held']==0 else None
try:
 stage('merged1.4.34 native connection')
 wait(lambda:(json.loads((lab/'coordinator/status-host.json').read_text(encoding='utf-8')).get('connected') if (lab/'coordinator/status-host.json').exists() else False),600,'client connection')
 wait(lambda:call('host',type='observe')['status'].get('screen')=='NONE',180,'world render ready')
 previous=json.loads((O/'release1434-shutdownfix-smoke1-acceptance.json').read_text(encoding='utf-8'))
 prior=[e['receipt']['status'] for e in previous['events'] if e.get('body',{}).get('type')=='game-action' and e.get('body',{}).get('action')=='createConfigured' and 'status' in e.get('receipt',{})][-1]
 restored=call('server',type='observe')['status']['byName']['DebugHost']
 check('durable return marker cleared after restart',not restored['horseReturnPending'])
 check('original game mode recovered',restored['mode']=='creative')
 check('original dimension recovered',restored['dimension']==prior['dimension'])
 check('original position recovered',sum((restored[k]-prior[k])**2 for k in ['x','y','z'])<.1)
 execute('time set day');execute('weather clear');execute('gamemode creative DebugHost');call('host',type='dismiss-dialog')
 wait(lambda: (lambda st:st.get('ysmRemaining')==0 and st.get('ysmLoaded',0)>0 and all('ground_'+k+'_v1' in st.get('ysmKeys','') for k in ['suzuka','oguri','special','gold','teio','mcqueen']))(call('host',type='observe')['status']),300,'YSM actual loaded map ready')
 for key in ['suzuka','oguri','special','gold','teio','mcqueen']:
  stage('new player YSM '+key)
  execute('ysm model set DebugHost "11_umamusume_player/ground_'+key+'_v1" skin')
  time.sleep(3)
  wait(lambda:call('host',type='observe')['status'].get('screen')=='NONE',180,'model frame world ready')
  log=(lab/'server/logs/latest.log').read_text(encoding='utf-8',errors='replace')
  check('server selects new model '+key, "Set '11_umamusume_player/ground_"+key+"_v1' models for player 'DebugHost'" in log)
  call('host',type='camera',mode='THIRD_PERSON_FRONT');screenshot('new player model '+key+'; native third-person assisted frame')
 stage('restarted shutdown recovery');state=call('host',type='observe')['status'];check('recovered outside horse dimension','horse' not in state.get('dimension',''));check('no room remains after restart',not snap()['rooms']);check('no simulation tickets after restart',not native()['simulationTickets'])
 receipt['completed']=True;receipt['passed']=True;save()
except BaseException as e:
 receipt['passed']=False;receipt['failure']=str(e);receipt['traceback']=traceback.format_exc();save();print(traceback.format_exc(),flush=True)
finally:
 if (lab/'local-mc-owner.json').exists():qa.stop_files(lab,json.loads((lab/'local-mc-owner.json').read_text(encoding='utf-8')))
 print(json.dumps({'completed':receipt['completed'],'passed':receipt.get('passed'),'checks':len(receipt['checks']),'failure':receipt.get('failure')},ensure_ascii=False),flush=True)
