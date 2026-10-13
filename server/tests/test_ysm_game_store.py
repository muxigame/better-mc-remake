"""Connection-bound game store fixtures; no native parser or live MC is implied."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.ysm_assets import AssetError, ValidationReceipt, YsmAssetStore


TEST_ROOT = Path(__file__).resolve().parents[2] / 'build' / 'owner-131-ysm-game-store'
TEST_ROOT.mkdir(parents=True, exist_ok=True)
DATA = b'opaque game-store fixture, not a production YSM model\x00'
SHA = hashlib.sha256(DATA).hexdigest()
PROFILE = 'synthetic-game-store-validator-v1'
TIMEOUT = 10


class YsmGameStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.store = YsmAssetStore(Path(self.tmp.name), validation_profile=PROFILE)
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def key(self):
        self.counter += 1
        return 'synthetic-game-store-'+str(self.counter)

    def upload(self, uid=10000, visibility='private', data=DATA, name='model.ysm'):
        lease, path = self.store.begin_upload(uid)
        try:
            path.write_bytes(data)
            return self.store.ingest(uid,name,path,self.key(),visibility)['model']['id']
        finally:
            self.store.end_upload(uid,lease,path)

    def promote(self, data=DATA):
        self.store.promote_verified(ValidationReceipt(hashlib.sha256(data).hexdigest(),len(data),PROFILE,self.key(),True))

    def revision(self):
        return self.store.bridge_snapshot()['revision']

    def content_id(self):
        return self.store.bridge_snapshot()['models'][0]['contentId']

    def error(self, status, code, method, *args, **kwargs):
        with self.assertRaises(AssetError) as raised:
            method(*args,**kwargs)
        self.assertEqual(status,raised.exception.status)
        self.assertEqual(code,raised.exception.code)

    def wal_fixture(self):
        # WAL allows an actual writer commit while the independent reader holds
        # its pinned snapshot. Without this fixture a test that waits for writer
        # commit while holding a SQLite rollback-journal read lock would deadlock.
        db = sqlite3.connect(self.store.database,isolation_level=None)
        try:
            self.assertEqual('wal',db.execute('PRAGMA journal_mode=WAL').fetchone()[0])
        finally:
            db.close()

    def test_empty_bundle_has_explicit_nonactivation_schema(self):
        bundle = self.store.bridge_bundle()
        self.assertEqual({'revision':0,'models':[],'selections':[],'active':False,'selectEnabled':False,
                          'schemaVersion':1,'events':[],'nextSeq':0,'hasMore':False},bundle)

    def test_bundle_contains_complete_snapshot_and_paginated_events(self):
        own = self.upload()
        self.promote()
        self.store.mutate(10000,own,'visibility',self.key(),'public')
        first = self.store.bridge_bundle(limit=1)
        self.assertEqual(3,first['revision'])
        self.assertEqual(1,len(first['models']))
        self.assertEqual(['asset.created'],[event['type'] for event in first['events']])
        self.assertTrue(first['hasMore'])
        self.assertEqual(1,first['nextSeq'])
        second = self.store.bridge_bundle(first['nextSeq'],1,first['revision'])
        self.assertEqual(['content.verified'],[event['type'] for event in second['events']])
        third = self.store.bridge_bundle(second['nextSeq'],1,second['revision'])
        self.assertEqual(['asset.visibility'],[event['type'] for event in third['events']])
        self.assertFalse(third['hasMore'])
        tail = self.store.bridge_bundle(third['nextSeq'],1,third['revision'])
        self.assertEqual([],tail['events'])
        self.assertEqual(3,tail['nextSeq'])
        self.assertEqual(first['models'],tail['models'])

    def test_bundle_rejects_future_malformed_cursor_limit_and_stale_revision(self):
        self.upload()
        for cursor in (-1,True,False,1.0,'0',None,2,2**64):
            self.error(400,'invalid_event_cursor',self.store.bridge_bundle,cursor)
        for limit in (0,-1,True,1.0,'1',501,None):
            self.error(400,'invalid_event_limit',self.store.bridge_bundle,0,limit)
        for revision in (0,2,True,'1',1.0):
            self.error(409,'stale_permission_revision',self.store.bridge_bundle,0,100,revision)

    def test_bundle_omits_pending_rejected_deleted_contents_but_preserves_events(self):
        pending = self.upload(visibility='public',data=b'unverified fixture')
        rejected = self.upload(data=b'rejected fixture')
        self.promote(b'rejected fixture')
        self.store.reject_content(hashlib.sha256(b'rejected fixture').hexdigest())
        deleted = self.upload()
        self.promote()
        self.store.mutate(10000,deleted,'delete',self.key())
        bundle = self.store.bridge_bundle()
        self.assertEqual([],bundle['models'])
        self.assertEqual(7,len(bundle['events']))
        self.assertFalse(bundle['active'])

    def test_bundle_and_events_do_not_tear_across_actual_wal_writer_commit(self):
        own = self.upload(visibility='public')
        self.promote()
        self.wal_fixture()
        original = self.store._snapshot_tx
        snapshot_built, release = threading.Event(),threading.Event()
        def paused(db, uid=None):
            snapshot = original(db,uid)
            snapshot_built.set()
            if not release.wait(TIMEOUT):
                raise TimeoutError('Fixture did not release bundle snapshot')
            return snapshot
        with patch.object(self.store,'_snapshot_tx',paused),ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(self.store.bridge_bundle)
            try:
                self.assertTrue(snapshot_built.wait(TIMEOUT))
                writer = pool.submit(self.store.mutate,10000,own,'visibility',self.key(),'private')
                writer.result(TIMEOUT)  # Real committed revision 3 during read tx.
            finally:
                release.set()
            old = reader.result(TIMEOUT)
        self.assertEqual(2,old['revision'])
        self.assertTrue(old['models'][0]['public'])
        self.assertEqual(['asset.created','content.verified'],[event['type'] for event in old['events']])
        self.assertEqual(2,old['nextSeq'])
        fresh = self.store.bridge_bundle(old['nextSeq'])
        self.assertEqual(3,fresh['revision'])
        self.assertFalse(fresh['models'][0]['public'])
        self.assertEqual(['asset.visibility'],[event['type'] for event in fresh['events']])

    def test_game_catalog_filters_shared_sha_private_refs_before_aggregating(self):
        first = self.upload(10000)
        public = self.upload(10001,'public')
        hidden = self.upload(10002)
        self.promote()
        revision = self.revision()
        stranger = self.store.game_catalog(10003,revision)
        model = stranger['models'][0]
        self.assertEqual(['10001'],model['ownerUids'])
        self.assertEqual([public],model['modelIds'])
        self.assertEqual([{'modelId':public,'ownerUid':'10001','visibility':'public'}],model['references'])
        self.assertNotIn(hidden,str(stranger))
        self.assertNotIn(first,str(stranger))
        own = self.store.game_catalog(10000,revision)['models'][0]
        self.assertEqual({'10000','10001'},set(own['ownerUids']))
        self.assertEqual({first,public},set(own['modelIds']))
        self.assertNotIn('10002',own['ownerUids'])
        self.assertTrue(all(reference['ownerUid'] != '10002' for reference in own['references']))
        self.assertEqual('verified/'+SHA+'.bin',own['storageKey'])
        self.assertFalse(stranger['active'])
        self.assertFalse(stranger['selectEnabled'])

    def test_game_catalog_only_own_private_content_and_own_selections(self):
        first = self.upload(10000)
        second = self.upload(10001)
        self.promote()
        self.store.mark_synced(SHA,self.revision(),'synthetic-acl-only',active=True)
        self.store.select(10000,first,self.key())
        self.store.select(10001,second,self.key())
        one = self.store.game_catalog(10000,self.revision())
        two = self.store.game_catalog(10001,self.revision())
        none = self.store.game_catalog(10002,self.revision())
        self.assertEqual([first],one['models'][0]['modelIds'])
        self.assertEqual(['10000'],one['models'][0]['ownerUids'])
        self.assertEqual([{'uid':'10000','modelId':first,'contentId':self.content_id()}],one['selections'])
        self.assertEqual([second],two['models'][0]['modelIds'])
        self.assertEqual('10001',two['selections'][0]['uid'])
        self.assertEqual([],none['models'])
        self.assertEqual([],none['selections'])

    def test_game_catalog_rejects_stale_revision_and_invalid_uid(self):
        own = self.upload()
        self.promote()
        old_revision = self.revision()
        self.store.mutate(10000,own,'visibility',self.key(),'public')
        self.error(409,'stale_permission_revision',self.store.game_catalog,10000,old_revision)
        for revision in (True,str(self.revision()),float(self.revision()),-1):
            self.error(409,'stale_permission_revision',self.store.game_catalog,10000,revision)
        self.error(403,'invalid_account_uid',self.store.game_catalog,True,self.revision())

    def test_game_catalog_keeps_revision_and_filtered_references_in_same_read_tx(self):
        own = self.upload(visibility='public')
        self.promote()
        self.wal_fixture()
        original = self.store._snapshot_tx
        before_filter, release = threading.Event(),threading.Event()
        def paused(db, uid=None):
            # The entry method has already checked/pinned revision. Delay the
            # actual permission query until after a real independent commit.
            before_filter.set()
            if not release.wait(TIMEOUT):
                raise TimeoutError('Fixture did not release catalog query')
            return original(db,uid)
        revision = self.revision()
        with patch.object(self.store,'_snapshot_tx',paused),ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(self.store.game_catalog,10001,revision)
            try:
                self.assertTrue(before_filter.wait(TIMEOUT))
                pool.submit(self.store.mutate,10000,own,'visibility',self.key(),'private').result(TIMEOUT)
            finally:
                release.set()
            old = reader.result(TIMEOUT)
        self.assertEqual(revision,old['revision'])
        self.assertEqual(1,len(old['models']))
        self.error(409,'stale_permission_revision',self.store.game_catalog,10001,revision)
        self.assertEqual([],self.store.game_catalog(10001,self.revision())['models'])

    def test_private_blob_authorizes_both_owned_logical_id_and_owned_content_id(self):
        own = self.upload()
        self.promote()
        for model_id in (own,self.content_id()):
            self.assertEqual((DATA,SHA,len(DATA)),self.store.game_blob(10000,model_id,self.revision()))
            self.error(404,'model_not_found',self.store.game_blob,10001,model_id,self.revision())
        self.error(404,'model_not_found',self.store.game_blob,10001,'p_'+'0'*32,self.revision())

    def test_public_blob_is_authorized_but_another_private_reference_id_stays_hidden(self):
        public = self.upload(10000,'public')
        hidden = self.upload(10001)
        self.promote()
        self.assertEqual((DATA,SHA,len(DATA)),self.store.game_blob(10002,self.content_id(),self.revision()))
        self.error(404,'model_not_found',self.store.game_blob,10002,public,self.revision())
        self.error(404,'model_not_found',self.store.game_blob,10002,hidden,self.revision())

    def test_blob_never_uses_bare_hash_or_unverified_file_as_authority(self):
        own = self.upload(visibility='public')
        with self.store._db() as db:
            content_id = db.execute('SELECT public_id FROM ysm_contents WHERE sha256=?',(SHA,)).fetchone()[0]
        # Even an orphan verified-directory copy from a rolled-back validation
        # cannot substitute for the committed validation state.
        self.store._path(SHA,True).write_bytes(DATA)
        for model_id in (own,content_id,SHA,True,None):
            self.error(404,'model_not_found',self.store.game_blob,10000,model_id,self.revision())

    def test_blob_rejects_current_revision_after_revocation_and_old_revision_is_stale(self):
        own = self.upload(visibility='public')
        self.promote()
        public_id, old_revision = self.content_id(),self.revision()
        self.store.mutate(10000,own,'visibility',self.key(),'private')
        self.error(409,'stale_permission_revision',self.store.game_blob,10001,public_id,old_revision)
        self.error(404,'model_not_found',self.store.game_blob,10001,public_id,self.revision())
        self.assertEqual((DATA,SHA,len(DATA)),self.store.game_blob(10000,public_id,self.revision()))
        self.store.mutate(10000,own,'delete',self.key())
        self.error(404,'model_not_found',self.store.game_blob,10000,public_id,self.revision())

    def test_blob_checks_digest_size_and_missing_storage_before_returning_bytes(self):
        own = self.upload()
        self.promote()
        path = self.store._path(SHA,True)
        for changed in (DATA[:-1],DATA+b'extra','X'.encode()*len(DATA)):
            path.write_bytes(changed)
            self.error(409,'storage_integrity_failure',self.store.game_blob,10000,own,self.revision())
        path.unlink()
        self.error(409,'storage_unavailable',self.store.game_blob,10000,own,self.revision())

    def test_blob_read_is_bounded_and_remains_inside_the_authorization_transaction(self):
        own = self.upload()
        self.promote()
        path = self.store._path(SHA,True)
        original_db, original_open = self.store._db,Path.open
        live = []
        reads = []
        @contextmanager
        def observe_db(write=False):
            with original_db(write) as db:
                live.append(db)
                try:
                    yield db
                finally:
                    live.pop()
        class ObservedStream:
            def __init__(stream_self, stream):
                stream_self.stream = stream
            def __enter__(stream_self):
                return stream_self
            def __exit__(stream_self,*args):
                stream_self.stream.close()
            def read(stream_self, length):
                self.assertTrue(live and live[-1].in_transaction)
                reads.append(length)
                return stream_self.stream.read(length)
        def observe_open(file_path,*args,**kwargs):
            stream = original_open(file_path,*args,**kwargs)
            return ObservedStream(stream) if file_path == path else stream
        revision = self.revision()
        with patch.object(self.store,'_db',observe_db),patch.object(Path,'open',observe_open):
            result = self.store.game_blob(10000,own,revision)
        self.assertEqual((DATA,SHA,len(DATA)),result)
        self.assertEqual([len(DATA)+1],reads)

    def test_blob_metadata_above_configured_limit_is_rejected_before_open(self):
        own = self.upload()
        self.promote()
        limited = YsmAssetStore(self.store.root,max_file_bytes=len(DATA)-1)
        revision = self.revision()
        with patch.object(Path,'open',side_effect=AssertionError('Unbounded file was opened')):
            self.error(409,'storage_integrity_failure',limited.game_blob,10000,own,revision)

    def test_stage_receipt_matches_exact_content_and_never_enables_selection(self):
        own = self.upload()
        self.promote()
        content_id, revision = self.content_id(),self.revision()
        for _ in range(2):
            self.assertEqual({'revision':revision,'syncStatus':'staged','active':False},
                             self.store.stage_receipt(content_id,SHA,revision,'fixture-generation'))
        self.assertEqual('staged',self.store.list_models(10000)['models'][0]['syncStatus'])
        self.assertFalse(self.store.list_models(10000)['models'][0]['canSelect'])
        self.error(409,'model_not_active_on_server',self.store.select,10000,own,self.key())
        self.assertEqual(2,len(self.store.events_after(0)))
        with self.assertRaises(TypeError):
            self.store.stage_receipt(content_id,SHA,revision,'fixture-generation',active=True)

    def test_stage_receipt_rejects_forged_pairs_malformed_generation_and_stale_revision(self):
        self.upload()
        self.promote()
        content_id, revision = self.content_id(),self.revision()
        for cid, sha in ((content_id,'f'*64),('p_'+'0'*32,SHA),('m_'+'0'*32,SHA),(content_id,SHA.upper())):
            self.error(404,'model_not_found',self.store.stage_receipt,cid,sha,revision,'fixture-generation')
        for generation in ('','../path','a\n',True,None):
            self.error(400,'invalid_sync_receipt',self.store.stage_receipt,content_id,SHA,revision,generation)
        for old in (revision-1,revision+1,True,str(revision),float(revision)):
            self.error(409,'stale_permission_revision',self.store.stage_receipt,content_id,SHA,old,'fixture-generation')

    def test_stage_receipt_rejects_unverified_and_unreferenced_content(self):
        own = self.upload()
        with self.store._db() as db:
            content_id = db.execute('SELECT public_id FROM ysm_contents WHERE sha256=?',(SHA,)).fetchone()[0]
        self.error(409,'model_not_verified',self.store.stage_receipt,content_id,SHA,self.revision(),'fixture-generation')
        self.promote()
        self.store.mutate(10000,own,'delete',self.key())
        self.error(409,'content_has_no_active_reference',self.store.stage_receipt,content_id,SHA,self.revision(),'fixture-generation')
        self.assertFalse(self.store.list_models(10000,True)['models'][0]['canSelect'])

    def test_stage_receipt_cannot_keep_fixture_legacy_ready_state(self):
        own = self.upload()
        self.promote()
        content_id, revision = self.content_id(),self.revision()
        self.store.mark_synced(SHA,revision,'fixture-legacy-acl',active=True)
        self.assertTrue(self.store.list_models(10000)['models'][0]['canSelect'])
        self.store.stage_receipt(content_id,SHA,revision,'fixture-copy-only')
        self.assertFalse(self.store.list_models(10000)['models'][0]['canSelect'])

    def test_every_game_method_uses_one_database_transaction(self):
        own = self.upload()
        self.promote()
        content_id, revision = self.content_id(),self.revision()
        operations = (lambda:self.store.bridge_bundle(),lambda:self.store.game_catalog(10000,revision),
                      lambda:self.store.game_blob(10000,own,revision),
                      lambda:self.store.stage_receipt(content_id,SHA,revision,'fixture-generation'))
        for operation in operations:
            with patch.object(self.store,'_db',wraps=self.store._db) as observed:
                operation()
                self.assertEqual(1,observed.call_count)


if __name__ == '__main__':
    unittest.main()
