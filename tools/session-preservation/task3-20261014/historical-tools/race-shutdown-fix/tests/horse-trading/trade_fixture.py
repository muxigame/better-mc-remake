"""Real FastAPI router + existing service guard, private loopback DB, loss injection."""
from pathlib import Path
import ast,hashlib,hmac,importlib.util,json,os,sqlite3,socket,sys,threading,time,types,uuid

def load_trade(own,shared):
    pkg=types.ModuleType('horse_trade_fixture');pkg.__path__=[str(own),str(shared)];sys.modules[pkg.__name__]=pkg
    for name,path in [('game_identity',shared/'game_identity.py'),('player_platform',shared/'player_platform.py'),('horse_trading',own/'horse_trading.py')]:
        spec=importlib.util.spec_from_file_location(pkg.__name__+'.'+name,path);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module

class TradeFixture:
    def __init__(self,own,shared,lab):
        from fastapi import FastAPI,Depends,HTTPException,Request
        import uvicorn
        self.lab=Path(lab);self.path=self.lab/'private-trades.sqlite';self.key=uuid.uuid4().hex+uuid.uuid4().hex;self.calls=[];self.loss=set();self.reject_sales=0
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE player_profiles(subject TEXT PRIMARY KEY,uid INTEGER,points INTEGER,balance_cents INTEGER,updated_at TEXT)')
            db.executemany("INSERT INTO player_profiles VALUES(?,?,10000,321,'')",[(str(uid),uid) for uid in (10000,10001,10002)])
        os.environ.update(BMC_GAME_PLATFORM_ENABLED='1',BMC_GAME_SERVICE_KEY=self.key,BMC_HORSE_TRADING_ENABLED='1')
        self.trade=load_trade(Path(own),Path(shared));self.store=self.trade.HorseTradeStore(self.path)
        self.shared_main=os.getenv('HORSE_TRADE_QA_USE_SHARED_MAIN')=='1'
        if self.shared_main:
            # Import the final shared entry unchanged, including its real router mount.
            # All module-level stores are redirected to this private SQLite before import.
            os.environ.update(BMC_SKIP_DOTENV='1',BMC_DATABASE_PATH=str(self.path),BMC_SERVE_WEB='0')
            spec=importlib.util.spec_from_file_location('horse_trade_fixture.main',Path(shared)/'main.py')
            main=importlib.util.module_from_spec(spec);sys.modules[spec.name]=main;spec.loader.exec_module(main)
            app=main.app;auth=main.platform_service_key
            assert {r.path for r in app.routes}>={'/api/internal/game/horse-trades','/api/internal/game/horse-market/{uid}'},'Shared owner router mount not present'
            assert main.database_path.resolve()==self.path.resolve()
        else:
            app=FastAPI();source=(Path(shared)/'main.py').read_text(encoding='utf-8');parsed=ast.parse(source)
            guard=next(n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name=='platform_service_key')
            scope=dict(os=os,hmac=hmac,Request=Request,HTTPException=HTTPException)
            exec(compile(ast.Module(body=[guard],type_ignores=[]),str(Path(shared)/'main.py'),'exec'),scope);auth=scope['platform_service_key']
            app.include_router(self.trade.horse_trading_router(self.path,auth))
        owner=self
        @app.middleware('http')
        async def loss(request,call_next):
            event=None
            if request.url.path.endswith('/horse-trades'):
                raw=await request.body()
                if not raw:
                    (owner.lab/'empty-request.json').write_text(json.dumps(dict(method=request.method,headers={k:v for k,v in request.headers.items() if k!='x-muxi-server-key'})))
                try:event=json.loads(raw)
                except ValueError:event=None
                flag=owner.lab/'reject-next-sale.flag'
                if event and event['action']=='sale_prepare' and (owner.reject_sales or flag.exists()):
                    owner.reject_sales=max(0,owner.reject_sales-1)
                    if flag.exists():flag.unlink()
                    from starlette.responses import JSONResponse
                    return JSONResponse({'detail':'Injected pre-commit custody rejection'},status_code=409)
            response=await call_next(request)
            if event:
                owner.calls.append(dict(event=event,status=response.status_code))
                if response.status_code==200 and event['action'] in ('buy','sale_commit','rent','grant_ack','rental_end') and event['operation'] not in owner.loss:
                    owner.loss.add(event['operation'])
                    from starlette.responses import JSONResponse
                    return JSONResponse({'detail':'Injected lost acknowledgement AFTER durable commit'},status_code=503)
            return response
        if not self.shared_main:
            @app.get('/api/internal/game/admission/{uid}',dependencies=[Depends(auth)])
            def admission(uid:int):return dict(uid=uid,allowed=not owner.store.platform.banned(uid),points=owner.store.platform.point_balance(uid))
            @app.post('/api/internal/game/results',dependencies=[Depends(auth)])
            async def result(request:Request):return dict(ok=True,**owner.store.platform.credit_game_result(await request.json()))
        self.app=app;self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen(64)
        self.http=uvicorn.Server(uvicorn.Config(app,log_level='error',http='h11'));self.thread=threading.Thread(target=self.http.run,kwargs={'sockets':[self.sock]},daemon=True);self.thread.start()
        deadline=time.monotonic()+10
        while not self.http.started and time.monotonic()<deadline:time.sleep(.05)
        if not self.http.started:raise RuntimeError('Private FastAPI startup failed')
        self.url='http://127.0.0.1:'+str(self.sock.getsockname()[1])+'/api/internal/game/'
        self.inputs={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(shared)/'main.py',Path(shared)/'player_platform.py',Path(own)/'horse_trading.py',Path(own)/'horse_trade_catalog_v1.json']}
    def checkpoint(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            rows={name:[dict(r) for r in db.execute('SELECT * FROM '+name)] for name in ('player_profiles','horse_trade_assets','horse_trade_rentals','horse_trade_sales','horse_trade_ledger','minigame_result_ledger')}
        result=dict(privateFixture=True,productionWrites=False,sharedMainEntry=self.shared_main,sourceSha256=self.inputs,calls=self.calls,lostAcknowledgements=len(self.loss),tables=rows)
        (self.lab/'trade-fixture.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
    def close(self):self.http.should_exit=True;self.thread.join(timeout=15);return self.checkpoint()
