"""Real SQLite races; barriers/events establish order without sleep or fake models.

All bytes, validator receipts, identities and permanent-cleanup approvals below
are disposable test fixtures. These tests establish Web transaction behavior,
not native YSM compatibility or a production activation/cleanup approval.
"""
from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from app.ysm_assets import AssetError, ValidationReceipt, YsmAssetStore


TEST_ROOT = Path(__file__).resolve().parents[2] / 'build' / 'owner-131-ysm-assets-races'
TEST_ROOT.mkdir(parents=True, exist_ok=True)
DATA = b'synthetic race fixture, not a production YSM model\x00'
OTHER_DATA = b'a different synthetic race payload\x00'
SHA = hashlib.sha256(DATA).hexdigest()
PROFILE = 'synthetic-race-validator-v1'
TIMEOUT = 10


@dataclass
class WriteGate:
    """Pause once *after* the real connection acquires BEGIN IMMEDIATE."""
    before: threading.Event = field(default_factory=threading.Event)
    acquired: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    hold: bool = False
    used: bool = False


class ObservableStore(YsmAssetStore):
    def __init__(self, *args, **kwargs):
        self.thread_state = threading.local()
        super().__init__(*args, **kwargs)

    @contextmanager
    def using_gate(self, gate):
        self.thread_state.gate = gate
        try:
            yield
        finally:
            self.thread_state.gate = None

    @contextmanager
    def _db(self, write=False):
        gate = getattr(self.thread_state, 'gate', None)
        if not write or gate is None or gate.used:
            with super()._db(write) as db:
                yield db
            return
        gate.used = True
        gate.before.set()
        with super()._db(True) as db:
            gate.acquired.set()
            if gate.hold and not gate.release.wait(TIMEOUT):
                raise TimeoutError('Fixture did not release the actual SQLite write lock')
            yield db


class YsmAssetRaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.now = 1000000.0
        self.store = ObservableStore(Path(self.tmp.name), validation_profile=PROFILE, clock=lambda:self.now)
        self.counter = 0
        self.leases = []

    def tearDown(self):
        for uid, lease, path in self.leases:
            self.store.end_upload(uid, lease, path)
        self.tmp.cleanup()

    def key(self, suffix='request'):
        self.counter += 1
        return f'synthetic-race-{suffix}-{self.counter}'

    def prepared(self, uid=10000, data=DATA):
        lease, path = self.store.begin_upload(uid)
        path.write_bytes(data)
        self.leases.append((uid, lease, path))
        return path

    def upload(self, uid=10000, name='model.ysm', visibility='private', data=DATA, key=None):
        return self.store.ingest(uid, name, self.prepared(uid, data), key or self.key(), visibility)

    def verified_public(self):
        model = self.upload(visibility='public')['model']
        self.store.promote_verified(ValidationReceipt(SHA, len(DATA), PROFILE, self.key('validator'), True))
        self.store.mark_synced(SHA, self.store.bridge_snapshot()['revision'], 'fixture-import-and-acl', active=True)
        public_id = self.store.public_by_sha256(10001, SHA)['model']['id']
        return model['id'], public_id

    @staticmethod
    def outcome(fn):
        try:
            return {'ok':True, 'data':fn()}
        except AssetError as error:
            return {'ok':False, 'status':error.status, 'code':error.code}

    def concurrent(self, *operations):
        barrier = threading.Barrier(len(operations) + 1)
        def run(operation):
            barrier.wait(TIMEOUT)
            return self.outcome(operation)
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            futures = [pool.submit(run, operation) for operation in operations]
            barrier.wait(TIMEOUT)
            return [future.result(TIMEOUT) for future in futures]

    def ordered(self, first_operation, second_operation):
        """Second writer attempts SQLite while first holds its actual write lock.

        No read transaction is kept open by the test while futures are joined.
        Releasing the first gate lets its real production method commit/rollback
        before SQLite admits the second method's authorization checks.
        """
        first_gate, second_gate = WriteGate(hold=True), WriteGate()
        def run(operation, gate):
            with self.store.using_gate(gate):
                return self.outcome(operation)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, first_operation, first_gate)
            try:
                self.assertTrue(first_gate.acquired.wait(TIMEOUT), 'First operation never acquired SQLite')
                second = pool.submit(run, second_operation, second_gate)
                self.assertTrue(second_gate.before.wait(TIMEOUT), 'Second operation never attempted SQLite')
                self.assertFalse(second_gate.acquired.is_set(), 'Competing writer entered a held transaction')
            finally:
                first_gate.release.set()
            return first.result(TIMEOUT), second.result(TIMEOUT)

    def rows(self, table):
        with self.store._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM '+table)]

    def assert_failure(self, result, code, status=409):
        self.assertEqual({'ok':False,'status':status,'code':code}, result)

    def assert_events(self, expected):
        events = self.store.events_after(0)
        self.assertEqual(expected, [event['type'] for event in events])
        self.assertEqual(list(range(1,len(events)+1)), [event['seq'] for event in events])
        self.assertEqual(list(range(1,len(events)+1)), [event['revision'] for event in events])
        self.assertEqual(len(events), self.store.bridge_snapshot()['revision'])
        return events

    def test_same_uid_same_key_and_bytes_commit_one_asset_blob_and_event(self):
        key = self.key()
        one, two = self.prepared(), self.prepared()
        results = self.concurrent(lambda:self.store.ingest(10000,'same.ysm',one,key),
                                  lambda:self.store.ingest(10000,'same.ysm',two,key))
        self.assertTrue(all(result['ok'] for result in results))
        models = [result['data']['model'] for result in results]
        self.assertEqual(models[0]['id'], models[1]['id'])
        self.assertEqual([False,True], sorted(result['data']['replayed'] for result in results))
        self.assertEqual(1,len(self.rows('ysm_assets')))
        self.assertEqual(1,len(self.rows('ysm_contents')))
        self.assertEqual(1,len(self.rows('ysm_requests')))
        self.assertEqual(DATA,self.store._path(SHA).read_bytes())
        self.assert_events(['asset.created'])

    def test_same_uid_same_key_different_bytes_conflict_without_loser_blob_or_event(self):
        key = self.key()
        one, two = self.prepared(data=DATA), self.prepared(data=OTHER_DATA)
        results = self.concurrent(lambda:self.store.ingest(10000,'same.ysm',one,key),
                                  lambda:self.store.ingest(10000,'same.ysm',two,key))
        self.assertEqual(1,sum(result['ok'] for result in results))
        failure = next(result for result in results if not result['ok'])
        self.assert_failure(failure,'idempotency_conflict')
        contents = self.rows('ysm_contents')
        self.assertEqual(1,len(contents))
        self.assertEqual(1,len(list(self.store.quarantine.glob('*.bin'))))
        self.assertEqual(contents[0]['sha256'],hashlib.sha256(self.store._path(contents[0]['sha256']).read_bytes()).hexdigest())
        self.assertEqual(1,len(self.rows('ysm_requests')))
        self.assert_events(['asset.created'])

    def test_cross_uid_same_sha_and_key_preserve_independent_private_refs(self):
        one, two = self.prepared(10000), self.prepared(10001)
        key = self.key()
        results = self.concurrent(lambda:self.store.ingest(10000,'same.ysm',one,key),
                                  lambda:self.store.ingest(10001,'same.ysm',two,key))
        self.assertTrue(all(result['ok'] for result in results))
        self.assertEqual(1,len(self.rows('ysm_contents')))
        self.assertEqual(2,len(self.rows('ysm_assets')))
        ids = [self.store.list_models(uid)['models'][0]['id'] for uid in (10000,10001)]
        self.assertNotEqual(*ids)
        self.store.promote_verified(ValidationReceipt(SHA,len(DATA),PROFILE,self.key('validator'),True))
        for actor, peer_id in ((10000,ids[1]),(10001,ids[0])):
            self.assert_failure(self.outcome(lambda:self.store.download(actor,peer_id)),'model_not_found',404)
        self.assertEqual([],self.store.public_models(10002)['models'])
        self.assert_failure(self.outcome(lambda:self.store.public_by_sha256(10002,SHA)),'public_model_not_found',404)
        refs = self.store.bridge_snapshot()['models'][0]['references']
        self.assertEqual({('10000',ids[0],'private'),('10001',ids[1],'private')},
                         {(r['ownerUid'],r['modelId'],r['visibility']) for r in refs})
        events = self.assert_events(['asset.created','asset.created','content.verified'])
        self.assertEqual({'10000','10001'},{event['uid'] for event in events[:2]})

    def test_public_lookup_then_revocation_wins_against_reference_creation(self):
        own_id, public_id = self.verified_public()
        self.assertEqual(public_id,self.store.public_by_sha256(10001,SHA)['model']['id'])
        private_key, reference_key = self.key(), self.key()
        revoked, reference = self.ordered(
            lambda:self.store.mutate(10000,own_id,'visibility',private_key,'private'),
            lambda:self.store.create_reference(10001,public_id,'saved.ysm',reference_key,'public'))
        self.assertTrue(revoked['ok'])
        self.assert_failure(reference,'public_model_not_found',404)
        self.assertEqual([],self.store.list_models(10001)['models'])
        self.assertEqual(1,len(self.rows('ysm_assets')))
        self.assertEqual(['10000'],self.store.bridge_snapshot()['models'][0]['ownerUids'])
        self.assert_events(['asset.created','content.verified','asset.visibility'])

    def test_reference_committed_before_revocation_remains_own_private_reference(self):
        own_id, public_id = self.verified_public()
        reference_key, private_key = self.key(), self.key()
        reference, revoked = self.ordered(
            lambda:self.store.create_reference(10001,public_id,'saved.ysm',reference_key,'private'),
            lambda:self.store.mutate(10000,own_id,'visibility',private_key,'private'))
        self.assertTrue(reference['ok'])
        self.assertTrue(revoked['ok'])
        self.assertEqual([],self.store.public_models(10002)['models'])
        saved_id = reference['data']['model']['id']
        self.assertEqual(DATA,self.store.download(10001,saved_id)[0].read_bytes())
        self.assert_failure(self.outcome(lambda:self.store.download(10002,saved_id)),'model_not_found',404)
        self.assert_events(['asset.created','content.verified','asset.created','asset.visibility'])

    def test_delete_wins_against_fresh_upload_and_neither_fresh_nor_original_key_resurrects(self):
        original_key = self.key()
        model_id = self.upload(key=original_key)['model']['id']
        retry_path = self.prepared()
        deleted, retried = self.ordered(
            lambda:self.store.mutate(10000,model_id,'delete',self.key('delete')),
            lambda:self.store.ingest(10000,'model.ysm',retry_path,self.key('retry')))
        self.assertTrue(deleted['ok'])
        self.assert_failure(retried,'model_deleted_use_restore')
        replay = self.store.ingest(10000,'model.ysm',retry_path,original_key)
        self.assertTrue(replay['replayed'])
        self.assertIsNotNone(replay['model']['deletedAt'])
        self.assertFalse(replay['model']['canSelect'])
        self.assertEqual([],self.store.list_models(10000)['models'])
        self.assertEqual(1,len(self.store.list_models(10000,True)['models']))
        self.assert_events(['asset.created','asset.delete'])

    def test_restore_wins_against_public_retry_and_restores_private_only(self):
        model_id = self.upload(visibility='public')['model']['id']
        self.store.mutate(10000,model_id,'delete',self.key())
        retry_path = self.prepared()
        restore_key, retry_key = self.key(), self.key()
        restored, upload = self.ordered(
            lambda:self.store.mutate(10000,model_id,'restore',restore_key),
            lambda:self.store.ingest(10000,'model.ysm',retry_path,retry_key,'public'))
        self.assertTrue(restored['ok'])
        self.assert_failure(upload,'model_exists_change_visibility')
        state = self.store.list_models(10000)['models'][0]
        self.assertEqual('private',state['visibility'])
        self.assertIsNone(state['deletedAt'])
        self.assertEqual([],self.store.public_models(10001)['models'])
        self.assert_events(['asset.created','asset.delete','asset.restore'])

    def deleted_eligible_model(self):
        own_id, public_id = self.verified_public()
        self.store.mutate(10000,own_id,'delete',self.key())
        self.now += self.store.retention_seconds + 1
        self.assertEqual(1,len(self.store.purge_candidates()))
        return own_id

    def test_restore_commits_before_fixture_purge_preserving_blob_and_reference(self):
        model_id = self.deleted_eligible_model()
        restore_key = self.key()
        restored, purge = self.ordered(lambda:self.store.mutate(10000,model_id,'restore',restore_key),
                                       lambda:self.store.purge_unreferenced(SHA,approved=True))
        self.assertTrue(restored['ok'])
        self.assert_failure(purge,'content_still_referenced_or_retained')
        state = self.store.list_models(10000)['models'][0]
        self.assertEqual('private',state['visibility'])
        self.assertEqual(DATA,self.store.download(10000,model_id)[0].read_bytes())
        self.assertEqual([],self.store.purge_candidates())
        self.assert_events(['asset.created','content.verified','asset.delete','asset.restore'])

    def test_fixture_purge_commits_before_restore_and_restore_cannot_recreate_rows(self):
        model_id = self.deleted_eligible_model()
        restore_key = self.key()
        purge, restored = self.ordered(lambda:self.store.purge_unreferenced(SHA,approved=True),
                                       lambda:self.store.mutate(10000,model_id,'restore',restore_key))
        self.assertTrue(purge['ok'])
        self.assert_failure(restored,'model_not_found',404)
        self.assertEqual([],self.rows('ysm_assets'))
        self.assertEqual([],self.rows('ysm_contents'))
        self.assertFalse(self.store._path(SHA,True).exists())
        self.assertFalse(self.store._path(SHA).exists())
        self.assertEqual([],list(self.store.incoming.glob('purge-*.bin')))
        self.assert_events(['asset.created','content.verified','asset.delete','content.purged'])

    def test_old_trash_cleanup_cannot_remove_same_sha_reupload_after_purge_commit(self):
        self.deleted_eligible_model()
        path = self.prepared()
        upload_key = self.key()
        at_trash_cleanup, allow_cleanup = threading.Event(), threading.Event()
        original_unlink = Path.unlink
        def fixture_unlink(file_path, *args, **kwargs):
            if file_path.parent == self.store.incoming and file_path.name.startswith('purge-'):
                at_trash_cleanup.set()
                if not allow_cleanup.wait(TIMEOUT):
                    raise TimeoutError('Fixture did not release committed trash cleanup')
            return original_unlink(file_path,*args,**kwargs)
        with patch.object(Path,'unlink',fixture_unlink), ThreadPoolExecutor(max_workers=2) as pool:
            purge = pool.submit(self.store.purge_unreferenced,SHA,approved=True)
            try:
                self.assertTrue(at_trash_cleanup.wait(TIMEOUT))
                # The purge transaction has committed but its old trash is still
                # present. This competing real transaction creates a new blob.
                upload = pool.submit(self.store.ingest,10000,'model.ysm',path,upload_key)
                created = upload.result(TIMEOUT)
                self.assertIsNone(created['model']['deletedAt'])
                self.assertEqual('pending',created['model']['validationStatus'])
            finally:
                allow_cleanup.set()
            purge.result(TIMEOUT)
        self.assertEqual(DATA,self.store._path(SHA).read_bytes())
        self.assertEqual(1,len(self.rows('ysm_assets')))
        self.assertEqual(1,len(self.rows('ysm_contents')))
        self.assert_events(['asset.created','content.verified','asset.delete','content.purged','asset.created'])

    def test_old_ack_queued_behind_revocation_cannot_restore_public_rights(self):
        own_id, public_id = self.verified_public()
        self.store.select(10001,public_id,self.key())
        revision = self.store.bridge_snapshot()['revision']
        private_key = self.key()
        revoked, old_ack = self.ordered(lambda:self.store.mutate(10000,own_id,'visibility',private_key,'private'),
                                       lambda:self.store.mark_synced(SHA,revision,'stale-acl',active=True))
        self.assertTrue(revoked['ok'])
        self.assert_failure(old_ack,'stale_permission_revision')
        self.assertIsNone(self.store.list_models(10001)['selectedModelId'])
        self.assertEqual([],self.store.public_models(10001)['models'])
        self.assertEqual('pending',self.store.list_models(10000)['models'][0]['syncStatus'])
        events = self.assert_events(['asset.created','content.verified','selection.changed','asset.visibility'])
        self.assertEqual('private',events[-1]['visibility'])
        self.assertEqual('10000',events[-1]['uid'])
        self.assertEqual(own_id,events[-1]['modelId'])

    def test_ack_before_revocation_is_superseded_by_permission_transaction(self):
        own_id, public_id = self.verified_public()
        self.store.select(10001,public_id,self.key())
        revision = self.store.bridge_snapshot()['revision']
        private_key = self.key()
        acknowledged, revoked = self.ordered(lambda:self.store.mark_synced(SHA,revision,'old-acl',active=True),
                                             lambda:self.store.mutate(10000,own_id,'visibility',private_key,'private'))
        self.assertTrue(acknowledged['ok'])
        self.assertTrue(revoked['ok'])
        self.assertIsNone(self.store.list_models(10001)['selectedModelId'])
        self.assertEqual('pending',self.store.list_models(10000)['models'][0]['syncStatus'])
        self.assertFalse(self.store.bridge_snapshot()['models'][0]['public'])
        self.assert_events(['asset.created','content.verified','selection.changed','asset.visibility'])

    def test_reader_sees_consistent_old_snapshot_until_permissions_and_event_commit(self):
        own_id, public_id = self.verified_public()
        self.store.select(10001,public_id,self.key())
        before = self.store.bridge_snapshot()
        before_event, allow_event = threading.Event(), threading.Event()
        original_event = self.store._event
        def pause_event(db, kind, **payload):
            if kind == 'asset.visibility':
                before_event.set()
                if not allow_event.wait(TIMEOUT):
                    raise TimeoutError('Fixture did not release transaction event')
            return original_event(db,kind,**payload)
        with patch.object(self.store,'_event',pause_event), ThreadPoolExecutor(max_workers=1) as pool:
            mutation = pool.submit(self.store.mutate,10000,own_id,'visibility',self.key(),'private')
            try:
                self.assertTrue(before_event.wait(TIMEOUT))
                # No snapshot read connection is retained while we release/join
                # the writer, avoiding a test-induced reader/writer deadlock.
                during = self.store.bridge_snapshot()
                self.assertEqual(before,during)
            finally:
                allow_event.set()
            mutation.result(TIMEOUT)
        after = self.store.bridge_snapshot()
        self.assertEqual(before['revision']+1,after['revision'])
        self.assertFalse(after['models'][0]['public'])
        self.assertEqual([],after['selections'])
        self.assert_events(['asset.created','content.verified','selection.changed','asset.visibility'])

    def test_failed_revocation_transaction_rolls_back_selection_and_outbox_atomically(self):
        own_id, public_id = self.verified_public()
        self.store.select(10001,public_id,self.key())
        before = self.store.bridge_snapshot()
        events = self.store.events_after(0)
        original_event = self.store._event
        def fail_event(db, kind, **payload):
            if kind == 'asset.visibility':
                raise RuntimeError('synthetic adapter event failure')
            return original_event(db,kind,**payload)
        with patch.object(self.store,'_event',fail_event):
            with self.assertRaisesRegex(RuntimeError,'synthetic adapter event failure'):
                self.store.mutate(10000,own_id,'visibility',self.key(),'private')
        self.assertEqual(before,self.store.bridge_snapshot())
        self.assertEqual(events,self.store.events_after(0))
        self.assertEqual(public_id,self.store.list_models(10001)['selectedModelId'])
        self.assertEqual(DATA,self.store.download(10001,public_id)[0].read_bytes())

    def test_failed_verification_event_preserves_quarantine_for_waiting_retry(self):
        original_id = self.upload()['model']['id']
        retry_path, retry_key = self.prepared(), self.key('retry')
        receipt = ValidationReceipt(SHA,len(DATA),PROFILE,self.key('validator'),True)
        original_event = self.store._event
        def fail_verification_event(db, kind, **payload):
            if kind == 'content.verified':
                raise RuntimeError('synthetic verification event failure')
            return original_event(db,kind,**payload)
        def verify_and_observe_failure():
            try:
                self.store.promote_verified(receipt)
            except RuntimeError as error:
                return str(error)
            self.fail('Injected verification event failure was unexpectedly ignored')
        with patch.object(self.store,'_event',fail_verification_event):
            verification, retry = self.ordered(verify_and_observe_failure,
                lambda:self.store.ingest(10000,'model.ysm',retry_path,retry_key))
        self.assertEqual('synthetic verification event failure',verification['data'])
        self.assertTrue(retry['ok'])
        self.assertEqual(original_id,retry['data']['model']['id'])
        self.assertEqual('pending',retry['data']['model']['validationStatus'])
        self.assert_events(['asset.created'])
        candidate = self.store.pending_validation()[0]
        retry_source = self.store.root / candidate['storageKey']
        self.assertTrue(retry_source.is_file(),
            'SQLite rolled back verification but the advertised quarantine bytes disappeared')
        self.assertEqual(DATA,retry_source.read_bytes())
        self.assertEqual([],self.store.bridge_snapshot()['models'])


if __name__ == '__main__':
    unittest.main()
