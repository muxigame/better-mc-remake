from pathlib import Path
import sys,json,time,traceback
ROOT=Path(__file__).resolve().parent
LAB=ROOT.parents[1]/'q/build/local-mc-debug/r7'
sys.path.insert(0,str(ROOT/'qa-project/scripts'))
sys.path.insert(0,'C:/Users/ranzh/Documents/Codex/2026-10-09/task-3')
import local_mc_debug as qa
from tick_action_driver import perform
OUT=ROOT/'combined-acceptance.json'
receipt=json.loads(OUT.read_text()) if OUT.exists() else dict(checks=[],events=[],productionMutation=False,physicalOSInput=False,formalSSO=False)
def save():OUT.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
def check(name,passed,detail=None):
 receipt['checks'].append(dict(name=name,passed=bool(passed),detail=detail));save();print(name,passed,flush=True)
 if not passed:raise AssertionError(name)
def call(role,**body):
 r=qa.command(LAB,role,body,90);receipt['events'].append(dict(role=role,body=body,result=r));save();return r
def wait(fn,timeout=90):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  r=fn()
  if r:return r
  time.sleep(1)
 raise TimeoutError(str(fn))
def action(game,action,value=''):
 return perform(lambda:call('server',type='game-write-state',player='10000')['writeState'],lambda request:call('host',type='game-action',game=game,action=action,value=value,request=request),timeout=90)
def state(game):return next(g['state'] for g in call('server',type='game-snapshot',player='10000')['snapshot']['games'] if g['id']==game)
def filecmd(folder,role,body,result_prefix):
 d=LAB/folder;d.mkdir(exist_ok=True);p=d/(role+'.json');n=int(time.time()*1000)%1000000000
 body.update(id=n);p.write_text(json.dumps(body),encoding='utf-8');out=d/(result_prefix+str(n)+'.json')
 wait(out.exists);r=json.loads(out.read_text(encoding='utf-8-sig'));receipt['events'].append(dict(file=str(p),body=body,result=r));save()
 if not r.get('ok'):raise RuntimeError(r)
 return r
def ui(kind,**kw):return filecmd('tower-ui','host',dict(type=kind,**kw),'host-')
def towerworld(**kw):return filecmd('tower-world','server',kw,'server-')
def teleport(role,action,**kw):
 run=json.loads((LAB/'local-mc-owner.json').read_text())['runId']
 return filecmd('coordinator','teleport-'+role,dict(action=action,runId=run,**kw),'teleport-result-'+role+'-')['state']
def uiwarp(uid,dimension,x):
 end=time.monotonic()+60
 teleport('host','browse',dimension=dimension)
 while time.monotonic()<end:
  s=teleport('server','observe')
  if s['dimension']==dimension and abs(s['x']-x)<2:return s
  c=teleport('host','observe')
  if c.get('pending')=='null' and c.get('near') and any(e['uid']==uid and e['affordable'] and not e['denial'] for e in (c.get('details') or {}).get('entries',[])):
   try:teleport('host','ui-warp',uid=uid)
   except RuntimeError as e:
    if 'entry absent' not in str(e):raise
  else:teleport('host','poll')
  time.sleep(1)
 raise TimeoutError('Native UI warp did not arrive')
def tower():
 call('host',type='dismiss-dialog');call('server',type='execute',command='gamemode creative 10000')
 before=towerworld()['players']['10000'];action('tower_defense','roomCreate',json.dumps(['Combined pack',['solo','brook','1']]))
 check('tower lobby created',any(r['mine'] and r['phase']=='WAITING' for r in state('tower_defense')['rooms']))
 action('tower_defense','start');wait(lambda:ui('observe')['screen']=='TowerScreen')
 c=state('tower_defense')['combat'];ox=c['originX'];ui('key',key=50);ui('world-click',x=4,y=3);ui('key',key=32)
 wait(lambda:'guard' in state('tower_defense')['combat']['battle']['units'])
 check('tower native world selection deploys guard',True);ui('capture',name='combined-tower-deployment')
 observed=towerworld(originX=ox);check('tower actual maid exists',any(e['type']=='EntityMaid' for e in observed['entities']),observed)
 action('tower_defense','leave');wait(lambda:not state('tower_defense')['rooms']);after=towerworld(originX=ox)
 check('tower room blocks entities tickets clear',not after['entities'] and after['forcedChunks']==0 and after['boardBlocks']==0,after)
 check('tower exit restores player',all(before[k]==after['players']['10000'][k] for k in ('dimension','mode','inventory','flying')),dict(before=before,after=after['players']['10000']))
 check('tower HUD restored',not ui('observe')['hideGui']);ui('close')
def maps():
 call('server',type='execute',command='gamemode survival 10000')
 fixture=LAB/'server/config/muxi-game-core/teleport-network.json';fixture.parent.mkdir(exist_ok=True);fixture.write_text(json.dumps(dict(version=1,facilities=[])))
 teleport('server','setup');teleport('server','fee',expression='add_xp_cost(9)');teleport('server','xp',points=0)
 teleport('host','open');time.sleep(3);teleport('host','poll');time.sleep(1)
 s=teleport('server','observe');uid=next(x['uuid'] for x in s['stones'] if x['name']=='Shared')
 c=teleport('host','observe');entry=next(x for x in c['details']['entries'] if x['uid']==uid)
 check('map native fee displayed and unaffordable',not entry['affordable'] and '9' in entry['cost'],entry)
 teleport('host','warp',uid=uid);time.sleep(2);after=teleport('server','observe')
 check('map insufficient fee prevents movement',abs(after['x']-.5)<1 and after['xpTotal']==0,after)
 teleport('server','xp',points=100);teleport('host','poll');after=uiwarp(uid,'minecraft:overworld',1002.5)
 check('map native UI warp arrives and charges',abs(after['x']-1002)<3 and after['xpTotal']==91,after)
 s=teleport('server','cross-setup');cross=next(x['uuid'] for x in s['stones'] if x['name']=='CrossDimension');linked=next(x['uuid'] for x in s['stones'] if x['name']=='Linked')
 teleport('server','frame');teleport('server','xp',points=100);teleport('host','open');teleport('host','poll');time.sleep(2)
 before=teleport('server','observe');teleport('host','warp',uid=cross);time.sleep(2);after=teleport('server','observe')
 check('map unregistered gateway rejects cross dimension',after['dimension']==before['dimension'] and after['xpTotal']==before['xpTotal'],after)
 fixture=LAB/'server/config/muxi-game-core/teleport-network.json';fixture.parent.mkdir(exist_ok=True)
 fixture.write_text(json.dumps(dict(version=1,facilities=[dict(dimension='minecraft:overworld',portalPos=[1006,101,2],waystoneUid=linked)])))
 teleport('host','poll');c=wait(lambda:(lambda c:c if c['details']['crossDimensionReady'] else None)(teleport('host','observe')),30);check('map private registered physical gateway authorized',c['details']['crossDimensionReady'],c)
 after=uiwarp(cross,'muxi_game_core:overworld',20.5)
 check('map actual cross dimension warp charges exactly once',abs(after['x']-20.5)<1 and after['xpTotal']==91,after)
 teleport('server','move',dimension='minecraft:overworld',x=.5,y=101,z=.5);time.sleep(2)
 teleport('host','open');teleport('host','save');teleport('server','move',dimension='minecraft:the_nether',x=.5,y=101,z=.5);time.sleep(3);teleport('host','poll');time.sleep(1);c=teleport('host','observe')
 check('map registered game dimension remains usable',c['dimension']=='minecraft:the_nether' and 'minecraft:the_nether' in c['dropdown'],c)
 prior=c['query'];c=teleport('host','stale');check('map stale home snapshot rejected',c['query']==prior,c)
 teleport('host','save')
 for dim in ['minecraft:overworld','muxi_game_core:overworld','minecraft:overworld']:teleport('server','move',dimension=dim,x=.5,y=101,z=.5)
 time.sleep(3);teleport('host','poll');time.sleep(1);c=teleport('host','observe')
 check('map game dimension hidden after exit',c['dimension']=='minecraft:overworld' and 'minecraft:the_nether' not in c['dropdown'],c)
 prior=c['query'];c=teleport('host','stale');check('map stale game snapshot rejected after exit',c['query']==prior,c);teleport('host','close')
def shop():
 (LAB/'begin-shop').write_text('explicit combined smoke gate')
 wait(lambda:(LAB/'shop-client-result.json').exists(),650)
 r=json.loads((LAB/'shop-client-result.json').read_text());check('shop browser suite',r.get('success',False),r)
def race():
 ui('close');call('server',type='execute',command='gamemode creative 10000')
 prior=call('server',type='observe')['status']['byName']['10000'];receipt['horseReturnExpected']=prior;save()
 config=json.dumps(dict(mode='obstacle',map='stadium-oval',difficulty='3',skills='on',laps='3'))
 action('horse_racing','createConfigured',config);action('horse_racing','start')
 def room():
  rooms=state('horse_racing')['rooms'];return rooms[0] if rooms else {}
 r=wait(lambda:(lambda r:r if r.get('phase')=='COUNTDOWN' else None)(room()),300)
 check('race legacy entry assembles eight racers',len(r['results'])==8,r)
 n=call('server',type='horse-native')['horseNative'];maids=[a for a in n['actors'] if a['type']=='touhou_little_maid:maid' and not a['removed']]
 check('race old riders remain mounted',len(maids)==8 and all(a['vehicle'] for a in maids),maids)
 wait(lambda:room().get('phase')=='RUNNING',90)
 # Move only the spectator. Native race actors are never moved or driven by QA.
 call('server',type='execute',command='execute in muxi_minigames:horse_stadium_lab run tp 10000 5000 130 5000')
 n1=call('server',type='horse-native')['horseNative'];time.sleep(15);n2=call('server',type='horse-native')['horseNative'];before={a['uuid']:a for a in n1['actors']}
 horses=[a for a in n2['actors'] if a['type']=='minecraft:horse' and not a['removed'] and a['leaseAllowed']]
 check('race distant actors keep native ticks and motion',len(horses)==8 and all(a['minPlayerHorizontalDistance']>100 and a['nativePostTicks']>before[a['uuid']]['nativePostTicks']+100 and sum((a[k]-before[a['uuid']][k])**2 for k in ['x','z'])>1 for a in horses),dict(before=n1,after=n2))
 action('horse_racing','leave');wait(lambda:not state('horse_racing')['rooms'])
 n=call('server',type='horse-native')['horseNative'];check('race leave clears simulation tickets',not n['simulationTickets'],n)
 prior=call('server',type='observe')['status']['byName']['10000'];receipt['horseReturnExpected']=prior;save()
 action('horse_racing','createConfigured',config);action('horse_racing','start');wait(lambda:room().get('phase')=='COUNTDOWN',300)
 check('race active room ready for normal shutdown',True)
 qa.stop_files(LAB,json.loads((LAB/'local-mc-owner.json').read_text()))
def recovery():
 global LAB
 LAB=ROOT.parents[1]/'q/build/local-mc-debug/r8'
 wait(lambda:(LAB/'coordinator/status-host.json').exists() and json.loads((LAB/'coordinator/status-host.json').read_text()).get('connected'),650)
 after=call('server',type='observe')['status']['byName']['10000'];before=receipt['horseReturnExpected']
 check('race restart durable return marker cleared',not after['horseReturnPending'],after)
 check('race restart restores original position and mode',after['dimension']==before['dimension'] and after['mode']==before['mode'] and sum((after[k]-before[k])**2 for k in ['x','y','z'])<.1,dict(before=before,after=after))
 check('race restart has no stale room',not state('horse_racing')['rooms'])
 check('race restart has no simulation tickets',not call('server',type='horse-native')['horseNative']['simulationTickets'])
 qa.stop_files(LAB,json.loads((LAB/'local-mc-owner.json').read_text()))
if __name__=='__main__':
 try:
  globals()[sys.argv[1]]()
 except BaseException as e:
  receipt['lastFailure']=dict(stage=sys.argv[1],error=str(e),traceback=traceback.format_exc());save();raise
