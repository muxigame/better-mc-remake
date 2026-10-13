import hashlib
import io
import json
import os
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from app.ysm_asset_bridge import AssetBridge, BridgeRejected, StaleRevision, next_run

WEB = Path(__file__).resolve().parents[2]


class Store:
    def __init__(self, root):
        payload = b"Opaque already-validated fixture. Never parse a real model."
        (root / "verified").mkdir()
        digest = hashlib.sha256(payload).hexdigest()
        self.blob = root / "verified" / (digest + ".bin")
        self.blob.write_bytes(payload)
        self.revision = 1
        self.models = [{
            "contentId": "p_fixture", "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload), "storageKey": "verified/" + digest + ".bin", "public": False,
            "ownerUids": ["10000"], "modelIds": ["m_fixture"],
            "references": [{"modelId": "m_fixture", "ownerUid": "10000", "visibility": "private"}],
            "validationProfile": "test-fixture", "validationReceiptId": "fixture-receipt",
            "validationStatus": "verified",
        }]
        self.events = []
        self.calls = 0
        self.race_at = None
        self.synced = False

    def bridge_snapshot(self):
        self.calls += 1
        if self.calls == self.race_at:
            self.revision += 1
            self.models = []
        return {"revision": self.revision, "models": self.models,
                "selections": [{"uid": "10000", "modelId": "m_fixture", "contentId": "p_fixture"}] if self.models else []}

    def events_after(self, seq):
        return [e for e in self.events if e["seq"] > seq]

    def mark_synced(self, *args):
        self.synced = True


class BridgeTests(unittest.TestCase):
    def setUp(self):
        lab = WEB / "build" / "owner-131-ysm-bridge-tests"
        lab.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(lab))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "verified"
        self.source.mkdir()
        self.store = Store(self.source)
        self.target = self.root / "quarantine"
        self.bridge = AssetBridge(self.store, source_root=self.source, target=self.target,
                                  allowed_stage_root=self.root)

    def test_default_dry_run_never_writes_or_marks_synced(self):
        result = self.bridge.run()
        self.assertTrue(result["dryRun"])
        self.assertFalse(self.target.exists())
        self.assertFalse(result["selectEnabled"])
        self.assertFalse(self.store.synced)

    def test_verified_stage_has_exact_hash_and_private_permissions_but_no_authority(self):
        result = self.bridge.run(dry_run=False)
        manifest = json.loads((self.target / "staged.json").read_text())
        self.assertFalse(manifest["active"])
        self.assertFalse(manifest["models"][0]["public"])
        self.assertEqual(manifest["models"][0]["ownerUids"], ["10000"])
        digest = self.store.models[0]["sha256"]
        self.assertEqual(hashlib.sha256((self.target / result["generation"] / (digest + ".blob")).read_bytes()).hexdigest(), digest)
        self.assertFalse(self.store.synced)
        with self.assertRaises(BridgeRejected):
            self.bridge.acknowledge_activation(revision=1, generation=result["generation"])

    def test_hash_tamper_rejected(self):
        self.store.blob.write_bytes(b"x" * self.store.models[0]["size"])
        with self.assertRaises(BridgeRejected):
            self.bridge.run()

    def test_missing_validator_receipt_rejected(self):
        self.store.models[0]["validationReceiptId"] = ""
        with self.assertRaises(BridgeRejected):
            self.bridge.run()

    def test_owner_reference_mismatch_rejected(self):
        self.store.models[0]["references"][0]["ownerUid"] = "10001"
        with self.assertRaises(BridgeRejected):
            self.bridge.run()

    def test_private_cached_selection_by_other_uid_rejected(self):
        original = self.store.bridge_snapshot
        def forged_cached_selection():
            snapshot = original()
            snapshot["selections"][0]["uid"] = "10001"
            return snapshot
        with patch.object(self.store, "bridge_snapshot", side_effect=forged_cached_selection):
            with self.assertRaises(BridgeRejected):
                self.bridge.run()

    def test_unverified_rejected(self):
        self.store.models[0]["validationStatus"] = "pending"
        with self.assertRaises(BridgeRejected):
            self.bridge.run()

    def test_oversized_rejected_before_read(self):
        self.store.models[0]["size"] = self.bridge.MAX_BLOB_BYTES + 1
        with self.assertRaises(BridgeRejected):
            self.bridge.run()

    def test_path_escape_rejected(self):
        self.store.models[0]["storageKey"] = str(self.root / "outside.blob")
        with self.assertRaises(BridgeRejected):
            self.bridge.run()

    def test_relative_traversal_rejected(self):
        self.store.models[0]["storageKey"] = "../outside.blob"
        with self.assertRaises(BridgeRejected):
            self.bridge.run()

    def test_reserved_and_overlapping_target_rejected(self):
        for target in [self.root / "custom" / "users", self.source / "stage"]:
            with self.assertRaises(BridgeRejected):
                AssetBridge(self.store, source_root=self.source, target=target,
                            allowed_stage_root=self.root)
        with self.assertRaises(BridgeRejected):
            AssetBridge(self.store, source_root=self.source, target=self.target,
                        allowed_stage_root=self.root, forbidden_roots=[self.root])

    def test_symlink_source_rejected(self):
        linked = self.source / "original.blob"
        linked.write_bytes(self.store.blob.read_bytes())
        try:
            self.store.blob.unlink()
            self.store.blob.symlink_to(linked)
        except (OSError, NotImplementedError):
            self.skipTest("Host does not permit creating test symlinks")
        with self.assertRaises(BridgeRejected):
            self.bridge.run()

    def test_stale_during_copy_never_publishes(self):
        self.store.race_at = 3
        with self.assertRaises(StaleRevision):
            self.bridge.run(dry_run=False)
        self.assertFalse((self.target / "staged.json").exists())
        self.assertEqual(list(self.target.iterdir()), [])

    def test_revoke_after_pointer_publication_invalidates_manifest(self):
        self.store.race_at = 5
        with self.assertRaises(StaleRevision):
            self.bridge.run(dry_run=False)
        manifest = json.loads((self.target / "staged.json").read_text())
        self.assertTrue(manifest["invalidated"])
        self.assertEqual(manifest["models"], [])
        self.assertFalse(manifest["active"])

    def test_private_delete_events_output_fallback_without_ack(self):
        self.store.events = [{"seq": 1, "revision": 1, "type": "visibility",
                              "uid": "10000", "modelId": "m_fixture"},
                             {"seq": 2, "revision": 1, "type": "delete",
                              "uid": "10000", "modelId": "m_fixture"}]
        result = self.bridge.run()
        self.assertEqual(result["lastSeq"], 2)
        self.assertEqual(result["tasks"][2]["fallbackIfRevoked"], "builtin_default")
        self.assertFalse(self.store.synced)

    def test_stale_ack_rejected(self):
        self.store.revision = 2
        with self.assertRaises(StaleRevision):
            self.bridge.acknowledge_activation(revision=1, generation="g_fixture")

    def actual_store(self, visibility="private"):
        from app.ysm_assets import ValidationReceipt, YsmAssetStore
        payload = b"synthetic store integration fixture; never native/parsed"
        profile = "fixture-only-validator-v1"
        store = YsmAssetStore(self.root / "backend", validation_profile=profile)
        lease, upload = store.begin_upload(10000)
        try:
            upload.write_bytes(payload)
            model = store.ingest(10000, "fixture.ysm", upload, "fixture-upload-1", visibility)["model"]
        finally:
            store.end_upload(10000, lease, upload)
        store.promote_verified(ValidationReceipt(hashlib.sha256(payload).hexdigest(), len(payload),
                                               profile, "fixture-receipt-1", True))
        bridge = AssetBridge(store, source_root=store.root, target=self.target,
                             allowed_stage_root=self.root)
        return store, bridge, model

    def test_actual_store_storage_key_stage_keeps_selection_disabled(self):
        from app.ysm_assets import AssetError
        store, bridge, model = self.actual_store()
        result = bridge.run(dry_run=False)
        self.assertEqual(1, result["modelCount"])
        self.assertFalse(store.list_models(10000)["models"][0]["canSelect"])
        with self.assertRaises(AssetError) as caught:
            store.select(10000, model["id"], "fixture-select-1")
        self.assertEqual(409, caught.exception.status)
        self.assertEqual([], store.public_models(10001)["models"])

    def test_actual_sqlite_public_to_private_race_revokes_selection_and_old_ack(self):
        from app.ysm_assets import AssetError
        store, bridge, model = self.actual_store("public")
        snapshot = store.bridge_snapshot()
        sha = snapshot["models"][0]["sha256"]
        # Synthetic adapter proof ONLY; not production activation evidence.
        store.mark_synced(sha, snapshot["revision"], "fixture-only-import-proof", active=True)
        public = store.public_models(10001)["models"][0]["id"]
        store.select(10001, public, "fixture-select-2")
        original = store.bridge_snapshot
        revision = original()["revision"]
        calls = [0]
        def concurrent_snapshot():
            calls[0] += 1
            if calls[0] == 3:
                store.mutate(10000, model["id"], "visibility", "fixture-visibility-1", "private")
            return original()
        with patch.object(store, "bridge_snapshot", side_effect=concurrent_snapshot):
            with self.assertRaises(StaleRevision):
                bridge.run(dry_run=False)
        self.assertIsNone(store.list_models(10001)["selectedModelId"])
        self.assertEqual([], store.public_models(10001)["models"])
        self.assertFalse((self.target / "staged.json").exists())
        with self.assertRaises(AssetError) as caught:
            store.mark_synced(sha, revision, "stale-fixture-proof", active=True)
        self.assertEqual(409, caught.exception.status)

    def test_actual_sqlite_delete_during_stage_invalidates_snapshot(self):
        store, bridge, model = self.actual_store()
        original = store.bridge_snapshot
        calls = [0]
        def concurrent_snapshot():
            calls[0] += 1
            if calls[0] == 3:
                store.mutate(10000, model["id"], "delete", "fixture-delete-1")
            return original()
        with patch.object(store, "bridge_snapshot", side_effect=concurrent_snapshot):
            with self.assertRaises(StaleRevision):
                bridge.run(dry_run=False)
        self.assertEqual([], original()["models"])
        self.assertFalse((self.target / "staged.json").exists())

    def test_midnight_timezone_boundary(self):
        now = datetime(2026, 10, 13, 19, 0, tzinfo=timezone.utc)
        self.assertEqual(next_run(now).isoformat(), "2026-10-15T03:00:00+08:00")
        self.assertEqual(next_run(now, "00:00").isoformat(), "2026-10-15T00:00:00+08:00")

    def test_schedule_bad_input(self):
        for at in ["24:00", "3:00", "../"]:
            with self.assertRaises(BridgeRejected):
                next_run(datetime.now(timezone.utc), at)
        with self.assertRaises(BridgeRejected):
            next_run(datetime.now())

    def test_dst_gap_and_fold(self):
        try:
            from zoneinfo import ZoneInfo
        except ImportError as error:
            self.skipTest(str(error))
        # Real 2026 EST/EDT transition instants, bounded TZif fixture; no tzdata download.
        transitions = [int(datetime(2026, 3, 8, 7, tzinfo=timezone.utc).timestamp()),
                       int(datetime(2026, 11, 1, 6, tzinfo=timezone.utc).timestamp())]
        tzif = (b"TZif\0" + b"\0" * 15 + struct.pack(">6I", 0, 0, 0, 2, 2, 8)
                + struct.pack(">2i", *transitions) + b"\x01\x00"
                + struct.pack(">iBB", -18000, 0, 0) + struct.pack(">iBB", -14400, 1, 4)
                + b"EST\0EDT\0")
        zone = ZoneInfo.from_file(io.BytesIO(tzif), key="America/New_York")
        spring = datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)
        fall = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
        with patch("app.ysm_asset_bridge.ZoneInfo", return_value=zone):
            self.assertEqual(next_run(spring, "02:30", "America/New_York").day, 9)
            self.assertEqual(next_run(fall, "01:30", "America/New_York").fold, 0)

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_real_windows_junction_rejected(self):
        junction = self.root / "linked-verified"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(self.source)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode:
            self.skipTest("Host does not permit creating fixture junction")
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
        with self.assertRaises(BridgeRejected):
            AssetBridge(self.store, source_root=junction, target=self.target,
                        allowed_stage_root=self.root)
