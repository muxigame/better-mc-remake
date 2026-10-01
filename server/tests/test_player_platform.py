import importlib.util
import sqlite3
from contextlib import contextmanager
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

spec=importlib.util.spec_from_file_location('player_platform',Path(__file__).resolve().parents[1]/'app/player_platform.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
PlatformStore=module.PlatformStore

@contextmanager
def fixture_connect(path):
    db=sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'test.db'
        with fixture_connect(self.path) as db:
            db.execute('CREATE TABLE player_profiles(subject TEXT PRIMARY KEY, uid INTEGER, points INTEGER, balance_cents INTEGER, updated_at TEXT)')
            db.execute("INSERT INTO player_profiles VALUES('synthetic',10000,0,4321,'')")
        self.store=PlatformStore(self.path)
    def tearDown(self): self.temp.cleanup()
    def points(self):
        with fixture_connect(self.path) as db: return db.execute('SELECT points,balance_cents FROM player_profiles').fetchone()
    def test_normal_hard_and_duplicate(self):
        self.assertTrue(self.store.credit(10000,'2026-09-29','normal',False))
        self.assertTrue(self.store.credit(10000,'2026-09-29','hard',True))
        self.assertFalse(self.store.credit(10000,'2026-09-29','normal',False))
        self.assertEqual((4,4321),self.points())
    def test_task_timezone_can_be_ahead_of_utc(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return datetime(2026,9,30,20,tzinfo=timezone.utc)
        with patch.object(module,'datetime',Clock):
            self.assertTrue(self.store.credit(10000,'2026-10-01','timezone',False))
            with self.assertRaises(ValueError): self.store.credit(10000,'2026-10-02','future',False)
        self.assertEqual(1,self.points()[0])
    def test_concurrent_replays(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _: self.store.credit(10000,'2026-09-29','same',True),range(16)))
        self.assertEqual(1,sum(results));self.assertEqual(3,self.points()[0])
    def test_lost_ack_restart(self):
        self.store.credit(10000,'2026-09-29','normal',False)
        restarted=PlatformStore(self.path)
        self.assertFalse(restarted.credit(10000,'2026-09-29','normal',False))
        self.assertEqual(1,self.points()[0])
    def test_conflicting_replay(self):
        self.store.credit(10000,'2026-09-29','normal',False)
        with self.assertRaises(ValueError): self.store.credit(10000,'2026-09-29','normal',True)
        self.assertEqual(1,self.points()[0])
    def test_unregistered_and_bad_event(self):
        with self.assertRaises(LookupError): self.store.credit(10001,'2026-09-29','task',False)
        for args in [(True,'2026-09-29','task',False),(10000,'bad','task',False),(10000,'2026-09-29','task',1),(10000,'2026-09-29','bad/task',False),(10000,'2999-01-01','task',False)]:
            with self.assertRaises(ValueError): self.store.credit(*args)
        self.assertEqual(0,self.points()[0])
    def test_atomic_rollback(self):
        with fixture_connect(self.path) as db:
            db.execute("CREATE TRIGGER reject_update BEFORE UPDATE ON player_profiles BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.store.credit(10000,'2026-09-29','task',False)
        with fixture_connect(self.path) as db: self.assertEqual(0,db.execute('SELECT count(*) FROM task_point_ledger').fetchone()[0])
    def test_permissions_independent(self):
        self.store.initialize_permissions(10000,False,4)
        self.assertEqual({'platformAdmin':False,'gameOpLevel':4},self.store.permissions(10000))
        with self.assertRaises(PermissionError): self.store.set_ban(10000,10001,True,'synthetic')
        self.store.initialize_permissions(10000,True,0)
        self.assertEqual(0,self.store.permissions(10000)['gameOpLevel'])
        self.store.set_ban(10000,10001,True,'synthetic');self.assertTrue(self.store.banned(10001))
        self.store.set_ban(10000,10001,False,'synthetic');self.assertFalse(self.store.banned(10001))
        with fixture_connect(self.path) as db: self.assertEqual(2,db.execute('SELECT count(*) FROM game_ban_audit').fetchone()[0])
    def test_proposal_not_automatic(self):
        self.assertFalse(self.store.permissions(10000)['platformAdmin'])
        self.store.initialize_permissions(10000,True,4)
        self.assertEqual({'platformAdmin':True,'gameOpLevel':4},self.store.permissions(10000))

if __name__=='__main__': unittest.main(verbosity=2)
