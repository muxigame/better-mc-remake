"""Quarantine-only YSM bridge. Staging never grants runtime model authority."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8 host: Asia/Shanghai has no current DST.
    ZoneInfo = None


class BridgeRejected(ValueError):
    pass


class StaleRevision(BridgeRejected):
    pass


def next_run(now: datetime, at: str = "03:00", zone: str = "Asia/Shanghai") -> datetime:
    """Next strictly future wall-clock run; reject nonexistent DST times."""
    if now.tzinfo is None or not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", at):
        raise BridgeRejected("An aware timestamp and HH:MM are required")
    hour, minute = map(int, at.split(":"))
    if hour > 23:
        raise BridgeRejected("Invalid hour")
    try:
        if ZoneInfo is None:
            raise KeyError(zone)
        tz = ZoneInfo(zone)
    except KeyError:
        if zone != "Asia/Shanghai" or now.year < 1992:
            raise BridgeRejected("Install IANA tzdata for this timezone/date")
        tz = timezone(timedelta(hours=8), "Asia/Shanghai")
    local = now.astimezone(tz)
    for offset in range(4):
        day = local.date() + timedelta(days=offset)
        candidate = datetime.combine(day, time(hour, minute), tzinfo=tz)
        # fold=0 gives one run at an ambiguous time; a nonexistent time is skipped.
        if candidate.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue
        if candidate.astimezone(timezone.utc) > now.astimezone(timezone.utc):
            return candidate
    raise BridgeRejected("No valid scheduled time")


def _plain_path(path: Path) -> Path:
    path = Path(os.path.abspath(str(path)))
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise BridgeRejected("Symlinks and junctions are not allowed")
        # Python 3.8 on Windows does not expose is_junction.
        if item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & 0x400:
            raise BridgeRejected("Reparse points are not allowed")
    return path


def _inside(path: Path, root: Path) -> Path:
    path, root = _plain_path(path), _plain_path(root)
    try:
        path.relative_to(root)
    except ValueError:
        raise BridgeRejected("Path escapes the registered root")
    return path


def _identifier(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9_-]{1,96}", value):
        raise BridgeRejected("Invalid opaque identifier")
    return value


def _uid(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{4,15}", value):
        raise BridgeRejected("Only trusted stable MuxiUID strings are accepted")
    return value


def _atomic_json(path: Path, value: dict) -> None:
    _plain_path(path)
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AssetBridge:
    """Consumes only the trusted internal store; all files remain non-active.

    source_root is the backend verified-blob root. allowed_stage_root must be a
    separately authorized quarantine root, never a live YSM scan directory.
    """
    MAX_BLOB_BYTES = 64 * 1024 * 1024
    RESERVED = {"custom", "auth", "public", "yes_steve_model", "yesstevemodel", "ysm"}

    def __init__(self, store, *, source_root: Path, target: Path,
                 allowed_stage_root: Path, forbidden_roots=()):
        self.store = store
        self.source_root = _plain_path(Path(source_root))
        self.allowed_stage_root = _plain_path(Path(allowed_stage_root))
        self.target = _inside(Path(target), self.allowed_stage_root)
        if self.target == self.allowed_stage_root:
            raise BridgeRejected("A dedicated child quarantine directory is required")
        if any(part.lower() in self.RESERVED for part in self.target.parts):
            raise BridgeRejected("Live/public YSM roots are forbidden")
        for root in forbidden_roots:
            root = _plain_path(Path(root))
            if self.target == root or root in self.target.parents or self.target in root.parents:
                raise BridgeRejected("Target overlaps a live model root")
        if self.source_root == self.target or self.source_root in self.target.parents or self.target in self.source_root.parents:
            raise BridgeRejected("Source and quarantine roots must be separate")

    def _snapshot(self):
        value = self.store.bridge_snapshot()
        if not isinstance(value, dict) or type(value.get("revision")) is not int or value["revision"] < 0:
            raise BridgeRejected("Invalid bridge revision")
        if not isinstance(value.get("models"), list) or not isinstance(value.get("selections", []), list):
            raise BridgeRejected("Invalid bridge snapshot")
        return value

    def _validate(self, snapshot):
        models, seen = [], set()
        for entry in snapshot["models"]:
            content = _identifier(entry.get("contentId"), "p_")
            digest = entry.get("sha256")
            if content in seen or not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
                raise BridgeRejected("Duplicate content or invalid SHA256")
            seen.add(content)
            if not entry.get("validationProfile") or not entry.get("validationReceiptId"):
                raise BridgeRejected("A trusted validator receipt is required")
            if entry.get("validationStatus", "verified") != "verified":
                raise BridgeRejected("Unverified model cannot be staged")
            size = entry.get("size")
            if type(size) is not int or not 0 < size <= self.MAX_BLOB_BYTES:
                raise BridgeRejected("Invalid model size")
            if type(entry.get("public")) is not bool:
                raise BridgeRejected("Explicit visibility is required")
            owners = sorted({_uid(v) for v in entry.get("ownerUids", [])})
            if not owners:
                raise BridgeRejected("Owner reference required")
            ids = sorted({_identifier(v, "m_") for v in entry.get("modelIds", [])})
            if not ids:
                raise BridgeRejected("Logical asset references required")
            refs = entry.get("references")
            if not isinstance(refs, list) or not refs:
                raise BridgeRejected("Exact owner-to-asset reference mapping is required")
            references = []
            for ref in refs:
                if ref.get("visibility") not in ("private", "public"):
                    raise BridgeRejected("Invalid reference visibility")
                references.append({"modelId": _identifier(ref.get("modelId"), "m_"),
                                   "ownerUid": _uid(ref.get("ownerUid")),
                                   "visibility": ref["visibility"]})
            if (sorted({ref["modelId"] for ref in references}) != ids
                    or sorted({ref["ownerUid"] for ref in references}) != owners
                    or len(references) != len(ids)
                    or any(ref["visibility"] == "public" for ref in references) != entry["public"]):
                raise BridgeRejected("Aggregate permissions disagree with asset references")
            original = entry.get("storageKey")
            if not isinstance(original, str) or ".." in Path(original).parts:
                raise BridgeRejected("Invalid verified blob path")
            if original != "verified/" + digest + ".bin":
                raise BridgeRejected("Invalid verified storage key")
            source = Path(original)
            if not source.is_absolute():
                source = self.source_root / source
            source = _inside(source, self.source_root)
            if not source.is_file() or source.stat().st_size != size:
                raise BridgeRejected("Verified blob missing or size mismatch")
            h = hashlib.sha256()
            with source.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            if h.hexdigest() != digest:
                raise BridgeRejected("Verified blob hash mismatch")
            models.append({"contentId": content, "sha256": digest, "size": size,
                           "source": source, "public": entry["public"], "ownerUids": owners,
                           "modelIds": ids, "references": references,
                           "validationProfile": entry["validationProfile"],
                           "validationReceiptId": entry["validationReceiptId"]})
        return models

    def _require_revision(self, revision):
        if self._snapshot()["revision"] != revision:
            raise StaleRevision("Permission revision changed during staging")

    def run(self, *, after_seq=0, dry_run=True):
        if type(after_seq) is not int or after_seq < 0:
            raise BridgeRejected("Invalid outbox cursor")
        snapshot = self._snapshot()
        revision = snapshot["revision"]
        events = self.store.events_after(after_seq)
        if not isinstance(events, list) or any(type(e.get("seq")) is not int or e["seq"] <= after_seq for e in events):
            raise BridgeRejected("Invalid outbox")
        models = self._validate(snapshot)
        indexed = {model["contentId"]: model for model in models}
        selections = []
        for selection in snapshot.get("selections", []):
            uid = _uid(selection.get("uid"))
            content = indexed.get(selection.get("contentId"))
            model_id = selection.get("modelId")
            if content is None or not ((content["public"] and model_id == content["contentId"])
                    or any(ref["ownerUid"] == uid and ref["modelId"] == model_id for ref in content["references"])):
                raise BridgeRejected("Selection is not authorized by current asset references")
            selections.append({"uid": uid, "modelId": model_id, "contentId": content["contentId"]})
        tasks = [{"kind": "reconcile_permissions", "revision": revision,
                  "fallback": "builtin_default", "requireTrustedConnection": True,
                  "cachedBytesGrantAccess": False}]
        for event in events:
            if event.get("revision", revision) > revision:
                raise StaleRevision("Outbox is ahead of snapshot")
            tasks.append({"kind": "reconcile_asset_event", "seq": event["seq"],
                          "type": str(event.get("type", "")), "uid": event.get("uid"),
                          "modelId": event.get("modelId"), "revision": revision,
                          "fallbackIfRevoked": "builtin_default"})
        self._require_revision(revision)
        result = {"revision": revision, "dryRun": dry_run, "active": False,
                  "syncStatus": "pending_runtime_integration",
                  "modelCount": len(models), "lastSeq": max([after_seq] + [e["seq"] for e in events]),
                  "tasks": tasks, "desiredSelections": selections, "selectEnabled": False}
        if dry_run:
            return result
        _plain_path(self.target)
        self.target.mkdir(parents=True, exist_ok=True)
        generation = "g_" + uuid.uuid4().hex
        folder = self.target / generation
        folder.mkdir()
        try:
            entries = []
            for model in models:
                _plain_path(model["source"])
                destination = folder / (model["sha256"] + ".blob")
                if not destination.exists():
                    with model["source"].open("rb") as incoming, destination.open("xb") as outgoing:
                        copied = 0
                        h = hashlib.sha256()
                        while True:
                            block = incoming.read(1024 * 1024)
                            if not block:
                                break
                            copied += len(block)
                            if copied > model["size"]:
                                raise BridgeRejected("Blob grew during copy")
                            h.update(block)
                            outgoing.write(block)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                    if copied != model["size"] or h.hexdigest() != model["sha256"]:
                        raise BridgeRejected("Blob changed during copy")
                entries.append({k: v for k, v in model.items() if k != "source"})
            self._require_revision(revision)
            manifest = dict(result, generation=generation, models=entries)
            _atomic_json(folder / "permissions.json", manifest)
            self._require_revision(revision)
            _atomic_json(self.target / "staged.json", manifest)
            try:
                self._require_revision(revision)
            except StaleRevision:
                # A published quarantine pointer still grants no authority.
                _atomic_json(self.target / "staged.json",
                             {"revision": self._snapshot()["revision"], "active": False,
                              "selectEnabled": False, "models": [], "invalidated": True})
                raise
            return dict(result, generation=generation)
        except Exception:
            # Own newly-created generation only; no live or historical assets deleted.
            shutil.rmtree(folder)
            raise

    def acknowledge_activation(self, *, revision, generation):
        self._require_revision(revision)
        raise BridgeRejected("Runtime import, per-UID visibility/download/use and wear fallback are unverified; activation disabled")
