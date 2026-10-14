"""Private loopback adapter around the real backend + review patch. Never production data."""
from pathlib import Path
import hashlib,importlib.util,json,sqlite3,sys,threading,types,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

def load_backend(source):
    source=Path(source).resolve()
    package=types.ModuleType('horse_fixture');package.__path__=[str(source.parent)];sys.modules['horse_fixture']=package
    spec=importlib.util.spec_from_file_location('horse_fixture.game_identity',source.parent/'game_identity.py')
    identity=importlib.util.module_from_spec(spec);sys.modules[spec.name]=identity;spec.loader.exec_module(identity)
    text=source.read_text(encoding='utf-8')
    replacements=[("game not in ('zombie-challenge', 'outbreak', 'flight')","game not in ('zombie-challenge', 'outbreak', 'flight', 'horse_racing')"),("'flight': 0}[game]","'flight': 0, 'horse_racing': 5}[game]"),("        policy = {} if rewards is None else rewards\n","        if game == 'horse_racing' and (not 1 <= event['difficulty'] <= 5 or not 1 <= event['score'] <= 10 or event['win'] != (event['score'] == 10)):\n            raise ValueError('Horse score must be 1..10, winner 10, difficulty 1..5')\n        policy = {} if rewards is None else rewards\n"),("        points = policy.get(game, {}).get(str(event['difficulty']), 0) if event['win'] else 0","        points = event['score'] if game == 'horse_racing' else (policy.get(game, {}).get(str(event['difficulty']), 0) if event['win'] else 0)")]
    if "'horse_racing': 5" not in text:
        for before,after in replacements:
            if text.count(before)!=1:raise ValueError('Shared backend changed: regenerate review patch before QA')
            text=text.replace(before,after)
    module=types.ModuleType('horse_fixture.player_platform');module.__package__='horse_fixture';module.__file__=str(source)
    exec(compile(text,str(source),'exec'),module.__dict__)
    return module

class PointsFixture:
    def __init__(self,source,lab):
        self.lab=Path(lab);self.path=self.lab/'private-points.sqlite';self.key=uuid.uuid4().hex+uuid.uuid4().hex;self.requests=[];self.posts=0
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE player_profiles(subject TEXT PRIMARY KEY, uid INTEGER, points INTEGER, balance_cents INTEGER, updated_at TEXT)')
            db.execute("INSERT INTO player_profiles VALUES('horse-fixture-only',10000,0,321,'')")
        self.store=load_backend(source).PlatformStore(self.path)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,status,data):
                body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_GET(self):
                if self.headers.get('x-muxi-server-key')!=owner.key or self.path!='/api/internal/game/admission/10000':return self.respond(403,{'error':'Private fixture only'})
                if (owner.lab/'admission-outage.json').exists():
                    owner.requests.append({'method':'GET','status':503,'privateFault':'admission outage'})
                    return self.respond(503,{'error':'Private admission outage'})
                with sqlite3.connect(owner.path) as db:points=db.execute('SELECT points FROM player_profiles WHERE uid=10000').fetchone()[0]
                owner.requests.append({'method':'GET','points':points});self.respond(200,{'uid':10000,'allowed':True,'points':str(points)})
            def do_POST(self):
                if self.headers.get('x-muxi-server-key')!=owner.key or self.path!='/api/internal/game/results':return self.respond(403,{'error':'Private fixture only'})
                try:
                    event=json.loads(self.rfile.read(int(self.headers['Content-Length'])));result=owner.store.credit_game_result(event)
                    owner.posts+=1;owner.requests.append({'method':'POST','event':event,'receipt':result})
                    # Commit then simulate lost acknowledgement. Existing native outbox must retry without duplicate credit.
                    self.respond(503 if owner.posts==1 else 200,{'ok':owner.posts>1,**result})
                except Exception as error:self.respond(400,{'ok':False,'error':str(error)})
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.http.server_port)+'/api/internal/game/'
        self.source_sha=hashlib.sha256(Path(source).read_bytes()).hexdigest()
    def close(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        with sqlite3.connect(self.path) as db:
            points,balance=db.execute('SELECT points,balance_cents FROM player_profiles WHERE uid=10000').fetchone();ledger=db.execute('SELECT uid,game,session,event,points FROM minigame_result_ledger').fetchall()
        report={'privateFixture':True,'productionWrites':False,'sourceSha256':self.source_sha,'uid':10000,'points':points,'balanceCents':balance,'ledger':ledger,'requests':self.requests,'posts':self.posts,'replayExactlyOnce':self.posts>=2 and len(ledger)==1 and points==ledger[0][-1]}
        (self.lab/'points-fixture.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report
