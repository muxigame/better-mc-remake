import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
os.environ['BMC_SKIP_DOTENV']='1'
_boot=tempfile.TemporaryDirectory()
os.environ.setdefault('BMC_DATABASE_PATH',str(Path(_boot.name)/'synthetic.db'))
os.environ.setdefault('BMC_PUBLIC_URL','http://testserver')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import main
from app.oidc import WebsiteAuthStore,WebsiteAccount
from app.player_platform import PlatformStore
from app.game_identity import offline_uuid

class GameOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.auth=WebsiteAuthStore(Path(self.temp.name)/'synthetic.db')
        self.store=PlatformStore(self.auth.database)
        self.accounts={}
        self.sessions={}
        for uid in (10000,10001,10002,10003):
            a=WebsiteAccount(subject='synthetic-'+str(uid),uid=uid,username='user'+str(uid),
                nickname='Player '+str(uid),game_name=str(uid),email=None,email_verified=False,
                role='admin' if uid==10003 else 'player')
            self.auth.player_profile(a);self.accounts[uid]=a;self.sessions[uid]=self.auth.create_session(a)
        self.store.initialize_permissions(10000,True,0)
        self.store.initialize_permissions(10002,False,4)
        self.stack=ExitStack()
        self.stack.enter_context(patch.object(main,'web_auth_store',self.auth))
        self.stack.enter_context(patch.object(main,'platform_store',self.store))
        self.stack.enter_context(patch.dict(os.environ,{'BMC_PUBLIC_URL':'http://testserver','BMC_GAME_OP_SYNC_ENABLED':'1','BMC_GAME_SERVICE_KEY':'synthetic-service-credential-00000000'}))
        self.client=TestClient(main.app)
        self.login(10000)
        self.origin={'origin':'http://testserver'}
        self.key={'x-muxi-server-key':os.environ['BMC_GAME_SERVICE_KEY']}
    def tearDown(self):
        self.client.close();self.stack.close();self.temp.cleanup()
    def login(self,uid): self.client.cookies.set('bmc_session',self.sessions[uid])
    def lookup(self,uid=10001):
        return self.client.get('/api/v1/platform/game-ops/'+str(uid)).json()['target']
    def data(self,target,level):
        return {'level':level,'expectedRevision':target['sync']['revision'],'expectedLevel':target['desiredLevel'],
                'identityConfirmation':target['identityConfirmation'],'reason':'synthetic reviewed change'}
    def save(self,target,level):
        return self.client.post('/api/v1/platform/game-ops/'+target['uid'],json=self.data(target,level),headers=self.origin)
    def ack(self,revision,level,applied=True,error=None):
        return self.client.post('/api/internal/game/ops-sync/ack',json={'uid':'10001','revision':revision,'applied':applied,'observedLevel':level,'error':error},headers=self.key)
    def test_only_platform_admin_even_direct_api(self):
        target=self.lookup()
        for uid in (10001,10002,10003):
            self.login(uid)
            self.assertEqual(403,self.client.get('/api/v1/platform/game-ops/10001').status_code)
            self.assertEqual(403,self.save(target,4).status_code)
        self.client.cookies.clear()
        self.assertEqual(401,self.save(target,4).status_code)
        self.assertEqual(0,self.store.permissions(10001)['gameOpLevel'])
    def test_grant_adjust_revoke_and_audit(self):
        for level in (4,2,0):
            target=self.lookup();response=self.save(target,level);self.assertEqual(200,response.status_code,response.text)
            self.assertEqual(level,response.json()['desiredLevel'])
            self.assertEqual('pending',response.json()['sync']['state'])
            self.assertFalse(self.store.permissions(10001)['platformAdmin'])
        with self.store.connect() as db:
            audit=db.execute('SELECT actor_uid,target_uid,revision,before_level,after_level FROM game_op_audit ORDER BY id').fetchall()
            self.assertEqual([(10000,10001,1,0,4),(10000,10001,2,4,2),(10000,10001,3,2,0)],[tuple(r) for r in audit])
            self.assertEqual(0,db.execute('SELECT points FROM player_profiles WHERE uid=10001').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT count(*) FROM game_bans').fetchone()[0])
        self.assertTrue(self.store.permissions(10000)['platformAdmin'])
    def test_csrf_and_payload_validation(self):
        target=self.lookup();path='/api/v1/platform/game-ops/10001';data=self.data(target,4)
        for headers in ({},{'origin':'http://evil.example'}):
            self.assertEqual(403,self.client.post(path,json=data,headers=headers).status_code)
        self.assertEqual(403,self.client.post(path,data='x',headers=self.origin).status_code)
        self.assertEqual(400,self.client.post(path,content='{',headers={**self.origin,'content-type':'application/json'}).status_code)
        self.assertEqual(413,self.client.post(path,content=' '*4097,headers={**self.origin,'content-type':'application/json'}).status_code)
        for level in (True,-1,5,1.5,'4',None):
            self.assertEqual(400,self.save(target,level).status_code)
        for field,value in (('expectedRevision',True),('expectedLevel',True),('reason',' '),('platformAdmin',True),('actorUid',10000)):
            bad={**data,field:value}
            self.assertEqual(400,self.client.post(path,json=bad,headers=self.origin).status_code)
        self.assertEqual(0,self.store.op_status(10001)['sync']['revision'])
    def test_identity_and_concurrent_confirmation(self):
        target=self.lookup();wrong={**self.data(target,4),'identityConfirmation':'wrong'}
        self.assertEqual(409,self.client.post('/api/v1/platform/game-ops/10001',json=wrong,headers=self.origin).status_code)
        self.assertEqual(200,self.save(target,4).status_code)
        self.assertEqual(409,self.save(target,0).status_code)
        self.assertEqual(404,self.client.get('/api/v1/platform/game-ops/19999').status_code)
        with self.store.connect() as db:
            db.execute("UPDATE player_profiles SET subject='changed-identity' WHERE uid=10001")
        self.assertEqual(409,self.save(target,1).status_code)
        self.assertEqual(409,self.client.get('/api/internal/game/ops-sync/',headers=self.key).status_code)
    def test_atomic_audit_failure(self):
        target=self.lookup()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON game_op_audit BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.set_op(10000,10001,4,0,0,target['identityConfirmation'],'synthetic')
        self.assertEqual(0,self.store.permissions(10001)['gameOpLevel'])
        self.assertEqual(0,self.store.op_status(10001)['sync']['revision'])
    def test_concurrent_writes(self):
        target=self.lookup()
        def write(level):
            try: self.store.set_op(10000,10001,level,0,0,target['identityConfirmation'],'synthetic');return True
            except RuntimeError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(1,sum(pool.map(write,(4,0))))
        with self.store.connect() as db: self.assertEqual(1,db.execute('SELECT count(*) FROM game_op_audit').fetchone()[0])
    def test_service_auth_disabled_and_browser_cannot_ack(self):
        for path in ('/api/internal/game/ops-sync/','/api/internal/game/ops-sync/?afterUid=10000'):
            self.assertEqual(401,self.client.get(path).status_code)
            self.assertEqual(200,self.client.get(path,headers=self.key).status_code)
        with patch.dict(os.environ,{'BMC_GAME_OP_SYNC_ENABLED':'0'}):
            self.assertEqual(503,self.client.get('/api/internal/game/ops-sync/',headers=self.key).status_code)
        self.assertEqual(401,self.client.post('/api/internal/game/ops-sync/ack',json={}).status_code)
        self.assertEqual(401,self.client.post('/api/internal/game/ops-sync/observations',json=[]).status_code)
    def test_retries_stale_ack_and_game_edits_never_change_desired(self):
        self.assertEqual(200,self.save(self.lookup(),4).status_code)
        self.assertEqual(200,self.ack(1,None,False,'apply_failed').status_code)
        self.assertEqual('failed',self.store.op_status(10001)['sync']['state'])
        self.assertEqual(200,self.ack(1,4).status_code)
        self.assertEqual(200,self.ack(1,4).status_code)
        with self.store.connect() as db: self.assertEqual(2,db.execute('SELECT count(*) FROM game_op_sync_audit').fetchone()[0])
        self.assertEqual(200,self.ack(1,2).status_code)
        status=self.store.op_status(10001);self.assertEqual(4,status['desiredLevel']);self.assertEqual('changed_in_game',status['sync']['state'])
        self.assertFalse(self.store.permissions(10001)['platformAdmin'])
        self.assertEqual(200,self.save(self.lookup(),4).status_code) # Same level is a new explicit event.
        self.assertEqual(409,self.ack(1,0).status_code)
        self.assertEqual(2,self.store.op_status(10001)['sync']['revision'])
        self.assertEqual(200,self.ack(2,4).status_code)
        feed=self.client.get('/api/internal/game/ops-sync/',headers=self.key).json()
        self.assertEqual({'uid':'10001','offlineUuid':offline_uuid(10001),'revision':2,'level':4},feed['records'][0])
    def test_self_status_and_read_only_observations(self):
        records=[{'uid':'10002','offlineUuid':offline_uuid(10002),'level':3}]
        response=self.client.post('/api/internal/game/ops-sync/observations',json=records,headers=self.key)
        self.assertEqual(200,response.status_code)
        self.login(10002)
        status=self.client.get('/api/v1/player/game-op?uid=10001').json()
        self.assertEqual('10002',status['uid']);self.assertEqual(4,status['desiredLevel'])
        self.assertEqual(3,status['sync']['observedLevel']);self.assertEqual('observed',status['sync']['state'])
        self.assertNotIn('identityConfirmation',status)
        self.assertFalse(self.client.get('/api/v1/player/profile').json()['permissions']['canManageGameOp'])
        self.assertEqual(403,self.client.get('/api/v1/platform/game-ops/10001').status_code)
        with self.store.connect() as db: db.execute("UPDATE game_op_observations SET observed_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual('stale',self.store.op_status(10002)['sync']['state'])
        wrong=[{**records[0],'offlineUuid':offline_uuid(10001)}]
        self.assertEqual(400,self.client.post('/api/internal/game/ops-sync/observations',json=wrong,headers=self.key).status_code)
        self.assertEqual(400,self.client.post('/api/internal/game/ops-sync/observations',json=records*2,headers=self.key).status_code)
        self.assertEqual(4,self.store.permissions(10002)['gameOpLevel'])
    def test_feed_pagination_and_large_uid_precision(self):
        large=9999999999999999
        a=WebsiteAccount(subject='synthetic-large',uid=large,username='large',nickname='Large',game_name=str(large),email=None,email_verified=False,role='player')
        self.auth.player_profile(a);self.auth.create_session(a)
        target=self.lookup(large);self.assertEqual(str(large),target['uid'])
        self.assertEqual(200,self.save(target,2).status_code)
        feed=self.store.op_feed();self.assertEqual(str(large),feed['records'][0]['uid'])
        self.assertEqual([],self.store.op_feed(large)['records'])
        for uid in range(10100,10201):
            a=WebsiteAccount(subject='synthetic-'+str(uid),uid=uid,username='u',nickname='P',game_name=str(uid),email=None,email_verified=False,role='player')
            self.auth.player_profile(a);self.auth.create_session(a)
            t=self.store.op_target(10000,uid)
            self.store.set_op(10000,uid,1,0,0,t['identityConfirmation'],'synthetic')
        first=self.store.op_feed();self.assertEqual(100,len(first['records']))
        second=self.store.op_feed(int(first['nextUid']));self.assertEqual(2,len(second['records']))
        self.assertIsNone(second['nextUid'])
    def test_numeric_bounds_and_revoked_administrator(self):
        target=self.lookup();data=self.data(target,4)
        for value in (9007199254740992,10**100):
            bad={**data,'expectedRevision':value}
            self.assertEqual(400,self.client.post('/api/v1/platform/game-ops/10001',json=bad,headers=self.origin).status_code)
            self.assertEqual(400,self.ack(value,4).status_code)
        self.assertEqual(400,self.client.post('/api/v1/platform/game-ops/10001',json={**data,'expectedRevision':9007199254740991},headers=self.origin).status_code)
        for cursor in (-1,10**100):
            self.assertEqual(400,self.client.get('/api/internal/game/ops-sync/?afterUid='+str(cursor),headers=self.key).status_code)
        with self.store.connect() as db:db.execute('UPDATE platform_permissions SET platform_admin=0 WHERE uid=10000')
        self.assertEqual(403,self.save(target,4).status_code)
        self.assertEqual(0,self.store.op_status(10001)['sync']['revision'])
    def test_website_restart_keeps_event_and_local_observation(self):
        self.assertEqual(200,self.save(self.lookup(),4).status_code)
        self.assertEqual(200,self.ack(1,2).status_code)
        reopened=PlatformStore(self.auth.database)
        self.assertEqual(4,reopened.permissions(10001)['gameOpLevel'])
        self.assertEqual('changed_in_game',reopened.op_status(10001)['sync']['state'])
        self.assertEqual(1,reopened.op_feed()['records'][0]['revision'])
        reopened.acknowledge_op(10001,1,True,2,None)
        with reopened.connect() as db:self.assertEqual(1,db.execute('SELECT count(*) FROM game_op_sync_audit').fetchone()[0])
    def test_actual_freshness_remains_honest_during_pending_or_failure(self):
        self.assertEqual('unknown',self.store.op_status(10001)['sync']['observedState'])
        self.assertEqual(200,self.save(self.lookup(),4).status_code)
        self.assertEqual(200,self.ack(1,4).status_code)
        self.assertEqual('fresh',self.store.op_status(10001)['sync']['observedState'])
        with self.store.connect() as db:db.execute("UPDATE game_op_observations SET observed_at='2000-01-01T00:00:00+00:00' WHERE uid=10001")
        self.assertEqual(200,self.save(self.lookup(),0).status_code)
        status=self.store.op_status(10001)['sync']
        self.assertEqual('pending',status['state']);self.assertEqual('stale',status['observedState'])
        self.assertEqual(200,self.ack(2,None,False,'apply_failed').status_code)
        status=self.store.op_status(10001)['sync']
        self.assertEqual('failed',status['state']);self.assertEqual('stale',status['observedState'])
        self.assertEqual(4,status['observedLevel']);self.assertEqual(0,self.store.permissions(10001)['gameOpLevel'])
if __name__=='__main__': unittest.main(verbosity=2)
