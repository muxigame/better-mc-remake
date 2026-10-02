import concurrent.futures
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from app.game_identity import offline_uuid
from app.social import SocialStore, social_uid


class SocialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'synthetic.db'
        self.now = [1000.0]
        self.store = SocialStore(self.path, clock=lambda: self.now[0])
        for uid in (10000, 10001, 10002, 9999999999999999):
            self.store.register(SimpleNamespace(uid=uid, subject=f'synthetic-{uid}', nickname='Same nickname', username='synthetic'))

    def tearDown(self):
        self.temp.cleanup()

    def online(self, *uids):
        self.store.presence([{'uid': str(uid), 'uuid': offline_uuid(uid), 'gameName': str(uid)} for uid in uids])

    def friend(self):
        self.store.mutate(10000, 10001, 'request')
        self.store.mutate(10001, 10000, 'accept')

    def test_explicit_recipient_accept_and_crossed_requests(self):
        self.assertTrue(self.store.mutate(10000, 10001, 'request'))
        self.assertFalse(self.store.mutate(10000, 10001, 'request'))
        self.assertEqual([], self.store.snapshot(10000)['friends'])
        with self.assertRaises(LookupError):
            self.store.mutate(10000, 10001, 'accept')
        self.store.mutate(10001, 10000, 'request')
        self.assertEqual([], self.store.snapshot(10000)['friends'])
        self.assertTrue(self.store.mutate(10001, 10000, 'accept'))
        self.assertFalse(self.store.mutate(10001, 10000, 'accept'))
        self.assertEqual('10001', self.store.snapshot(10000)['friends'][0]['uid'])
        self.assertEqual('10000', self.store.snapshot(10001)['friends'][0]['uid'])
        self.assertEqual([], self.store.snapshot(10000)['incoming'])

    def test_block_removes_both_directions_and_unblock_does_not_restore(self):
        self.friend()
        self.assertTrue(self.store.mutate(10000, 10001, 'block'))
        self.assertEqual([], self.store.snapshot(10001)['friends'])
        self.assertFalse(self.store.mutate(10000, 10001, 'block'))
        for actor, peer in ((10000,10001),(10001,10000)):
            with self.assertRaises(PermissionError):
                self.store.mutate(actor, peer, 'request')
        self.store.mutate(10000, 10001, 'unblock')
        self.assertEqual([], self.store.snapshot(10000)['friends'])
        self.store.mutate(10000, 10001, 'request')
        self.store.mutate(10001, 10000, 'request')
        self.store.mutate(10001, 10000, 'block')
        self.assertEqual([], self.store.snapshot(10000)['incoming'])
        self.assertEqual([], self.store.snapshot(10000)['outgoing'])

    def test_persistence_and_receipt_replay_cannot_undo_later_block(self):
        key = str(uuid.uuid4())
        self.store.mutate(10000,10001,'request',key)
        self.store.mutate(10001,10000,'accept')
        self.store.mutate(10001,10000,'block')
        reopened = SocialStore(self.path)
        self.assertTrue(reopened.mutate(10000,10001,'request',key))
        self.assertEqual([], reopened.snapshot(10001)['incoming'])
        with self.assertRaises(ValueError):
            reopened.mutate(10000,10002,'request',key)

    def test_online_players_do_not_require_friendship_friends_source_does(self):
        self.online(10000,10001)
        self.assertTrue(self.store.eligibility(10000,10001,'online')['allowed'])
        self.assertFalse(self.store.eligibility(10000,10001,'friends')['allowed'])
        self.friend()
        self.assertTrue(self.store.eligibility(10000,10001,'friends')['allowed'])
        self.store.mutate(10001,10000,'block')
        self.assertFalse(self.store.eligibility(10000,10001,'online')['allowed'])

    def test_online_snapshot_replacement_ttl_and_clock_reversal_fail_closed(self):
        self.friend(); self.online(10000,10001)
        self.assertTrue(self.store.snapshot(10000)['friends'][0]['online'])
        self.online(10000)
        self.assertFalse(self.store.snapshot(10000)['friends'][0]['online'])
        self.online(10000,10001)
        self.now[0] += 90
        self.assertFalse(self.store.snapshot(10000)['presenceAvailable'])
        self.assertFalse(self.store.eligibility(10000,10001,'online')['allowed'])
        self.now[0] = 999
        self.assertFalse(self.store.snapshot(10000)['presenceAvailable'])

    def test_reject_injected_names_and_fake_uuid_atomically(self):
        self.online(10000)
        for name in ('nickname', '10001\n/op @a', '@a', '10001 ', '/msg 10001'):
            with self.assertRaises(ValueError):
                self.store.presence([{'uid':'10001','uuid':offline_uuid(10001),'gameName':name}])
        with self.assertRaises(ValueError):
            self.store.presence([{'uid':'10001','uuid':str(uuid.uuid4()),'gameName':'10001'}])
        with self.assertRaises(ValueError):
            self.store.presence([{'uid':'10001','uuid':offline_uuid(10001),'gameName':'10001','online':True}])
        with self.store.connect() as db:
            self.assertEqual([10000],[r[0] for r in db.execute('SELECT uid FROM social_presence')])

    def test_uid_precision_self_unknown_and_subject_binding(self):
        for bad in (True,False,1.0,10000.0,'010000',' 10000','10000\n',9999,10000000000000000):
            with self.assertRaises(ValueError): social_uid(bad)
        self.store.mutate(10000,9999999999999999,'request')
        self.assertEqual('9999999999999999',self.store.snapshot(10000)['outgoing'][0]['uid'])
        with self.assertRaises(ValueError): self.store.mutate(10000,10000,'request')
        with self.assertRaises(LookupError): self.store.mutate(10000,10009,'request')
        with self.assertRaises(PermissionError):
            self.store.register(SimpleNamespace(uid=10000,subject='forged',nickname='',username=''))

    def test_parallel_accept_block_cannot_leave_friendship_with_block(self):
        self.store.mutate(10000,10001,'request')
        def write(action):
            try: return SocialStore(self.path).mutate(10001,10000,action)
            except PermissionError: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(write,['accept','block']))
        self.assertEqual([],self.store.snapshot(10000)['friends'])
        self.assertEqual([],self.store.snapshot(10001)['incoming'])

    def test_expired_request_cannot_be_accepted(self):
        self.store.mutate(10000,10001,'request')
        with self.store.connect() as db:
            db.execute("UPDATE social_requests SET created_at='2000-01-01 00:00:00'")
        self.assertEqual([],self.store.snapshot(10001)['incoming'])
        with self.assertRaises(LookupError): self.store.mutate(10001,10000,'accept')

    def test_unrelated_existing_data_remains_identical(self):
        with self.store.connect() as db:
            db.execute('CREATE TABLE synthetic_balance(uid INTEGER,points INTEGER,balance_cents INTEGER)')
            db.execute('INSERT INTO synthetic_balance VALUES(10000,123,456)')
        self.friend(); self.store.mutate(10000,10001,'remove')
        with self.store.connect() as db:
            self.assertEqual((10000,123,456),tuple(db.execute('SELECT * FROM synthetic_balance').fetchone()))


if __name__ == '__main__': unittest.main()
