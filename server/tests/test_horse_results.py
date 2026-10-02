"""Actual loopback HTTP API and SQLite acceptance, using disposable synthetic data."""
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

SERVER=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVER))
TEMP=tempfile.TemporaryDirectory(prefix='horse-results-qa-')
DB=Path(TEMP.name)/'synthetic.db'
KEY='synthetic-horse-results-service-only-00000000'
os.environ.update(BMC_SKIP_DOTENV='1',BMC_SERVE_WEB='0',BMC_DATABASE_PATH=str(DB),
                  BMC_GAME_SERVICE_KEY=KEY,BMC_GAME_PLATFORM_ENABLED='1',
                  BMC_MINIGAME_REWARDS_JSON='{}',BMC_AUTH_CLIENT_SECRET='',
                  BMC_GAME_OP_SYNC_ENABLED='0',BMC_TERMINAL_SSO_ENABLED='0')
from app import main
from app.oidc import WebsiteAccount
import uvicorn

REQUESTS=[]
@contextmanager
def fixture_connect(path):
    db=sqlite3.connect(path)
    try:
        with db:yield db
    finally:db.close()

class HorseResults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.socket=socket.socket();cls.socket.bind(('127.0.0.1',0))
        cls.port=cls.socket.getsockname()[1];cls.base=f'http://127.0.0.1:{cls.port}'
        cls.server=uvicorn.Server(uvicorn.Config(main.app,log_level='critical',access_log=False))
        cls.thread=threading.Thread(target=lambda:cls.server.run(sockets=[cls.socket]),daemon=True)
        cls.thread.start()
        deadline=time.monotonic()+15
        while not cls.server.started and cls.thread.is_alive() and time.monotonic()<deadline:time.sleep(.02)
        if not cls.server.started:raise RuntimeError('Own loopback API failed to start')
    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit=True;cls.thread.join(15)
        if cls.thread.is_alive():raise RuntimeError('Own loopback API did not exit')
        cls.socket.close()
    def setUp(self):
        with fixture_connect(DB) as db:
            db.execute('DELETE FROM minigame_result_ledger')
        self.cookies={}
        for uid in (10000,10001,9999999999999999):
            account=WebsiteAccount(subject=f'horse-qa-{uid}',uid=uid,username=f'qa-{uid}',nickname='Horse QA',game_name=str(uid),email=None,email_verified=False,role='player')
            main.web_auth_store.player_profile(account)
            self.cookies[uid]=main.web_auth_store.create_session(account)
        with fixture_connect(DB) as db:db.execute('UPDATE player_profiles SET points=7,balance_cents=321')
        self.event={'uid':10000,'game':'horse_racing','session':str(uuid4()),'win':True,'score':10,'difficulty':1,'seconds':60}
    def request(self,event=None,key=KEY,cookie=None,raw=None,path='/api/internal/game/results'):
        headers={'Content-Type':'application/json'}
        if key is not None:headers['x-muxi-server-key']=key
        if cookie is not None:headers['Cookie']='bmc_session='+cookie
        data=(raw if raw is not None else json.dumps(event).encode()) if event is not None or raw is not None else None
        request=urllib.request.Request(self.base+path,data=data,headers=headers)
        try:response=urllib.request.urlopen(request,timeout=15)
        except urllib.error.HTTPError as error:response=error
        with response:
            body=response.read();status=response.status
        REQUESTS.append({'method':'POST' if data is not None else 'GET','path':path,'status':status})
        try:body=json.loads(body)
        except ValueError:body=None
        return status,body
    def values(self,uid=10000):
        with fixture_connect(DB) as db:
            points,cents=db.execute('SELECT points,balance_cents FROM player_profiles WHERE uid=?',(uid,)).fetchone()
            ledger=db.execute('SELECT count(*) FROM minigame_result_ledger WHERE uid=?',(uid,)).fetchone()[0]
        return points,cents,ledger
    def test_champion_ten_and_every_finisher_score_for_all_tiers(self):
        total=7
        for tier in range(1,6):
            for score in range(1,11):
                event={**self.event,'session':str(uuid4()),'difficulty':tier,'score':score,'win':score==10}
                self.assertEqual((200,{'ok':True,'credited':True,'points':score}),self.request(event))
                total+=score
        self.assertEqual((total,321,50),self.values())
    def test_concurrent_http_replay_credits_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies=list(pool.map(lambda _:self.request(self.event),range(16)))
        self.assertTrue(all(status==200 and data['points']==10 for status,data in replies))
        self.assertEqual(1,sum(data['credited'] for _,data in replies))
        self.assertEqual((17,321,1),self.values())
    def test_store_concurrency_and_reconstructed_store_replay(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies=list(pool.map(lambda _:main.platform_store.credit_game_result(self.event),range(16)))
        self.assertEqual(1,sum(r['credited'] for r in replies))
        from app.player_platform import PlatformStore
        self.assertEqual({'credited':False,'points':10},PlatformStore(DB).credit_game_result(self.event))
        self.assertEqual((17,321,1),self.values())
    def test_database_reopen_and_policy_changes_do_not_recredit(self):
        self.assertEqual(200,self.request(self.event)[0])
        from app.player_platform import PlatformStore
        previous=main.platform_store;main.platform_store=PlatformStore(DB)
        try:
            with patch.dict(os.environ,{'BMC_MINIGAME_REWARDS_JSON':'{"zombie-challenge":{"1":999}}'}):
                self.assertEqual((200,{'ok':True,'credited':False,'points':10}),self.request(self.event))
        finally:main.platform_store=previous
        self.assertEqual((17,321,1),self.values())
    def test_changed_result_replays_are_rejected(self):
        self.request(self.event)
        for key,value in [('score',9),('win',False),('difficulty',2),('seconds',61)]:
            self.assertEqual(400,self.request({**self.event,key:value})[0])
        self.assertEqual(400,self.request({**self.event,'score':9,'win':False})[0])
        self.assertEqual((17,321,1),self.values())
    def test_service_authentication_is_required_even_with_real_website_cookie(self):
        for key in (None,'wrong-test-key'):
            self.assertEqual(401,self.request(self.event,key=key,cookie=self.cookies[10000])[0])
            self.assertEqual(401,self.request({**self.event,'uid':10001},key=key,cookie=self.cookies[10000])[0])
        self.assertEqual((7,321,0),self.values());self.assertEqual((7,321,0),self.values(10001))
    def test_disabled_integration_is_rejected(self):
        with patch.dict(os.environ,{'BMC_GAME_PLATFORM_ENABLED':'0'}):self.assertEqual(503,self.request(self.event)[0])
        self.assertEqual((7,321,0),self.values())
    def test_uid_type_bounds_and_unknown_profile(self):
        for uid in (True,10000.0,'10000',9999,10000000000000000,None):
            self.assertEqual(400,self.request({**self.event,'uid':uid})[0])
        self.assertEqual(409,self.request({**self.event,'uid':10002})[0])
        self.assertEqual(200,self.request({**self.event,'uid':9999999999999999})[0])
        self.assertEqual((17,321,1),self.values(9999999999999999));self.assertEqual((7,321,0),self.values())
    def test_account_and_session_keys_are_independent(self):
        self.request(self.event);self.request({**self.event,'uid':10001})
        self.request({**self.event,'session':str(uuid4())})
        self.assertEqual((27,321,2),self.values());self.assertEqual((17,321,1),self.values(10001))
    def test_score_win_and_difficulty_bounds(self):
        for key,values in [('score',[0,11,-1,True,1.5,'10',None]),('win',[1,'true',None]),('difficulty',[0,6,-1,True,1.5,'1',None])]:
            for value in values:self.assertEqual(400,self.request({**self.event,key:value})[0],(key,value))
        self.assertEqual(400,self.request({**self.event,'score':9,'win':True})[0])
        self.assertEqual(400,self.request({**self.event,'score':10,'win':False})[0])
        self.assertEqual((7,321,0),self.values())
    def test_seconds_integer_bounds(self):
        for seconds in (-1,1000000001,True,1.5,'60',None):self.assertEqual(400,self.request({**self.event,'seconds':seconds})[0])
        for seconds in (0,1000000000):
            self.assertEqual(200,self.request({**self.event,'session':str(uuid4()),'seconds':seconds})[0])
        self.assertEqual((27,321,2),self.values())
    def test_noncanonical_sessions_and_field_injection(self):
        for session in ('short',str(uuid4()).upper(),uuid4().hex,'00000000-0000-1000-8000-000000000000',None):
            self.assertEqual(400,self.request({**self.event,'session':session})[0])
        for field in ('points','subject','rank','balance_cents','platformAdmin','gameOpLevel'):
            self.assertEqual(400,self.request({**self.event,field:999})[0])
        self.assertEqual(400,self.request({k:v for k,v in self.event.items() if k!='seconds'})[0])
        self.assertEqual(400,self.request(raw=b'[]')[0])
        self.assertEqual(400,self.request(raw=b'{')[0])
        self.assertEqual(413,self.request(raw=b'x'*4097)[0])
        self.assertEqual((7,321,0),self.values())
    def test_points_are_the_existing_readonly_platform_currency(self):
        self.request(self.event)
        status,data=self.request(path='/api/internal/game/admission/10000')
        self.assertEqual(200,status);self.assertEqual('17',data['points']);self.assertTrue(data['allowed'])
        self.assertEqual(401,self.request(key=None,path='/api/internal/game/admission/10000')[0])
        self.assertEqual({'platformAdmin':False,'gameOpLevel':0},main.platform_store.permissions(10000))
        self.assertEqual((17,321,1),self.values())
    def test_atomic_rollback_then_same_event_can_succeed(self):
        with fixture_connect(DB) as db:db.execute("CREATE TRIGGER horse_qa_reject BEFORE UPDATE ON player_profiles BEGIN SELECT RAISE(ABORT,'synthetic QA failure'); END")
        try:
            self.assertEqual(500,self.request(self.event)[0]);self.assertEqual((7,321,0),self.values())
        finally:
            with fixture_connect(DB) as db:db.execute('DROP TRIGGER horse_qa_reject')
        self.assertEqual(200,self.request(self.event)[0]);self.assertEqual((17,321,1),self.values())
    def test_other_games_and_reward_configuration_remain_separate(self):
        with patch.dict(os.environ,{'BMC_MINIGAME_REWARDS_JSON':'{"zombie-challenge":{"1":3},"outbreak":{"1":2}}'}):
            self.assertEqual(10,self.request(self.event)[1]['points'])
            for game,score in [('zombie-challenge',3),('outbreak',2)]:
                self.assertEqual(score,self.request({**self.event,'game':game})[1]['points'])
        self.assertEqual((22,321,3),self.values())
        with patch.dict(os.environ,{'BMC_MINIGAME_REWARDS_JSON':'{"horse_racing":{"1":999}}'}):self.assertEqual(400,self.request({**self.event,'session':str(uuid4())})[0])
        with patch.dict(os.environ,{'BMC_MINIGAME_REWARDS_JSON':'invalid'}):self.assertEqual(503,self.request({**self.event,'session':str(uuid4())})[0])
        self.assertEqual((22,321,3),self.values())

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HorseResults))
    receipt={'success':result.wasSuccessful(),'tests':result.testsRun,'actualLoopbackHTTP':True,'actualCurrentAppAndSQLite':True,'productionMutation':False,'temporaryDatabaseRemoved':False,'httpRequests':len(REQUESTS),'responseStatuses':{str(code):sum(r['status']==code for r in REQUESTS) for code in sorted({r['status'] for r in REQUESTS})},'ownHTTPServerExited':not HorseResults.thread.is_alive(),'trustedUIDScope':'API requires authenticated game service plus known website UID; existing GameRuntime/GamePlatform validates connection-bound TrustedAccounts before sending. Browser cookies alone cannot write results.'}
    TEMP.cleanup();receipt['temporaryDatabaseRemoved']=not DB.exists()
    output=os.environ.get('HORSE_QA_RECEIPT')
    if output:Path(output).write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt))
    sys.exit(0 if result.wasSuccessful() else 1)
