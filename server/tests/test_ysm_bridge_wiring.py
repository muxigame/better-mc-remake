"""Actual Store / machine HTTP / inactive mirror / actual Core contract wiring.

Only synthetic opaque fixture bytes are used. No model parsing, native, MC or
production requests. All output and temporary state stay under Web/build.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.ysm_assets import ValidationReceipt, YsmAssetStore

WEB = Path(__file__).resolve().parents[2]
WORKSPACE = WEB.parent
LAB = WEB / "build" / "owner-131-ysm-bridge-wiring"
PROFILE = "synthetic-wire-validator-only-v1"
DATA = b"Opaque synthetic wire fixture; not an actual YSM format."


def make_verified(store, visibility="private", uid="10000", name="wire.ysm", key="fixture-upload-1"):
    lease, upload = store.begin_upload(uid)
    try:
        upload.write_bytes(DATA)
        result = store.ingest(uid, name, upload, key, visibility)
    finally:
        store.end_upload(uid, lease, upload)
    store.promote_verified(ValidationReceipt(hashlib.sha256(DATA).hexdigest(), len(DATA),
                                           PROFILE, "fixture-receipt-1", True))
    return result["model"]


HARNESS = r"""
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import net.muxigame.core.feature.ysmassets.*;
public final class WireCoreContract {
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static void rejected(Runnable action){try{action.run();throw new AssertionError("Expected rejection");}catch(IllegalArgumentException expected){}}
    public static void main(String[] args)throws Exception{
        String json=Files.readString(Path.of(args[0]));
        var snapshot=YsmAssetSnapshot.parseTrusted(json);
        var content=snapshot.contents().values().iterator().next();
        var reference=content.references().get(0);
        var listener=new Object();
        var identity=new AtomicReference<>(new YsmAssetCoordinator.Identity(reference.ownerUid(),UUID.randomUUID(),listener));
        YsmAssetCoordinator.Connection owner=identity::get;
        var observerIdentity=new YsmAssetCoordinator.Identity("10001",UUID.randomUUID(),new Object());
        YsmAssetCoordinator.Connection observer=()->observerIdentity;
        var coordinator=new YsmAssetCoordinator(()->true);
        String generation="g_00000000000000000000000000000000";
        var receipt=coordinator.stage(snapshot,generation);
        check(!receipt.active(),"Staged receipt must be inactive");
        check(coordinator.list(owner).stream().anyMatch(p->p.modelId().equals(reference.modelId())),"Owner asset absent");
        check(coordinator.list(owner).stream().noneMatch(YsmAssetCoordinator.Projection::canSelect),"Default runtime must deny");
        check(coordinator.list(observer).isEmpty(),"Private asset exposed");
        check(!coordinator.canReceivePrivateRenderBytes(observer,reference.modelId(),snapshot.revision()),"Private render bytes exposed");
        rejected(()->coordinator.downloadPermit(owner,reference.modelId(),snapshot.revision()));
        rejected(()->coordinator.select(owner,reference.modelId(),snapshot.revision(),"fixture-use-key-1"));
        rejected(()->coordinator.stage(snapshot,"g_11111111111111111111111111111111"));
        check(!coordinator.metadata(owner,reference.modelId(),snapshot.revision()).canSelect(),"Metadata granted use");
        // New runtime remains disabled after changing a current connection identity.
        identity.set(new YsmAssetCoordinator.Identity("10001",UUID.randomUUID(),new Object()));
        rejected(()->coordinator.metadata(owner,reference.modelId(),snapshot.revision()));
        coordinator.close();
        System.out.println("actualCoreContractPassed=true active=false privateObserverVisible=0");
    }
}
"""


class ActualCoreContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        LAB.mkdir(parents=True, exist_ok=True)
        suffix = ".exe" if os.name == "nt" else ""
        homes = []
        if os.environ.get("YSM_TEST_JAVA_HOME"):
            homes.append(Path(os.environ["YSM_TEST_JAVA_HOME"]))
        if os.environ.get("JAVA_HOME"):
            homes.append(Path(os.environ["JAVA_HOME"]))
        portable = WEB / "build/owner-131-tools/temurin21"
        if portable.exists():
            homes.append(portable)
            homes.extend(p for p in portable.iterdir() if p.is_dir())
            expanded = portable / "expanded"
            if expanded.exists():
                homes.extend(p for p in expanded.iterdir() if p.is_dir())
        for base in (Path("C:/Program Files/Java"), Path("C:/Program Files/Eclipse Adoptium"),
                     Path("C:/Program Files/Microsoft")):
            if base.exists():
                homes.extend(sorted(base.iterdir(), reverse=True))
        if shutil.which("javac"):
            homes.append(Path(shutil.which("javac")).resolve().parent.parent)
        selected = None
        for home in homes:
            compiler, runtime = home / ("bin/javac" + suffix), home / ("bin/java" + suffix)
            if not compiler.is_file() or not runtime.is_file():
                continue
            version = subprocess.run([str(compiler), "-version"], capture_output=True, text=True, timeout=15)
            import re
            match = re.search(r"javac (\d+)", version.stdout + version.stderr)
            if version.returncode == 0 and match and int(match[1]) >= 21:
                selected = (compiler, runtime)
                break
        if selected is None:
            raise AssertionError("Actual Core compilation requires JDK 21+: configure YSM_TEST_JAVA_HOME or verified Web/build portable runtime")
        gson = WORKSPACE / "bmc5server/libraries/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar"
        if not gson.is_file():
            raise AssertionError("Existing pinned Gson 2.10.1 compile dependency is missing")
        cls.compiler, cls.runtime = selected
        cls.temp = tempfile.TemporaryDirectory(dir=LAB)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)
        harness = cls.output / "WireCoreContract.java"
        harness.write_text(HARNESS, encoding="utf-8")
        core = WORKSPACE / "muxi-game-core/src/main/java/net/muxigame/core/feature/ysmassets"
        cls.sources = [core / "YsmAssetSnapshot.java", core / "YsmAssetCoordinator.java"]
        for source in cls.sources:
            if not source.is_file():
                raise AssertionError("Registered actual Core protocol source is missing: " + str(source))
        cls.hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in cls.sources}
        cls.classpath = str(cls.output) + os.pathsep + str(gson)
        command = [str(cls.compiler), "--release", "21", "-proc:none", "-encoding", "UTF-8",
                   "-cp", str(gson), "-d", str(cls.output), *(str(p) for p in cls.sources), str(harness)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (LAB / "actual-core-build.txt").write_text(built.stdout + built.stderr, encoding="utf-8")
        if built.returncode:
            raise AssertionError("Actual Core parser/coordinator failed compilation: " + built.stderr)
        if cls.hashes != {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in cls.sources}:
            raise AssertionError("Other-owner Core source changed during contract compilation")

    def test_actual_wire_dto_parsed_by_actual_core_with_runtime_disabled(self):
        store = YsmAssetStore(self.output / "synthetic-backend", validation_profile=PROFILE)
        make_verified(store)
        app = FastAPI()
        app.include_router(ysm_game_router(store, lambda request, purpose: GameMachine("fixture-server")))
        with TestClient(app) as client:
            response = client.post(PREFIX + "/snapshot", json={})
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertFalse(payload["active"])
        self.assertFalse(payload["selectEnabled"])
        wire = self.output / "trusted-snapshot.json"
        wire.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run([str(self.runtime), "-cp", self.classpath, "WireCoreContract", str(wire)],
                                capture_output=True, text=True, timeout=30)
        (LAB / "actual-core-runtime.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("actualCoreContractPassed=true active=false", result.stdout)
        (LAB / "actual-core-inputs.json").write_text(json.dumps({
            "sourceHashes": self.hashes, "runtime": str(self.runtime),
            "snapshotSha256": hashlib.sha256(wire.read_bytes()).hexdigest(),
            "syntheticValidatorOnly": True, "mcStarted": False, "productionChanged": False,
        }, indent=2), encoding="utf-8")


from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.ysm_asset_bridge import AssetBridge, BridgeRejected, StaleRevision
from app.ysm_game_api import GameConnection, GameMachine, ysm_game_router

PREFIX = "/api/internal/game/ysm"


class Mirror:
    """Tests-only wire consumer. Does not parse, import or activate models."""
    def __init__(self, client, machine_headers, connection, root):
        self.client, self.headers, self.connection = client, machine_headers, connection
        self.root = root
        self.snapshot = self._snapshot()
        self.revision = self.snapshot["revision"]
        verified = root / "verified"
        verified.mkdir(parents=True)
        for content in self.snapshot["models"]:
            response = self.client.post(PREFIX + "/blob", headers=self.headers, json={
                "connection": self.connection, "revision": self.revision,
                "modelId": content["contentId"],
            })
            if response.status_code != 200:
                raise BridgeRejected("Wire blob denied: " + str(response.status_code))
            if (hashlib.sha256(response.content).hexdigest() != content["sha256"]
                    or len(response.content) != content["size"]
                    or response.headers.get("x-muxi-sha256") != content["sha256"]
                    or response.headers.get("content-length") != str(content["size"])):
                raise BridgeRejected("Wire integrity mismatch")
            if content["storageKey"] != "verified/" + content["sha256"] + ".bin":
                raise BridgeRejected("Wire storage key mismatch")
            (verified / (content["sha256"] + ".bin")).write_bytes(response.content)

    def _snapshot(self):
        response = self.client.post(PREFIX + "/snapshot", headers=self.headers, json={})
        if response.status_code != 200:
            raise BridgeRejected("Wire snapshot denied")
        return response.json()

    def bridge_snapshot(self):
        return self._snapshot()

    def events_after(self, seq):
        events = []
        while True:
            response = self.client.post(PREFIX + "/snapshot", headers=self.headers, json={
                "afterSeq": seq, "limit": 2, "expectedRevision": self.revision,
            })
            if response.status_code == 409:
                raise StaleRevision("Wire revision changed")
            if response.status_code != 200:
                raise BridgeRejected("Wire events denied")
            page = response.json()
            events.extend(page["events"])
            if not page["hasMore"]:
                return events
            if page["nextSeq"] <= seq:
                raise BridgeRejected("Wire cursor did not advance")
            seq = page["nextSeq"]


class WireIntegrationTests(unittest.TestCase):
    def setUp(self):
        LAB.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=LAB)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = YsmAssetStore(self.root / "backend", validation_profile=PROFILE)
        self.model = make_verified(self.store)
        self.machine_headers = {"X-Test-Machine": "synthetic-fixture"}
        self.connection = GameConnection("fixture-server", "fixture-connection", "binding-1", "10000")
        self.resolver_calls = 0
        self.purposes = []
        self.client = self.make_client(self.authorize, self.resolve)
        self.addCleanup(self.client.close)

    def authorize(self, request, purpose):
        self.purposes.append(purpose)
        if request.headers.get("X-Test-Machine") != "synthetic-fixture":
            raise HTTPException(403, "fixture_machine_denied")
        return GameMachine("fixture-server")

    def resolve(self, machine, claim):
        self.resolver_calls += 1
        return self.connection

    def make_client(self, authorizer=None, resolver=None):
        app = FastAPI()
        app.include_router(ysm_game_router(self.store, authorizer, resolver))
        return TestClient(app)

    def claim(self, **changes):
        claim = {"serverId": "fixture-server", "connectionId": "fixture-connection",
                 "bindingGeneration": "binding-1", "uid": "10000"}
        claim.update(changes)
        return claim

    def snapshot(self, **extra):
        response = self.client.post(PREFIX + "/snapshot", headers=self.machine_headers, json=extra)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def blob(self, *, claim=None, revision=None, model_id=None):
        return self.client.post(PREFIX + "/blob", headers=self.machine_headers, json={
            "connection": claim or self.claim(),
            "revision": self.snapshot()["revision"] if revision is None else revision,
            "modelId": self.model["id"] if model_id is None else model_id,
        })

    def test_default_disabled_even_with_user_bearer_and_cookie(self):
        with self.make_client() as client:
            for route in ["snapshot", "catalog", "blob", "staged"]:
                response = client.post(PREFIX + "/" + route, headers={"Authorization": "Bearer synthetic-user",
                    "Cookie": "synthetic-user-session"}, json={})
                self.assertEqual(503, response.status_code, route)
                self.assertNotIn(DATA.decode(), response.text)

    def test_user_bearer_never_authorizes_machine_hook(self):
        response = self.client.post(PREFIX + "/snapshot", headers={"Authorization": "Bearer synthetic-user"}, json={})
        self.assertEqual(403, response.status_code)
        self.assertEqual("sync", self.purposes[-1])

    def test_real_store_wire_to_mirror_stage_is_inactive_and_ack_not_ready(self):
        mirror = Mirror(self.client, self.machine_headers, self.claim(), self.root / "remote")
        bridge = AssetBridge(mirror, source_root=mirror.root, target=self.root / "staged",
                             allowed_stage_root=self.root)
        result = bridge.run(dry_run=False)
        content = mirror.snapshot["models"][0]
        self.assertFalse(result["active"])
        self.assertFalse(result["selectEnabled"])
        copied = self.root / "staged" / result["generation"] / (content["sha256"] + ".blob")
        self.assertEqual(DATA, copied.read_bytes())
        response = self.client.post(PREFIX + "/staged", headers=self.machine_headers, json={
            "contentId": content["contentId"], "sha256": content["sha256"], "revision": mirror.revision,
            "generation": result["generation"], "status": "staged",
        })
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(response.json()["active"])
        self.assertEqual("staged", response.json()["syncStatus"])
        self.assertFalse(self.store.list_models("10000")["models"][0]["canSelect"])
        self.assertTrue(self.resolver_calls >= 2)

    def test_connection_uid_is_resolved_not_selected_by_body(self):
        response = self.blob(claim=self.claim(uid="10001"))
        self.assertEqual(403, response.status_code)
        self.assertEqual("uid_claim_mismatch", response.json()["detail"])

    def test_disconnected_and_old_binding_denied_without_bytes(self):
        self.connection = GameConnection("fixture-server", "fixture-connection", "binding-2", "10000")
        response = self.blob()
        self.assertEqual(409, response.status_code)
        self.connection = GameConnection("fixture-server", "fixture-connection", "binding-1", "10000", live=False)
        response = self.blob()
        self.assertEqual(409, response.status_code)
        self.assertNotEqual(DATA, response.content)

    def test_rebind_during_blob_read_is_rejected_after_io(self):
        original = self.store.game_blob
        def rebinding(*args):
            result = original(*args)
            self.connection = GameConnection("fixture-server", "fixture-connection", "binding-2", "10000")
            return result
        with patch.object(self.store, "game_blob", side_effect=rebinding):
            response = self.blob()
        self.assertEqual(409, response.status_code)
        self.assertNotEqual(DATA, response.content)
        # A current trusted binding works; the old claim remains invalid.
        response = self.blob(claim=self.claim(bindingGeneration="binding-2"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(409, self.blob().status_code)

    def test_private_other_uid_cannot_catalog_or_download_or_hash_probe(self):
        self.connection = GameConnection("fixture-server", "fixture-connection", "binding-1", "10001")
        claim = self.claim(uid="10001")
        snapshot = self.snapshot()
        catalog = self.client.post(PREFIX + "/catalog", headers=self.machine_headers,
                                   json={"connection": claim, "revision": snapshot["revision"]})
        self.assertEqual(200, catalog.status_code)
        self.assertEqual([], catalog.json()["models"])
        for handle in [self.model["id"], snapshot["models"][0]["contentId"], hashlib.sha256(DATA).hexdigest()]:
            response = self.blob(claim=claim, model_id=handle)
            self.assertEqual(404, response.status_code)
            self.assertEqual("model_not_found", response.json()["detail"])

    def test_unknown_format_is_quarantined_and_not_exported(self):
        lease, upload = self.store.begin_upload("10000")
        try:
            upload.write_bytes(b"not validated unknown bytes")
            unknown = self.store.ingest("10000", "unknown.ysm", upload, "fixture-unknown-1")["model"]
        finally:
            self.store.end_upload("10000", lease, upload)
        snapshot = self.snapshot()
        self.assertEqual(1, len(snapshot["models"]))
        response = self.blob(model_id=unknown["id"])
        self.assertEqual(404, response.status_code)

    def test_public_content_id_does_not_grant_other_owners_logical_reference(self):
        self.store.mutate("10000", self.model["id"], "visibility", "fixture-public-handle-1", "public")
        content = self.snapshot()["models"][0]["contentId"]
        self.connection = GameConnection("fixture-server", "fixture-connection", "binding-1", "10001")
        claim = self.claim(uid="10001")
        self.assertEqual(200, self.blob(claim=claim, model_id=content).status_code)
        response = self.blob(claim=claim, model_id=self.model["id"])
        self.assertEqual(404, response.status_code)
        self.assertEqual("model_not_found", response.json()["detail"])

    def test_outbox_pages_are_complete_monotonic_and_revision_fenced(self):
        self.store.mutate("10000", self.model["id"], "visibility", "fixture-public-1", "public")
        self.store.mutate("10000", self.model["id"], "visibility", "fixture-private-1", "private")
        snapshot = self.snapshot(limit=1)
        revision, cursor = snapshot["revision"], 0
        observed = []
        while True:
            page = self.snapshot(afterSeq=cursor, limit=1, expectedRevision=revision)
            self.assertEqual(revision, page["revision"])
            self.assertFalse(page["active"])
            observed.extend(event["seq"] for event in page["events"])
            if not page["hasMore"]:
                break
            self.assertGreater(page["nextSeq"], cursor)
            cursor = page["nextSeq"]
        self.assertEqual([event["seq"] for event in self.store.events_after(0)], observed)
        self.assertEqual(sorted(set(observed)), observed)
        self.store.mutate("10000", self.model["id"], "visibility", "fixture-public-2", "public")
        response = self.client.post(PREFIX + "/snapshot", headers=self.machine_headers,
                                    json={"afterSeq": cursor, "limit": 1, "expectedRevision": revision})
        self.assertEqual(409, response.status_code)

    def test_revocation_after_blob_io_never_releases_buffered_public_bytes(self):
        self.store.mutate("10000", self.model["id"], "visibility", "fixture-public-3", "public")
        snapshot = self.snapshot()
        content = snapshot["models"][0]["contentId"]
        self.connection = GameConnection("fixture-server", "fixture-connection", "binding-1", "10001")
        original = self.store.game_blob
        def revoke(*args):
            result = original(*args)
            self.store.mutate("10000", self.model["id"], "visibility", "fixture-private-3", "private")
            return result
        with patch.object(self.store, "game_blob", side_effect=revoke):
            response = self.blob(claim=self.claim(uid="10001"), revision=snapshot["revision"], model_id=content)
        self.assertEqual(409, response.status_code)
        self.assertNotEqual(DATA, response.content)

    def test_mirror_stage_rejects_stale_revision_and_old_ack(self):
        mirror = Mirror(self.client, self.machine_headers, self.claim(), self.root / "remote")
        content = mirror.snapshot["models"][0]
        self.store.mutate("10000", self.model["id"], "delete", "fixture-delete-4")
        bridge = AssetBridge(mirror, source_root=mirror.root, target=self.root / "staged",
                             allowed_stage_root=self.root)
        with self.assertRaises(StaleRevision):
            bridge.run(dry_run=False)
        self.assertFalse((self.root / "staged" / "staged.json").exists())
        response = self.client.post(PREFIX + "/staged", headers=self.machine_headers, json={
            "contentId": content["contentId"], "sha256": content["sha256"], "revision": mirror.revision,
            "generation": "g_" + "0" * 32, "status": "staged",
        })
        self.assertEqual(409, response.status_code)
        self.assertEqual("stale_permission_revision", response.json()["detail"])

    def test_no_ready_or_failed_receipt_can_activate(self):
        snapshot = self.snapshot()
        content = snapshot["models"][0]
        base = {"contentId": content["contentId"], "sha256": content["sha256"],
                "revision": snapshot["revision"], "generation": "g_" + "0" * 32}
        for status in ["ready", "failed", "active"]:
            response = self.client.post(PREFIX + "/staged", headers=self.machine_headers, json={**base, "status": status})
            self.assertEqual(400, response.status_code)
        response = self.client.post(PREFIX + "/staged", headers=self.machine_headers,
                                    json={**base, "status": "staged", "active": True})
        self.assertEqual(400, response.status_code)
        self.assertFalse(self.store.list_models("10000")["models"][0]["canSelect"])

    def test_corrupt_verified_storage_returns_failure_not_bytes(self):
        digest = hashlib.sha256(DATA).hexdigest()
        (self.store.blobs / (digest + ".bin")).write_bytes(b"x" * len(DATA))
        response = self.blob()
        self.assertEqual(409, response.status_code)
        self.assertEqual("storage_integrity_failure", response.json()["detail"])

    def test_async_authority_hooks_work_without_changing_contract(self):
        async def machine(request, purpose):
            return self.authorize(request, purpose)
        async def resolver(machine, claim):
            return self.resolve(machine, claim)
        with self.make_client(machine, resolver) as client:
            response = client.post(PREFIX + "/catalog", headers=self.machine_headers,
                                   json={"connection": self.claim(), "revision": self.snapshot()["revision"]})
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["active"])
