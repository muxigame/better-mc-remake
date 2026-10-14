from pathlib import Path
import hashlib,json,time,uuid
import local_mc_debug as debug
import local_mc_runtime as rt

def run(lab,control,inputs):
    end=time.monotonic()+2700
    checks=[];events=[];ids={}
    def check(value,label):
        if not value:raise AssertionError(label)
        if label not in checks:checks.append(label)
    def wait_file(path,timeout=180):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            obj=rt.read_json(path)
            if obj:return obj
            result=rt.read_json(lab/'run-result.json')
            if result:raise RuntimeError('MC runner ended: '+str(result))
            time.sleep(.2)
        raise TimeoutError('Callback not received: '+str(path))
    def custom(role,kind,**data):
        ids[role]=ids.get(role,0)+1;number=ids[role]
        name='horse-server-command.json' if role=='server' else 'horse-client-'+role+'.json'
        result='horse-server-result-'+str(number)+'.json' if role=='server' else 'horse-client-'+role+'-result-'+str(number)+'.json'
        rt.write_json(lab/'coordinator'/name,{'id':number,'type':kind,**data})
        reply=wait_file(lab/'coordinator'/result,240)
        check(reply.get('ok'),str(reply.get('error')))
        events.append({'role':role,'type':kind,'result':reply});rt.write_json(control/'horse-callback-receipts.json',events)
        return reply
    def snapshot():return custom('server','observe')['players']
    def room(players,name='10000'):
        for game in players[name]['app']['games']:
            if game['id']=='horse_racing':
                return next((r for r in game['state']['rooms'] if r.get('mine')),None)
        raise AssertionError('Horse module not present')
    def wait(predicate,label,timeout=240):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=snapshot()
            if predicate(state):return state
            time.sleep(1)
        raise TimeoutError(label)
    def action(role,kind,value=''):
        reply=debug.command(lab,role,{'type':'game-action','game':'horse_racing','action':kind,'value':value},60)
        events.append({'role':role,'type':kind,'result':reply});time.sleep(1)
    def originals(state,names=('10000','10001')):
        for name in names:
            src=state[name]['source'];check(src['alive'] and src['mounted'] and not src['pendingReturn'] and src['copies']==1,'original UUID mounted and unique '+name)
            check(src['name']=='Private horse '+name and src['speed']==(.38 if name=='10000' else .28) and src['jump']==1,'original name and attributes unchanged '+name)
    while time.monotonic()<end:
        server=rt.read_json(lab/'coordinator/status-server.json')
        clients=[rt.read_json(lab/'coordinator'/('status-'+r+'.json')) or {} for r in ('host','guest')]
        views=[rt.read_json(lab/'coordinator'/('horse-status-'+r+'.json')) or {} for r in ('host','guest')]
        if server and server.get('players')==2 and all(c.get('connected') for c in clients) and all(v.get('screen')=='NONE' and v.get('overlay')=='NONE' for v in views):break
        if rt.read_json(lab/'run-result.json'):raise RuntimeError('Runner failed during startup')
        time.sleep(1)
    else:raise TimeoutError('Two real clients did not become ready')
    for role in ('host','guest'):
        reply=debug.command(lab,role,{'type':'observe'},60)
        check(reply['status']['connected'] and reply['status']['playersSeen']>=1 and not reply['status']['glfwFocused'] and not reply['status']['glfwVisible'],'hidden genuine client connection after login reload '+role)
    initial=custom('server','observe')['players']
    check(len(initial)==2 and all(p['network'] for p in initial.values()),'both fresh identities complete actual first login')
    rt.write_json(control/'first-login-app.json',initial)
    def clients_ready(dimension):
        for role in ('host','guest'):
            view=rt.read_json(lab/'coordinator'/('horse-status-'+role+'.json')) or {}
            pos=view.get('position',{})
            if not (view.get('connected') and view.get('alive') and view.get('dimension')==dimension and view.get('screen')=='NONE' and view.get('overlay')=='NONE' and abs(pos.get('y',-1000)-65)<.5):return False
        return True
    def transit_ready(state,dimension):
        return len(state)==2 and all(p.get('alive') and p.get('dimension')==dimension and p.get('entityTicking') and p.get('feetBlock')=='minecraft:stone' for p in state.values()) and clients_ready(dimension)
    custom('server','prepare-pad')
    custom('server','transit-home')
    wait(lambda s:transit_ready(s,'minecraft:overworld'),'Both clients acknowledge home transit on prepared ticking floor',180)
    custom('server','transit-source')
    wait(lambda s:transit_ready(s,'muxi_game_core:adventure'),'Both clients acknowledge adventure transit on prepared ticking floor',180)
    # A complete client tick interval precedes horse creation; never mount through two pending respawns.
    time.sleep(1)
    state=custom('server','mount-fixture')['players']
    check(len(state)==2 and all(p['network'] for p in state.values()),'distinct actual TCP clients; no fake players')
    originals(state)
    for role in ('host','guest'):
        visible_until=time.monotonic()+180
        while time.monotonic()<visible_until:
            view=debug.command(lab,role,{'type':'observe'},60)
            if view['status']['playersSeen']==2:
                check(True,'both real clients see the gathered participants '+role);break
            time.sleep(1)
        else:raise TimeoutError('Two participants were not visible after private fixture gathering '+role)
    def client_mounts_ready(state):
        for role,name in (('host','10000'),('guest','10001')):
            view=rt.read_json(lab/'coordinator'/('horse-status-'+role+'.json')) or {}
            if not view.get('alive') or view.get('vehicleUuid')!=state[name]['source'].get('uuid'):return False
        return True
    state=wait(client_mounts_ready,'Both real clients receive original horse and passenger tracking',180)
    originals(state)
    time.sleep(3)
    originals(snapshot())
    custom('server','release-pad') # Production HorseReturn must preserve assets during racing by itself.
    state=wait(lambda s:all(s[n]['app'].get('allowed') and s[n]['app']['platform']['available'] for n in ('10000','10001')),'Current server private account and connection admission')
    for role,name in (('host','10000'),('guest','10001')):
        expected=str(uuid.UUID(bytes=hashlib.md5(('OfflinePlayer:'+name).encode()).digest(),version=3))
        check(state[name]['verifiedUid']==int(name) and state[name]['uuid']==expected and state[name]['connectionAcceptingMessages'],'fixture UID bound to current real server connection '+role)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            view=custom(role,'observe');app=view['app']
            check(view['status'].get('uuid')==expected and view['status'].get('name')==name,'actual client profile matches its server-bound fixture UID '+role)
            if app.get('protocol')==2 and app.get('allowed') and app.get('actionSupported') and app.get('platform',{}).get('available'):
                rt.write_json(control/('client-admission-'+role+'.json'),view);break
            time.sleep(1)
        else:raise TimeoutError('Current real client account/connection admission not confirmed '+role)
    guestUuid=rt.read_json(lab/'coordinator/status-guest.json')['uuid']
    def focus_host():
        if not inputs.get('focusedHostAuthorized'):raise RuntimeError('Real terminal actions need a coordinated focused-host run; hidden mode never bypasses the gate')
        custom('host','show-for-terminal')
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if (rt.read_json(lab/'coordinator/horse-status-host.json') or {}).get('windowActive'):return
            time.sleep(.2)
        raise TimeoutError('GLFW did not establish actual host focus; no synthetic active flag is used')
    def cef_step(kind,label,timeout=240):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            reply=custom('host',kind)
            if not reply.get('status',{}).get('alive'):raise RuntimeError('Host died before terminal action: '+label)
            if not reply.get('status',{}).get('windowActive'):raise RuntimeError('Actual terminal action requires a coordinated real focused window: '+label)
            if reply.get('done'):
                check(True,label);return
            time.sleep(.5)
        raise TimeoutError(label)
    def create_and_start(actual_cef=False):
        if actual_cef:
            focus_host()
            cef_step('cef-create','Actual Chromium DOM selects horse map and creates waiting room')
            custom('host','screenshot',name='cef-waiting-room')
        else:action('host','createConfigured',json.dumps({'mode':'obstacle','map':'stadium-oval','difficulty':'5'}))
        state=wait(lambda s:room(s) is not None and room(s)['phase']=='WAITING','Host waiting room')
        originals(state);check(state['10000']['dimension']=='muxi_game_core:adventure','waiting keeps host in source dimension')
        action('host','invite',guestUuid)
        action('guest','join',room(state)['id'])
        state=wait(lambda s:room(s) is not None and room(s)['members']==2,'Invited actual guest joined')
        originals(state);check(room(state)['phase']=='WAITING','both participants wait before start')
        rt.write_json(control/'waiting-app.json',state)
        if actual_cef:
            cef_step('cef-start','Actual Chromium DOM starts invited race')
            custom('host','cef-dismiss')
            custom('host','hide-after-terminal')
        else:action('host','start')
        return wait(lambda s:room(s) is not None and room(s)['phase']=='RUNNING','Real stadium race starts',420)
    running=create_and_start(actual_cef=True)
    check(all(running[n]['dimension']=='muxi_minigames:horse_stadium_lab' for n in ('10000','10001')),'both real clients travel after start')
    for role in ('host','guest'):custom(role,'follow')
    time.sleep(3)
    for role in ('host','guest'):custom(role,'screenshot',name='race-ysm-horse')
    rt.write_json(control/'running-app.json',running)
    finished=wait(lambda s:room(s) is not None and room(s)['phase']=='FINISHED','Native horses finish and arena cleanup',240)
    race=room(finished);check(len(race['results'])==8,'two player horses and six NPCs have race results')
    for r in race['results']:
        check(r['finished'] and not r['dnf'] and r['recoveries']<=3,'all vanilla horses finish with bounded recovery')
        check(r['penaltyTicks']==80*r['recoveries'] and r['ticks']==r['rawTicks']+r['penaltyTicks'] and len(r['recoveryEvents'])==r['recoveries'],'recovery causes and exact penalty recorded')
        check((r['score']==10 and r['win']) if r['rank']==1 else (1<=r['score']<=9 and not r['win']),'first placing gets ten and every other placing positive points')
    originals(finished)
    returned=wait(client_mounts_ready,'Both actual clients receive returned original UUID mounts',180)
    originals(returned);rt.write_json(control/'finished-app.json',returned)
    settled=wait(lambda s:all(s[n]['app'].get('resultPending')==0 and s[n]['app'].get('lastResult',{}).get('game')=='horse_racing' and int(s[n]['app']['platform']['points'])>=1 for n in ('10000','10001')),'Two result outboxes acknowledged',180)
    for name in ('10000','10001'):
        expected=next(r['score'] for r in race['results'] if r['name']=='Private horse '+name)
        check(int(settled[name]['app']['platform']['points'])==expected,'existing shop points equal actual horse result '+name)
    for role in ('host','guest'):
        view=custom(role,'observe');check(view['status']['mountedYsmDraws']>100,'actual mounted YSM geometry branch rendered '+role)
        custom(role,'screenshot',name='returned-original')
    rt.write_json(control/'settled-app.json',settled)
    focus_host()
    cef_step('cef-results','Actual Chromium APP displays settled results')
    custom('host','screenshot',name='cef-settled-results')
    action('guest','leave');custom('host','cef-leave');time.sleep(1)
    wait(lambda s:all(s[n]['app']['activeGame']=='' for n in ('10000','10001')),'Membership released')
    custom('host','cef-close')
    custom('host','hide-after-terminal')
    # Second race checks a real socket loss while racing. Cancel host before a second settlement.
    create_and_start()
    custom('guest','disconnect')
    offline=wait(lambda s:'10001' not in s,'Guest actual socket disconnected',60)
    check(offline['10000']['app']['activeGame']=='horse_racing','guest disconnect leaves host room running')
    action('host','leave')
    custom('guest','reconnect')
    restored=wait(lambda s:'10001' in s and s['10001']['source'].get('mounted') and not s['10001']['source'].get('pendingReturn') and s['10001']['app']['activeGame']=='','Guest reconnect and original horse restoration',180)
    originals(restored,('10001',))
    final=wait(lambda s:all(s[n]['app']['activeGame']=='' for n in ('10000','10001')),'Safe host cancellation after guest disconnect',60)
    originals(final)
    final=wait(client_mounts_ready,'Both actual clients track unique original mounts after socket reconnect',180)
    originals(final)
    rt.write_json(control/'reconnected-app.json',final)
    rt.write_json(control/'scenario-result.json',{'success':True,'checks':checks,'race':race,'final':final,'nativeCallbacks':True,'physicalOSInput':False,'SSOAcceptance':False,'actualClientProcesses':2})
