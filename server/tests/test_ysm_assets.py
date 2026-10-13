"""Security/transaction fixtures use synthetic opaque bytes, never real YSM assets."""
import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.ysm_assets import AssetError, ValidationReceipt, YsmAssetStore, safe_filename, trusted_uid


TEST_ROOT = Path(__file__).resolve().parents[2] / 'build' / 'owner-131-ysm-assets'
TEST_ROOT.mkdir(parents=True, exist_ok=True)
PROFILE = 'fixture-validator-only-v1'
DATA = b'opaque synthetic fixture; not a valid production YSM model\x00'


class YsmAssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.now = 1000000.0
        self.store = YsmAssetStore(Path(self.tmp.name), validation_profile=PROFILE, clock=lambda: self.now)
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def key(self):
        self.counter += 1
        return 'synthetic-key-' + str(self.counter)

    def upload(self, uid=10000, name='sample.ysm', data=DATA, value='private', key=None, store=None):
        store = store or self.store
        lease, path = store.begin_upload(uid)
        try:
            path.write_bytes(data)
            return store.ingest(uid, name, path, key or self.key(), value)
        finally:
            store.end_upload(uid, lease, path)

    def promote(self, data=DATA):
        return self.store.promote_verified(ValidationReceipt(hashlib.sha256(data).hexdigest(), len(data), PROFILE, self.key(), True))

    def activate(self, data=DATA):
        sha = hashlib.sha256(data).hexdigest()
        revision = self.store.bridge_snapshot()['revision']
        self.store.mark_synced(sha, revision, 'synthetic-import-acl-proof', active=True)

    def assert_error(self, code, fn, *args, **kwargs):
        with self.assertRaises(AssetError) as raised:
            fn(*args, **kwargs)
        self.assertEqual(code, raised.exception.status)
        return raised.exception.code

    def test_uid_cannot_be_boolean_display_name_or_noncanonical(self):
        for value in (True, False, 9999, 'player', '010000', '1e4', 10000.0, None, '10000/../../x'):
            self.assert_error(403, trusted_uid, value)
        self.assertEqual('10000', trusted_uid(10000))

    def test_filename_cannot_traverse_or_disguise_extensions(self):
        bad = ('../model.ysm', '..\\model.ysm', 'C:model.ysm', 'a%2f.ysm', '.hidden.ysm', 'a.ysm.exe',
               'a.exe.ysm', 'CON.ysm', 'NUL.part.ysm', 'a\x00.ysm', ' a.ysm', 'a.ysm ', '\uff0froot.ysm', 'foo.zip.ysm')
        for name in bad:
            self.assert_error(400, safe_filename, name)
        self.assertEqual('model.ysm', safe_filename('model.ysm'))

    def test_upload_defaults_private_pending_and_is_not_selectable_or_downloadable(self):
        model = self.upload()['model']
        self.assertEqual('private', model['visibility'])
        self.assertEqual('pending', model['validationStatus'])
        self.assertFalse(model['canDownload'])
        self.assertFalse(model['canSelect'])
        self.assertEqual([], self.store.public_models(10001)['models'])
        self.assertEqual([], self.store.bridge_snapshot()['models'])
        self.assert_error(409, self.store.download, 10000, model['id'])
        self.assert_error(409, self.store.select, 10000, model['id'], self.key())

    def test_public_intention_pending_never_appears_public(self):
        self.upload(value='public')
        self.assertEqual([], self.store.public_models(10001)['models'])
        content = self.store.pending_validation()[0]
        self.assertNotIn('path', content)
        self.assertTrue(content['storageKey'].startswith('quarantine/'))

    def test_default_validator_is_failclosed_and_attestation_binds_bytes(self):
        model = self.upload()['model']
        unconfigured = YsmAssetStore(self.store.root)
        receipt = ValidationReceipt(hashlib.sha256(DATA).hexdigest(), len(DATA), PROFILE, self.key(), True)
        self.assert_error(403, unconfigured.promote_verified, receipt)
        self.assert_error(403, self.store.promote_verified, ValidationReceipt(receipt.sha256, len(DATA), 'unknown-profile', self.key(), True))
        self.assert_error(409, self.store.promote_verified, ValidationReceipt(receipt.sha256, len(DATA)+1, PROFILE, self.key(), True))
        self.assertFalse(self.store.list_models(10000)['models'][0]['canDownload'])

    def test_private_model_all_ownership_operations_reject_other_uid(self):
        model = self.upload()['model']
        self.promote()
        self.activate()
        self.assertEqual([], self.store.list_models(10001)['models'])
        for action, value in (('visibility', 'public'), ('delete', None), ('restore', None)):
            self.assert_error(404, self.store.mutate, 10001, model['id'], action, self.key(), value)
        self.assert_error(404, self.store.download, 10001, model['id'])
        self.assert_error(404, self.store.select, 10001, model['id'], self.key())

    def test_private_content_has_no_hash_probe_or_public_reference(self):
        self.upload()
        self.promote()
        sha = hashlib.sha256(DATA).hexdigest()
        self.assert_error(404, self.store.download, 10001, sha)
        self.assertEqual([], self.store.public_models(10001)['models'])
        public = self.store.list_models(10000)['models'][0]
        self.assertNotIn('sha256', public)
        self.assertNotIn('ownerUid', public)

    def test_public_content_deduplicates_blobs_and_catalog_but_preserves_owner_references(self):
        one = self.upload(10000, value='public')['model']
        two = self.upload(10001, name='renamed.ysm', value='public')['model']
        self.assertNotEqual(one['id'], two['id'])
        self.assertEqual(1, len(list(self.store.quarantine.glob('*.bin'))))
        self.promote()
        self.activate()
        catalog = self.store.public_models(10002)['models']
        self.assertEqual(1, len(catalog))
        self.assertTrue(catalog[0]['id'].startswith('p_'))
        self.assertNotIn('sha256', catalog[0])
        self.assertEqual(DATA, self.store.download(10002, catalog[0]['id'])[0].read_bytes())

    def test_adding_new_reference_invalidates_previous_permission_ack(self):
        self.upload()
        self.promote()
        self.activate()
        self.assertTrue(self.store.list_models(10000)['models'][0]['canSelect'])
        self.upload(10001)
        self.assertFalse(self.store.list_models(10001)['models'][0]['canSelect'])

    def test_same_key_replays_and_cannot_change_content_name_or_visibility(self):
        key = self.key()
        one = self.upload(key=key)
        two = self.upload(key=key)
        self.assertEqual(one['model']['id'], two['model']['id'])
        self.assertTrue(two['replayed'])
        for kwargs in ({'name':'other.ysm'}, {'data':b'other'}, {'value':'public'}):
            self.assert_error(409, self.upload, key=key, **kwargs)
        self.assertEqual(1, len(self.store.list_models(10000)['models']))

    def test_public_hash_lookup_never_exposes_private_pending_or_unknown(self):
        private = self.upload()['model']
        sha = hashlib.sha256(DATA).hexdigest()
        self.assert_error(404, self.store.public_by_sha256, 10001, sha)
        self.store.mutate(10000, private['id'], 'visibility', self.key(), 'public')
        self.assert_error(404, self.store.public_by_sha256, 10001, sha)
        self.promote()
        self.assertTrue(self.store.public_by_sha256(10001, sha)['model']['id'].startswith('p_'))
        self.store.mutate(10000, private['id'], 'visibility', self.key(), 'private')
        self.assertEqual(self.assert_error(404, self.store.public_by_sha256, 10001, sha),
                         self.assert_error(404, self.store.public_by_sha256, 10001, 'f'*64))

    def test_public_reference_does_not_upload_bytes_and_is_owned_and_idempotent(self):
        self.upload(value='public')
        self.promote()
        public_id = self.store.public_models(10001)['models'][0]['id']
        key = self.key()
        created = self.store.create_reference(10001, public_id, 'saved.ysm', key)
        replay = self.store.create_reference(10001, public_id, 'saved.ysm', key)
        self.assertEqual(created['model']['id'], replay['model']['id'])
        self.assertTrue(replay['replayed'])
        self.assertEqual('private', created['model']['visibility'])
        self.assertEqual(1, len(list(self.store.blobs.glob('*.bin'))))
        self.assertEqual([], list(self.store.incoming.glob('*.bin')))
        self.assert_error(409, self.store.create_reference, 10001, public_id, 'changed.ysm', key)
        self.assert_error(404, self.store.download, 10002, created['model']['id'])

    def test_reference_deny_after_source_revoked_does_not_add_owner_or_asset(self):
        own = self.upload(value='public')['model']
        self.promote()
        public_id = self.store.public_models(10001)['models'][0]['id']
        self.store.mutate(10000, own['id'], 'visibility', self.key(), 'private')
        self.assert_error(404, self.store.create_reference, 10001, public_id, 'saved.ysm', self.key(), 'public')
        self.assertEqual([], self.store.list_models(10001)['models'])
        self.assertEqual(['10000'], self.store.bridge_snapshot()['models'][0]['ownerUids'])

    def test_concurrent_public_reference_and_revoke_never_grants_after_revocation(self):
        own = self.upload(value='public')['model']
        self.promote()
        public_id = self.store.public_models(10001)['models'][0]['id']
        # The actual SQLite lock forces revoke to commit before the competing
        # reference is authorized, proving this is not a cache-only decision.
        with self.store._db(True) as db:
            db.execute("UPDATE ysm_assets SET visibility='private' WHERE id=?", (own['id'],))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.store.create_reference, 10001, public_id, 'raced.ysm', self.key())
                db.commit()
                with self.assertRaises(AssetError) as raised:
                    future.result(timeout=10)
                self.assertEqual(404, raised.exception.status)
        self.assertEqual([], self.store.list_models(10001)['models'])

    def test_concurrent_same_uid_key_uploads_create_one_reference_and_blob(self):
        key = self.key()
        paths = []
        for number in range(8):
            path = self.store.incoming / f'synthetic-{number}.bin'
            path.write_bytes(DATA)
            paths.append(path)
        def submit(path):
            return self.store.ingest(10000, 'same.ysm', path, key)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, paths))
        self.assertEqual(1, len({r['model']['id'] for r in results}))
        self.assertEqual(1, sum(not r['replayed'] for r in results))
        self.assertEqual(1, len(self.store.list_models(10000)['models']))
        self.assertEqual(1, len(list(self.store.quarantine.glob('*.bin'))))

    def test_upload_size_empty_and_retained_soft_delete_quota(self):
        limited = YsmAssetStore(self.store.root/'limited', max_file_bytes=4, max_user_bytes=4, max_models=1)
        self.assert_error(400, self.upload, data=b'', store=limited)
        self.assert_error(413, self.upload, data=b'12345', store=limited)
        model = self.upload(data=b'1234', store=limited)['model']
        limited.mutate(10000, model['id'], 'delete', self.key())
        self.assert_error(413, self.upload, data=b'123', name='next.ysm', store=limited)

    def test_privacy_revokes_public_selection_and_stale_ack_cannot_regrant(self):
        own = self.upload(value='public')['model']
        self.promote()
        self.activate()
        public_id = self.store.public_models(10001)['models'][0]['id']
        key = self.key()
        self.store.select(10001, public_id, key)
        revision = self.store.bridge_snapshot()['revision']
        self.store.mutate(10000, own['id'], 'visibility', self.key(), 'private')
        self.assertEqual([], self.store.public_models(10001)['models'])
        self.assertIsNone(self.store.list_models(10001)['selectedModelId'])
        self.assert_error(404, self.store.download, 10001, public_id)
        self.assert_error(404, self.store.select, 10001, public_id, key)
        self.assert_error(409, self.store.mark_synced, hashlib.sha256(DATA).hexdigest(), revision, 'old-generation', active=True)

    def test_delete_revokes_selection_and_restore_is_private_and_not_automatic(self):
        model = self.upload(value='public')['model']
        self.promote()
        self.activate()
        self.store.select(10000, model['id'], self.key())
        deleted = self.store.mutate(10000, model['id'], 'delete', self.key())
        self.assertTrue(deleted['model']['canRestore'])
        self.assertIsNone(deleted['selectedModelId'])
        self.assertEqual([], self.store.list_models(10000)['models'])
        self.assertEqual(1, len(self.store.list_models(10000, True)['models']))
        self.assert_error(404, self.store.download, 10000, model['id'])
        self.assert_error(409, self.upload, value='public')
        restored = self.store.mutate(10000, model['id'], 'restore', self.key())['model']
        self.assertEqual('private', restored['visibility'])
        self.assertIsNone(restored['deletedAt'])
        self.assertFalse(restored['canSelect'])
        self.assertEqual([], self.store.public_models(10001)['models'])

    def test_last_reference_controls_cleanup_and_permanent_purge_requires_explicit_approval(self):
        first = self.upload()['model']
        second = self.upload(10001)['model']
        sha = hashlib.sha256(DATA).hexdigest()
        self.store.mutate(10000, first['id'], 'delete', self.key())
        self.now += self.store.retention_seconds + 1
        self.assertEqual([], self.store.purge_candidates())
        self.assert_error(409, self.store.purge_unreferenced, sha, approved=True)
        self.store.mutate(10001, second['id'], 'delete', self.key())
        self.assert_error(409, self.store.purge_unreferenced, sha, approved=True)
        self.now += self.store.retention_seconds + 1
        self.assertEqual(1, len(self.store.purge_candidates()))
        self.assert_error(403, self.store.purge_unreferenced, sha)
        # No automatic cleanup even after its earliest permitted time.
        self.assertTrue(self.store._path(sha).exists())
        self.store.purge_unreferenced(sha, approved=True)
        self.assertFalse(self.store._path(sha).exists())
        self.assert_error(404, self.store.mutate, 10000, first['id'], 'restore', self.key())

    def test_stage_is_not_runtime_activation(self):
        model = self.upload()['model']
        self.promote()
        sha = hashlib.sha256(DATA).hexdigest()
        self.store.mark_synced(sha, self.store.bridge_snapshot()['revision'], 'copied-only')
        state = self.store.list_models(10000)['models'][0]
        self.assertEqual('staged', state['syncStatus'])
        self.assertFalse(state['canSelect'])
        self.assert_error(409, self.store.select, 10000, model['id'], self.key())

    def test_restart_retains_assets_events_and_revocation(self):
        model = self.upload()['model']
        self.store.mutate(10000, model['id'], 'delete', self.key())
        reopened = YsmAssetStore(self.store.root)
        self.assertEqual(1, len(reopened.list_models(10000, True)['models']))
        events = reopened.events_after(0)
        self.assertEqual(['asset.created', 'asset.delete'], [r['type'] for r in events])
        self.assertEqual(sorted(r['seq'] for r in events), [r['seq'] for r in events])

    def test_bad_staging_path_is_never_consumed(self):
        outside = Path(self.tmp.name).parent/'outside.bin'
        self.assert_error(400, self.store.ingest, 10000, 'x.ysm', outside, self.key())


if __name__ == '__main__':
    unittest.main()
