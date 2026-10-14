from pathlib import Path
import argparse,sqlite3,tempfile,unittest,uuid
from concurrent.futures import ThreadPoolExecutor
from horse_points_fixture import load_backend
class HorsePoints(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'points.sqlite'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE player_profiles(subject TEXT PRIMARY KEY, uid INTEGER, points INTEGER, balance_cents INTEGER, updated_at TEXT)');db.execute("INSERT INTO player_profiles VALUES('fixture',10000,0,321,'')")
        self.store=module.PlatformStore(self.path);self.event={'uid':10000,'game':'horse_racing','session':str(uuid.uuid4()),'win':True,'score':10,'difficulty':5,'seconds':29}
    def tearDown(self):
        # SQLite contexts commit but do not close; collect unreferenced handles before Windows cleanup.
        import gc
        gc.collect()
        self.temp.cleanup()
    def value(self):
        with sqlite3.connect(self.path) as db:return db.execute('SELECT points,balance_cents FROM player_profiles').fetchone()
    def test_every_rank_rewards_and_cap(self):
        for score in range(1,11):
            receipt=self.store.credit_game_result({**self.event,'session':str(uuid.uuid4()),'score':score,'win':score==10});self.assertEqual(score,receipt['points'])
        self.assertEqual((55,321),self.value())
    def test_concurrent_retry_restart(self):
        with ThreadPoolExecutor(max_workers=8) as pool:receipts=list(pool.map(lambda _:self.store.credit_game_result(self.event),range(24)))
        self.assertEqual(1,sum(r['credited'] for r in receipts));self.assertEqual((10,321),self.value());self.assertFalse(module.PlatformStore(self.path).credit_game_result(self.event)['credited'])
    def test_reject_invalid_without_mutation(self):
        for invalid in [{'score':0},{'score':11},{'score':True},{'win':False},{'score':9},{'difficulty':0},{'difficulty':6},{'points':100},{'uid':10001}]:
            with self.assertRaises((ValueError,LookupError)):self.store.credit_game_result({**self.event,**invalid})
        self.assertEqual((0,321),self.value())
    def test_conflict_and_atomic_rollback(self):
        self.store.credit_game_result(self.event)
        with self.assertRaises(ValueError):self.store.credit_game_result({**self.event,'seconds':30})
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER reject_points BEFORE UPDATE ON player_profiles BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.credit_game_result({**self.event,'session':str(uuid.uuid4())})
        with sqlite3.connect(self.path) as db:self.assertEqual(1,db.execute('SELECT count(*) FROM minigame_result_ledger').fetchone()[0])
        self.assertEqual((10,321),self.value())
    def test_other_games_keep_policy(self):
        self.assertEqual(0,self.store.credit_game_result({**self.event,'game':'flight','win':False,'score':0,'difficulty':0})['points'])
        self.assertEqual(3,self.store.credit_game_result({**self.event,'game':'zombie-challenge','difficulty':1},{'zombie-challenge':{'1':3}})['points'])
        self.assertEqual(0,self.store.credit_game_result({**self.event,'game':'outbreak','win':False,'difficulty':1,'score':9},{'outbreak':{'1':3}})['points'])
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--backend-source',type=Path,required=True);args=parser.parse_args();module=load_backend(args.backend_source);unittest.main(argv=['horse-points'],verbosity=2)
