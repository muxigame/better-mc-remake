import importlib
import sys
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager

@contextmanager
def fixture_connect(path):
    db=sqlite3.connect(path)
    try:
        with db:yield db
    finally:db.close()

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
module=importlib.import_module('app.player_platform')

class Results(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'fixture.db'
        with fixture_connect(self.path) as db:
            db.execute('CREATE TABLE player_profiles(subject TEXT PRIMARY KEY, uid INTEGER, points INTEGER, balance_cents INTEGER, updated_at TEXT)')
            db.execute("INSERT INTO player_profiles VALUES('verified-test',10000,7,321,'')")
        self.store=module.PlatformStore(self.path)
        self.event={'uid':10000,'game':'zombie-challenge','session':str(uuid4()),'win':True,'score':900,'difficulty':1,'seconds':180}
        self.policy={'zombie-challenge':{'1':3},'outbreak':{'1':2}}
    def tearDown(self):self.temp.cleanup()
    def values(self):
        with fixture_connect(self.path) as db:return db.execute('SELECT points,balance_cents FROM player_profiles').fetchone()
    def test_concurrent_replay_and_restart(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.store.credit_game_result(self.event,self.policy),range(16)))
        self.assertEqual(1,sum(r['credited'] for r in results));self.assertEqual((10,321),self.values())
        replay=module.PlatformStore(self.path).credit_game_result(self.event,{'zombie-challenge':{'1':99}})
        self.assertEqual({'credited':False,'points':3},replay)
    def test_conflicting_replay(self):
        self.store.credit_game_result(self.event,self.policy)
        for key,value in [('score',901),('win',False),('difficulty',2),('seconds',181)]:
            with self.assertRaises(ValueError):self.store.credit_game_result({**self.event,key:value},self.policy)
        self.assertEqual((10,321),self.values())
    def test_server_policy_and_failure(self):
        self.assertEqual(0,self.store.credit_game_result(self.event)['points']);self.assertEqual((7,321),self.values())
        event={**self.event,'session':str(uuid4()),'win':False,'score':10**12}
        self.assertEqual(0,self.store.credit_game_result(event,self.policy)['points'])
    def test_games_and_accounts_have_independent_keys(self):
        self.store.credit_game_result(self.event,self.policy)
        self.store.credit_game_result({**self.event,'game':'outbreak'},self.policy)
        self.assertEqual((12,321),self.values())
        with self.assertRaises(LookupError):self.store.credit_game_result({**self.event,'uid':10001},self.policy)
    def test_ban_does_not_block_durable_settlement(self):
        self.store.set_ban(10000,10000,True,'fixture',account_admin=True)
        self.assertTrue(self.store.banned(10000));self.store.credit_game_result(self.event,self.policy)
        self.assertEqual((10,321),self.values())
    def test_atomic_rollback(self):
        with fixture_connect(self.path) as db:db.execute("CREATE TRIGGER reject_points BEFORE UPDATE ON player_profiles BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.credit_game_result(self.event,self.policy)
        with fixture_connect(self.path) as db:self.assertEqual(0,db.execute('SELECT count(*) FROM minigame_result_ledger').fetchone()[0])
    def test_untrusted_fields_and_bounds(self):
        for invalid in [{**self.event,'points':1000},{**self.event,'uid':True},{**self.event,'score':True},{**self.event,'score':-1},{**self.event,'difficulty':99},{**self.event,'win':1},{**self.event,'session':'short-id'},{**self.event,'game':'unknown'}]:
            with self.assertRaises((ValueError,TypeError)):self.store.credit_game_result(invalid,self.policy)
        for policy in [[],0,False,{'zombie-challenge':{'1':True}},{'unknown':{'1':1}},{'outbreak':{'1':10001}}]:
            with self.assertRaises(ValueError):self.store.credit_game_result(self.event,policy)
        self.assertEqual((7,321),self.values())

if __name__=='__main__':unittest.main(verbosity=2)
