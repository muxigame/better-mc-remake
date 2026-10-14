from pathlib import Path
import argparse,datetime,hashlib,json,os,shutil,subprocess,sys,zipfile,time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'.ops-venv/Lib/site-packages'))
from trade_fixture import TradeFixture
ROOT=Path(__file__).resolve().parents[3];STAGE=ROOT/'trading-stage';SHARED=ROOT/'better-mc-remake/server/app'
SERVER=ROOT/'bmc5server';CLIENT=Path(os.environ.get('MUXI_CLIENT_GAME',str(ROOT/'_client_test/game')));JDK=Path(os.environ.get('JAVA_HOME',r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-<PRIVATE_HOST>+1'))
def main():
    parser=argparse.ArgumentParser(description='Private vanilla-horse commerce and real JVM-restart QA, never production data')
    parser.add_argument('--jar',type=Path);parser.add_argument('--output',type=Path,default=ROOT/'evidence');parser.add_argument('--backend-app',type=Path,default=SHARED);parser.add_argument('--commerce-app',type=Path);args=parser.parse_args()
    if args.jar:jar=args.jar.resolve();build=dict(jar=str(jar),sha256=hashlib.sha256(jar.read_bytes()).hexdigest())
    else:build=json.loads((ROOT/'trade-build-latest.json').read_text());jar=Path(build['jar'])
    assert hashlib.sha256(jar.read_bytes()).hexdigest()==build['sha256']
    test_root=Path(__file__).parent;own_app=args.commerce_app or args.backend_app
    if not args.commerce_app and (STAGE/'backend/app/horse_trading.py').exists():own_app=STAGE/'backend/app'
    args.output.mkdir(parents=True,exist_ok=True)
    lab=args.output/('horse-trade-native-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'));lab.mkdir();(lab/'mods').mkdir();(lab/'config').mkdir();(lab/'HORSE_QA_ONLY').write_text('Synthetic users/generated horses/private SQLite only\n')
    subprocess.run(['powershell','-NoProfile','-Command',"New-Item -ItemType Junction -Path '"+str(lab/'libraries')+"' -Target '"+str(SERVER/'libraries')+"' | Out-Null"],check=True)
    target=lab/'mods'/jar.name;shutil.copy2(jar,target)
    cp=os.pathsep.join(map(str,[target,CLIENT/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-client.jar',CLIENT/'libraries/net/minecraft/client/1.21.1-20240808.144430/client-1.21.1-20240808.144430-srg.jar',*list((SERVER/'libraries').rglob('*.jar'))]))
    classes=lab/'qa-classes';classes.mkdir();compile_args=['--release','21','-encoding','UTF-8','-proc:none','-cp',cp,'-d',str(classes),str(test_root/'HorseTradingQA.java')]
    (lab/'compile.args').write_text('\n'.join('"'+v.replace('\\','/')+'"' for v in compile_args));subprocess.run([str(JDK/'bin/javac.exe'),'@'+str(lab/'compile.args')],check=True)
    with zipfile.ZipFile(target,'a',zipfile.ZIP_DEFLATED) as z:
        for p in classes.rglob('*.class'):z.write(p,p.relative_to(classes).as_posix())
    for folder,pattern in [(SERVER/'mods','*touhoulittlemaid-1.5.3*'),(CLIENT/'mods','*ysm-2.6.5*')]:
        source=next(folder.glob(pattern));shutil.copy2(source,lab/'mods'/source.name)
    (lab/'config/fml.toml').write_text('versionCheck=false\ndisableConfigWatcher=true\nmaxThreads=2\n');(lab/'eula.txt').write_text('eula=true\n')
    (lab/'server.properties').write_text('server-ip=127.0.0.1\nserver-port=0\nonline-mode=false\nlevel-name=horse-trade-qa-world\nlevel-type=minecraft:flat\ngenerator-settings={"layers":[{"block":"minecraft:stone","height":64},{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains","structure_overrides":[]}\nspawn-protection=0\nview-distance=2\nsimulation-distance=2\nsync-chunk-writes=true\nmax-tick-time=60000\n')
    launch=lab/'launch.args';launch.write_text('-Xms512M\n-Xmx3G\n-XX:ActiveProcessorCount=2\n-Dfile.encoding=UTF-8\n-Dmuxi.horse.trading.enabled=true\n-Dmuxi.horse.tradeQa=true\n'+(SERVER/'libraries/net/neoforged/neoforge/21.1.250/win_args.txt').read_text()+'\n--nogui\n')
    fixture=TradeFixture(own_app,args.backend_app,lab)
    env={k:v for k,v in os.environ.items() if not k.startswith('MUXI_') and k not in ('JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS')};env.update(MUXI_GAME_PLATFORM_URL=fixture.url,MUXI_GAME_PLATFORM_KEY=fixture.key)
    results=[]
    try:
        for phase in ('initial','restart'):
            if phase=='restart':launch.write_text('-Dmuxi.horse.tradeResumeQa=true\n'+launch.read_text())
            with (lab/(phase+'-boot.log')).open('w',encoding='utf-8') as log:
                proc=subprocess.Popen([str(JDK/'bin/java.exe'),'@'+str(launch)],cwd=lab,env=env,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
                (lab/(phase+'-pid.json')).write_text(json.dumps(dict(pid=proc.pid,lab=str(lab))));print(json.dumps(dict(phase=phase,pid=proc.pid,lab=str(lab))),flush=True)
                try:code=proc.wait(timeout=900 if phase=='initial' else 240)
                except subprocess.TimeoutExpired:proc.stdin.write(b'stop\n');proc.stdin.flush();code=proc.wait(timeout=60)
            path=lab/'trade-result.json';r=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(success=False,error='Missing native report');r.update(exitCode=code,cleanExit=code==0)
            (lab/(phase+'-result.json')).write_text(json.dumps(r,indent=2));results.append(r);fixture.checkpoint()
            if not r.get('success') or code:break
            path.unlink()
    finally:backend=fixture.close()
    tables=backend['tables'];profile=next(v for v in tables['player_profiles'] if v['uid']==10000)
    trade_delta=sum(v['delta'] for v in tables['horse_trade_ledger'] if v['uid']==10000);game_delta=sum(v['points'] for v in tables['minigame_result_ledger'] if v['uid']==10000)
    ledger_ok=profile['points']==10000+trade_delta+game_delta and profile['balance_cents']==321 and game_delta==10
    summary=dict(success=len(results)==2 and all(v.get('success') and v['cleanExit'] for v in results) and ledger_ok,jarSha256=build['sha256'],actualJvmRestart=len(results)==2,isolatedFastAPIRouter=True,productionWrites=False,privateBalance=profile['points'],tradeDelta=trade_delta,gameDelta=game_delta,ledgerReconciles=ledger_ok,lostAcknowledgements=backend['lostAcknowledgements'],checks=sum(len(v.get('checks',[])) for v in results),results=results,lab=str(lab))
    (lab/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));(args.output/'horse-trade-native-latest.json').write_text(json.dumps(dict(lab=str(lab),success=summary['success'])))
    print(json.dumps({k:v for k,v in summary.items() if k!='results'}),flush=True)
    if not summary['success']:sys.exit(1)
if __name__=='__main__':main()
