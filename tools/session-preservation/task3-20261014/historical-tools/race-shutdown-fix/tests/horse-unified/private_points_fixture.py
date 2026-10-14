"""Loopback-only private profiles with the unchanged actual PlatformStore implementation."""
from pathlib import Path
import hashlib,importlib.util,json,sqlite3,sys,threading,types,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

def load_backend(source):
    source=Path(source).resolve()
    package=types.ModuleType('horse_unified_backend')
    package.__path__=[str(source.parent)]
    sys.modules[package.__name__]=package
    for name in ('game_identity','player_platform'):
        spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,source.parent/(name+'.py'))
        module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module
        spec.loader.exec_module(module)
    return module

class Fixture:
 def __init__(self,source,lab):
  self.lab=lab;self.path=lab/'private-points.sqlite';self.key=uuid.uuid4().hex+uuid.uuid4().hex;self.requests=[];self.posts={10000:0,10001:0}
  with sqlite3.connect(self.path) as db:
   db.execute('CREATE TABLE player_profiles(subject TEXT PRIMARY KEY, uid INTEGER, points INTEGER, balance_cents INTEGER, updated_at TEXT)')
   for uid in self.posts:db.execute('INSERT INTO player_profiles VALUES(?,?,0,321,?)',(f'private-horse-{uid}',uid,''))
  self.store=load_backend(source).PlatformStore(self.path);self.source_sha=hashlib.sha256(source.read_bytes()).hexdigest();owner=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def respond(self,status,data):
    body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
   def do_GET(self):
    if self.headers.get('x-muxi-server-key')!=owner.key:return self.respond(403,{'error':'Private fixture only'})
    prefix='/api/internal/game/admission/'
    if not self.path.startswith(prefix) or self.path[len(prefix):] not in ('10000','10001'):return self.respond(403,{'error':'Private fixture only'})
    uid=int(self.path[len(prefix):])
    if (owner.lab/'coordinator/admission-outage.json').exists():
     owner.requests.append({'method':'GET','uid':uid,'status':503});return self.respond(503,{'error':'Private bounded outage'})
    with sqlite3.connect(owner.path) as db:points=db.execute('SELECT points FROM player_profiles WHERE uid=?',(uid,)).fetchone()[0]
    owner.requests.append({'method':'GET','uid':uid,'status':200,'points':points});self.respond(200,{'uid':uid,'allowed':True,'points':str(points)})
   def do_POST(self):
    if self.headers.get('x-muxi-server-key')!=owner.key or self.path!='/api/internal/game/results':return self.respond(403,{'error':'Private fixture only'})
    try:
     event=json.loads(self.rfile.read(int(self.headers['Content-Length'])));uid=event['uid']
     if uid not in owner.posts:raise ValueError('Private fixture UID required')
     receipt=owner.store.credit_game_result(event);owner.posts[uid]+=1;status=503 if owner.posts[uid]==1 else 200
     owner.requests.append({'method':'POST','event':event,'receipt':receipt,'status':status});self.respond(status,{'ok':status==200,**receipt})
    except Exception as error:self.respond(400,{'error':str(error)})
  self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start();self.url=f'http://127.0.0.1:{self.http.server_port}/api/internal/game/'
 def close(self):
  self.http.shutdown();self.http.server_close();self.thread.join()
  with sqlite3.connect(self.path) as db:
   profiles=db.execute('SELECT uid,points,balance_cents FROM player_profiles ORDER BY uid').fetchall();ledger=db.execute('SELECT uid,game,session,event,points FROM minigame_result_ledger ORDER BY uid').fetchall()
  success=len(ledger)==2 and all(1<=r[1]<=10 for r in profiles) and all(r[2]==321 for r in profiles) and all(self.posts[r[0]]>=2 and sum(e[0]==r[0] for e in ledger)==1 and next(e[-1] for e in ledger if e[0]==r[0])==r[1] for r in profiles)
  report={'success':success,'privateFixture':True,'productionWrites':False,'sourceSha256':self.source_sha,'profiles':profiles,'ledger':ledger,'posts':self.posts,'requests':self.requests,'replayExactlyOnce':success}
  (self.lab/'points-fixture.json').write_text(json.dumps(report,indent=2),encoding='utf-8');return report
