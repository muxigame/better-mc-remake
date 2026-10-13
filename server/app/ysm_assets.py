"""UID-owned YSM assets, separate from the public YSM and music directories.

Opaque uploads are never parsed here. A locally configured, isolated validator
must attest to the exact bytes before a machine adapter may stage them. Staging
is not activation: only an actual import/permission receipt enables selection.
No method below is an HTTP validation, sync, or permanent-delete endpoint.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator


class AssetError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


def trusted_uid(value) -> str:
    """Only the already authenticated account may supply this value."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AssetError(403, 'invalid_account_uid')
    text = str(value)
    if not re.fullmatch(r'[1-9][0-9]{4,15}', text) or not 10000 <= int(text) <= 9999999999999999:
        raise AssetError(403, 'invalid_account_uid')
    return text


def safe_filename(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 120:
        raise AssetError(400, 'invalid_filename')
    try:
        if len(value.encode('utf-8')) > 240:
            raise AssetError(400, 'invalid_filename')
    except UnicodeError:
        raise AssetError(400, 'invalid_filename')
    canonical = unicodedata.normalize('NFKC', value)
    if (canonical != canonical.strip() or canonical.startswith('.') or '..' in canonical
            or any(c in canonical for c in '/\\:%')
            or any(unicodedata.category(c).startswith('C') for c in canonical)
            or not canonical.lower().endswith('.ysm')):
        raise AssetError(400, 'invalid_filename')
    stem = canonical[:-4]
    if not stem or stem.endswith(('.', ' ')) or re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', stem):
        raise AssetError(400, 'invalid_filename')
    if re.search(r'(?i)\.(exe|dll|com|bat|cmd|ps1|sh|js|html|php|jar|zip|7z|rar)$', stem):
        raise AssetError(400, 'disguised_extension')
    return unicodedata.normalize('NFC', value)


def visibility(value: str) -> str:
    if value not in ('private', 'public'):
        raise AssetError(400, 'invalid_visibility')
    return value


def request_key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}', value):
        raise AssetError(400, 'invalid_idempotency_key')
    return value


def utc_string(value: float | None):
    return None if value is None else datetime.fromtimestamp(value, timezone.utc).isoformat()


@dataclass(frozen=True)
class ValidationReceipt:
    """Issued by a trusted local adapter after out-of-process format validation.

    Strings from a client request are never accepted as an attestation. The
    deployment must explicitly configure the profile for its actual YSM version.
    This class is not a format checker or a claim that 2.6.5 is understood.
    """
    sha256: str
    size: int
    profile: str
    receipt_id: str
    passed: bool


class YsmAssetStore:
    MAX_FILE_BYTES = 64 * 1024 * 1024
    MAX_USER_BYTES = 256 * 1024 * 1024
    MAX_MODELS = 100
    RETENTION_SECONDS = 7 * 86400

    def __init__(self, root: Path, *, max_file_bytes: int = MAX_FILE_BYTES,
                 max_user_bytes: int = MAX_USER_BYTES, max_models: int = MAX_MODELS,
                 retention_seconds: int = RETENTION_SECONDS,
                 validation_profile: str | None = None, clock: Callable[[], float] = time.time):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.incoming = self.root / 'incoming'
        self.quarantine = self.root / 'quarantine'
        self.blobs = self.root / 'verified'
        for folder in (self.incoming, self.quarantine, self.blobs):
            folder.mkdir(exist_ok=True)
            if folder.is_symlink() or folder.resolve().parent != self.root:
                raise ValueError('YSM storage must not use redirected directories')
        self.database = self.root / 'assets.sqlite3'
        self.max_file_bytes, self.max_user_bytes, self.max_models = max_file_bytes, max_user_bytes, max_models
        self.retention_seconds = retention_seconds
        self.validation_profile, self.clock = validation_profile, clock
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ysm_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO ysm_meta VALUES('revision',0);
                CREATE TABLE IF NOT EXISTS ysm_contents(
                    sha256 TEXT PRIMARY KEY, public_id TEXT NOT NULL UNIQUE, size INTEGER NOT NULL,
                    verification TEXT NOT NULL DEFAULT 'pending', profile TEXT, receipt_id TEXT,
                    sync_status TEXT NOT NULL DEFAULT 'blocked', generation TEXT,
                    created_at REAL NOT NULL, unreferenced_at REAL);
                CREATE TABLE IF NOT EXISTS ysm_assets(
                    id TEXT PRIMARY KEY, uid TEXT NOT NULL, sha256 TEXT NOT NULL REFERENCES ysm_contents,
                    name TEXT NOT NULL, visibility TEXT NOT NULL, created_at REAL NOT NULL, deleted_at REAL,
                    UNIQUE(uid,sha256,name));
                CREATE INDEX IF NOT EXISTS ysm_assets_owner ON ysm_assets(uid,deleted_at);
                CREATE TABLE IF NOT EXISTS ysm_requests(
                    uid TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    asset_id TEXT, action TEXT NOT NULL, response TEXT,
                    PRIMARY KEY(uid,key));
                CREATE TABLE IF NOT EXISTS ysm_selections(
                    uid TEXT PRIMARY KEY, model_id TEXT NOT NULL, sha256 TEXT NOT NULL REFERENCES ysm_contents);
                CREATE TABLE IF NOT EXISTS ysm_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ysm_upload_leases(
                    id TEXT PRIMARY KEY, uid TEXT NOT NULL, expires_at REAL NOT NULL);
            ''')

    @contextmanager
    def _db(self, write=False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _revision(self, db):
        return db.execute("SELECT value FROM ysm_meta WHERE key='revision'").fetchone()[0]

    def _event(self, db, kind, **payload):
        db.execute("UPDATE ysm_meta SET value=value+1 WHERE key='revision'")
        revision = self._revision(db)
        db.execute('INSERT INTO ysm_events(revision,kind,payload,created_at) VALUES(?,?,?,?)',
                   (revision, kind, json.dumps(payload, sort_keys=True), self.clock()))
        return revision

    def _limits(self):
        return {'maxFileBytes': self.max_file_bytes, 'maxUserBytes': self.max_user_bytes,
                'maxModels': self.max_models}

    def begin_upload(self, uid) -> tuple[str, Path]:
        uid = trusted_uid(uid)
        lease = secrets.token_hex(16)
        with self._db(True) as db:
            # Expiry releases admission capacity, but never deletes someone else's
            # in-flight files. Size/time limits are independently enforced by HTTP.
            db.execute('DELETE FROM ysm_upload_leases WHERE expires_at<?', (self.clock(),))
            if db.execute('SELECT count(*) FROM ysm_upload_leases WHERE uid=?', (uid,)).fetchone()[0] >= 2:
                raise AssetError(429, 'too_many_uploads')
            if db.execute('SELECT count(*) FROM ysm_upload_leases').fetchone()[0] >= 16:
                raise AssetError(503, 'upload_capacity_exceeded')
            db.execute('INSERT INTO ysm_upload_leases VALUES(?,?,?)', (lease, uid, self.clock() + 300))
        try:
            fd, name = tempfile.mkstemp(prefix=lease + '-', suffix='.bin', dir=self.incoming)
            os.close(fd)
            return lease, Path(name)
        except BaseException:
            with self._db(True) as db:
                db.execute('DELETE FROM ysm_upload_leases WHERE id=?', (lease,))
            raise

    def end_upload(self, uid, lease: str, path: Path):
        uid = trusted_uid(uid)
        with self._db(True) as db:
            own = db.execute('SELECT 1 FROM ysm_upload_leases WHERE id=? AND uid=?', (lease, uid)).fetchone()
            if own:
                db.execute('DELETE FROM ysm_upload_leases WHERE id=? AND uid=?', (lease, uid))
        # Only the exact safe file of this upload may be removed.
        if path.parent == self.incoming and path.name.startswith(lease + '-') and not path.is_symlink():
            path.unlink(missing_ok=True)

    def _path(self, sha: str, verified: bool = False) -> Path:
        if not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise AssetError(400, 'invalid_content_id')
        path = (self.blobs if verified else self.quarantine) / (sha + '.bin')
        if path.is_symlink():
            raise AssetError(409, 'storage_integrity_failure')
        return path

    def _row(self, db, model_id, uid=None, include_deleted=False):
        row = db.execute('''SELECT a.*,c.public_id,c.size,c.verification,c.sync_status
                            FROM ysm_assets a JOIN ysm_contents c USING(sha256) WHERE a.id=?''', (model_id,)).fetchone()
        if row is None or (uid is not None and row['uid'] != uid) or (row['deleted_at'] is not None and not include_deleted):
            raise AssetError(404, 'model_not_found')
        return row

    def _model(self, row, *, public=False):
        active = row['deleted_at'] is None
        verified = row['verification'] == 'verified'
        return {'id': row['public_id'] if public else row['id'], 'name': row['name'], 'size': row['size'],
                'visibility': 'public' if public else row['visibility'],
                'validationStatus': row['verification'], 'syncStatus': row['sync_status'],
                'deletedAt': utc_string(row['deleted_at']),
                'canRestore': not active,
                'cleanupEligibleAt': utc_string(row['deleted_at'] + self.retention_seconds) if not active else None,
                'canDownload': active and verified,
                'canSelect': active and verified and row['sync_status'] == 'ready'}

    def _selection(self, db, uid):
        row = db.execute('SELECT model_id FROM ysm_selections WHERE uid=?', (uid,)).fetchone()
        return row[0] if row else None

    def list_models(self, uid, include_deleted=False):
        uid = trusted_uid(uid)
        with self._db() as db:
            rows = db.execute('''SELECT a.*,c.public_id,c.size,c.verification,c.sync_status FROM ysm_assets a
                JOIN ysm_contents c USING(sha256) WHERE a.uid=? AND (? OR a.deleted_at IS NULL)
                ORDER BY a.created_at DESC,a.id''', (uid, bool(include_deleted))).fetchall()
            return {'models': [self._model(row) for row in rows], 'selectedModelId': self._selection(db, uid),
                    'limits': self._limits(), 'revision': self._revision(db)}

    def public_models(self, uid):
        uid = trusted_uid(uid)
        with self._db() as db:
            rows = db.execute('''SELECT a.*,c.public_id,c.size,c.verification,c.sync_status FROM ysm_contents c
                JOIN ysm_assets a ON a.id=(SELECT id FROM ysm_assets WHERE sha256=c.sha256
                    AND visibility='public' AND deleted_at IS NULL ORDER BY created_at,id LIMIT 1)
                WHERE c.verification='verified' ORDER BY a.created_at DESC,c.public_id''').fetchall()
            return {'models': [self._model(row, public=True) for row in rows],
                    'selectedModelId': self._selection(db, uid), 'limits': self._limits(), 'revision': self._revision(db)}

    def public_by_sha256(self, uid, sha: str):
        trusted_uid(uid)
        # This is deliberately not a contents-table existence query. Private,
        # unknown and unverified bytes have the same response, including timing
        # query shape and error code. Only an actually published model is found.
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise AssetError(404, 'public_model_not_found')
        with self._db() as db:
            row = db.execute('''SELECT a.*,c.public_id,c.size,c.verification,c.sync_status FROM ysm_assets a
                JOIN ysm_contents c USING(sha256) WHERE a.sha256=? AND a.visibility='public'
                AND a.deleted_at IS NULL AND c.verification='verified' ORDER BY a.created_at,a.id LIMIT 1''', (sha,)).fetchone()
            if row is None:
                raise AssetError(404, 'public_model_not_found')
            return {'model': self._model(row, public=True)}

    def create_reference(self, uid, public_id: str, name: str, key: str, requested_visibility='private'):
        uid, name, key = trusted_uid(uid), safe_filename(name), request_key(key)
        requested_visibility = visibility(requested_visibility)
        if not isinstance(public_id, str) or not re.fullmatch(r'p_[0-9a-f]{32}', public_id):
            raise AssetError(404, 'public_model_not_found')
        fingerprint = self._fingerprint('reference', [public_id, name, requested_visibility])
        with self._db(True) as db:
            previous = db.execute('SELECT * FROM ysm_requests WHERE uid=? AND key=?', (uid, key)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise AssetError(409, 'idempotency_conflict')
                return {'model': self._model(self._row(db, previous['asset_id'], uid, True)), 'replayed': True}
            # Authority is rechecked under the same transaction that creates the
            # reference. A public-directory cache is never sufficient permission.
            content = db.execute('''SELECT c.* FROM ysm_contents c WHERE c.public_id=? AND c.verification='verified'
                AND EXISTS(SELECT 1 FROM ysm_assets a WHERE a.sha256=c.sha256
                    AND a.visibility='public' AND a.deleted_at IS NULL)''', (public_id,)).fetchone()
            if content is None:
                raise AssetError(404, 'public_model_not_found')
            sha = content['sha256']
            asset = db.execute('SELECT id,visibility,deleted_at FROM ysm_assets WHERE uid=? AND sha256=? AND name=?',
                               (uid, sha, name)).fetchone()
            if asset:
                if asset['deleted_at'] is not None:
                    raise AssetError(409, 'model_deleted_use_restore')
                if asset['visibility'] != requested_visibility:
                    raise AssetError(409, 'model_exists_change_visibility')
                model_id = asset['id']
            else:
                used = db.execute('''SELECT count(*),coalesce(sum(c.size),0) FROM ysm_assets a
                                      JOIN ysm_contents c USING(sha256) WHERE a.uid=?''', (uid,)).fetchone()
                if used[0] >= self.max_models or used[1] + content['size'] > self.max_user_bytes:
                    raise AssetError(413, 'user_storage_quota_exceeded')
                model_id = 'm_' + secrets.token_hex(16)
                db.execute('INSERT INTO ysm_assets VALUES(?,?,?,?,?,?,NULL)',
                           (model_id, uid, sha, name, requested_visibility, self.clock()))
                db.execute('UPDATE ysm_contents SET unreferenced_at=NULL WHERE sha256=?', (sha,))
                self._invalidate(db, sha)
                self._event(db, 'asset.created', uid=uid, modelId=model_id, sha256=sha)
            db.execute('INSERT INTO ysm_requests(uid,key,fingerprint,asset_id,action) VALUES(?,?,?,?,?)',
                       (uid, key, fingerprint, model_id, 'reference'))
            return {'model': self._model(self._row(db, model_id, uid)), 'replayed': False}

    @staticmethod
    def _fingerprint(action, values):
        return hashlib.sha256(json.dumps([action, values], ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def ingest(self, uid, name: str, path: Path, key: str, requested_visibility='private'):
        uid, name, key = trusted_uid(uid), safe_filename(name), request_key(key)
        requested_visibility = visibility(requested_visibility)
        path = Path(path)
        if path.is_symlink() or path.resolve().parent != self.incoming:
            raise AssetError(400, 'invalid_staging_file')
        digest, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > self.max_file_bytes:
                    raise AssetError(413, 'model_too_large')
                digest.update(chunk)
        if not size:
            raise AssetError(400, 'empty_model')
        sha = digest.hexdigest()
        fingerprint = self._fingerprint('upload', [name, sha, size, requested_visibility])
        with self._db(True) as db:
            previous = db.execute('SELECT * FROM ysm_requests WHERE uid=? AND key=?', (uid, key)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise AssetError(409, 'idempotency_conflict')
                return {'model': self._model(self._row(db, previous['asset_id'], uid, True)), 'replayed': True}
            asset = db.execute('SELECT id,visibility,deleted_at FROM ysm_assets WHERE uid=? AND sha256=? AND name=?',
                               (uid, sha, name)).fetchone()
            if asset:
                if asset['deleted_at'] is not None:
                    raise AssetError(409, 'model_deleted_use_restore')
                if asset['visibility'] != requested_visibility:
                    raise AssetError(409, 'model_exists_change_visibility')
                model_id = asset['id']
            else:
                used = db.execute('''SELECT count(*),coalesce(sum(c.size),0) FROM ysm_assets a
                                      JOIN ysm_contents c USING(sha256) WHERE a.uid=?''', (uid,)).fetchone()
                # Retained soft-deleted references count toward quota too.
                if used[0] >= self.max_models or used[1] + size > self.max_user_bytes:
                    raise AssetError(413, 'user_storage_quota_exceeded')
                content = db.execute('SELECT * FROM ysm_contents WHERE sha256=?', (sha,)).fetchone()
                if not content:
                    destination = self._path(sha)
                    if destination.exists():
                        # A crash can leave an unregistered blob; never trust bytes
                        # merely because their filename claims this digest.
                        if self._hash_file(destination) != (sha, size):
                            raise AssetError(409, 'storage_integrity_failure')
                    else:
                        os.replace(path, destination)
                    db.execute('INSERT INTO ysm_contents(sha256,public_id,size,created_at) VALUES(?,?,?,?)',
                               (sha, 'p_' + secrets.token_hex(16), size, self.clock()))
                elif content['size'] != size:
                    raise AssetError(409, 'storage_integrity_failure')
                model_id = 'm_' + secrets.token_hex(16)
                db.execute('INSERT INTO ysm_assets VALUES(?,?,?,?,?,?,NULL)',
                           (model_id, uid, sha, name, requested_visibility, self.clock()))
                db.execute('UPDATE ysm_contents SET unreferenced_at=NULL WHERE sha256=?', (sha,))
                self._invalidate(db, sha)
                self._event(db, 'asset.created', uid=uid, modelId=model_id, sha256=sha)
            db.execute('INSERT INTO ysm_requests(uid,key,fingerprint,asset_id,action) VALUES(?,?,?,?,?)',
                       (uid, key, fingerprint, model_id, 'upload'))
            return {'model': self._model(self._row(db, model_id, uid)), 'replayed': False}

    @staticmethod
    def _hash_file(path):
        digest, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def _invalidate(self, db, sha, *, deleted_model=None):
        # Revocation and the durable adapter event are committed together.
        if deleted_model:
            db.execute('DELETE FROM ysm_selections WHERE model_id=?', (deleted_model,))
        public = db.execute("SELECT 1 FROM ysm_assets WHERE sha256=? AND visibility='public' AND deleted_at IS NULL", (sha,)).fetchone()
        if not public:
            db.execute('DELETE FROM ysm_selections WHERE model_id=(SELECT public_id FROM ysm_contents WHERE sha256=?)', (sha,))
        db.execute("UPDATE ysm_contents SET sync_status=CASE WHEN verification='verified' THEN 'pending' ELSE 'blocked' END,generation=NULL WHERE sha256=?", (sha,))

    def mutate(self, uid, model_id: str, action: str, key: str, value=None):
        uid, key = trusted_uid(uid), request_key(key)
        if action not in ('visibility', 'delete', 'restore'):
            raise AssetError(400, 'invalid_action')
        if action == 'visibility':
            value = visibility(value)
        fingerprint = self._fingerprint(action, [model_id, value])
        with self._db(True) as db:
            old = db.execute('SELECT * FROM ysm_requests WHERE uid=? AND key=?', (uid, key)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise AssetError(409, 'idempotency_conflict')
                return {'model': self._model(self._row(db, model_id, uid, True)),
                        'selectedModelId': self._selection(db, uid), 'replayed': True}
            row = self._row(db, model_id, uid, True)
            sha = row['sha256']
            if action == 'visibility':
                if row['deleted_at'] is not None:
                    raise AssetError(409, 'model_deleted_use_restore')
                db.execute('UPDATE ysm_assets SET visibility=? WHERE id=?', (value, model_id))
                self._invalidate(db, sha)
            elif action == 'delete':
                db.execute('UPDATE ysm_assets SET deleted_at=coalesce(deleted_at,?) WHERE id=?', (self.clock(), model_id))
                self._invalidate(db, sha, deleted_model=model_id)
                if not db.execute('SELECT 1 FROM ysm_assets WHERE sha256=? AND deleted_at IS NULL', (sha,)).fetchone():
                    db.execute('UPDATE ysm_contents SET unreferenced_at=coalesce(unreferenced_at,?) WHERE sha256=?', (self.clock(), sha))
            else:
                # Recovery never silently republishes a previously public model.
                if row['deleted_at'] is None:
                    raise AssetError(409, 'model_not_deleted')
                db.execute("UPDATE ysm_assets SET deleted_at=NULL,visibility='private' WHERE id=?", (model_id,))
                db.execute('UPDATE ysm_contents SET unreferenced_at=NULL WHERE sha256=?', (sha,))
                self._invalidate(db, sha)
            revision = self._event(db, 'asset.' + action, uid=uid, modelId=model_id, sha256=sha, visibility=value)
            db.execute('INSERT INTO ysm_requests(uid,key,fingerprint,asset_id,action) VALUES(?,?,?,?,?)',
                       (uid, key, fingerprint, model_id, action))
            return {'model': self._model(self._row(db, model_id, uid, True)),
                    'selectedModelId': self._selection(db, uid), 'revision': revision, 'replayed': False}

    def _authorized(self, db, uid, model_id):
        if model_id.startswith('m_'):
            row = self._row(db, model_id, uid)
        elif model_id.startswith('p_'):
            row = db.execute('''SELECT a.*,c.public_id,c.size,c.verification,c.sync_status FROM ysm_contents c
                JOIN ysm_assets a ON a.sha256=c.sha256 WHERE c.public_id=? AND a.visibility='public'
                AND a.deleted_at IS NULL ORDER BY a.created_at,a.id LIMIT 1''', (model_id,)).fetchone()
            if row is None:
                raise AssetError(404, 'model_not_found')
        else:
            raise AssetError(404, 'model_not_found')
        if row['verification'] != 'verified':
            # Unknown public content is indistinguishable from a nonexistent id.
            raise AssetError(404 if model_id.startswith('p_') else 409, 'model_not_verified')
        return row

    def download(self, uid, model_id):
        uid = trusted_uid(uid)
        with self._db() as db:
            row = self._authorized(db, uid, model_id)
            path = self._path(row['sha256'], True)
            if not path.is_file():
                raise AssetError(409, 'storage_unavailable')
            return path, row['name']

    def select(self, uid, model_id, key):
        uid, key = trusted_uid(uid), request_key(key)
        fingerprint = self._fingerprint('select', [model_id])
        with self._db(True) as db:
            old = db.execute('SELECT fingerprint FROM ysm_requests WHERE uid=? AND key=?', (uid, key)).fetchone()
            if old and old[0] != fingerprint:
                raise AssetError(409, 'idempotency_conflict')
            # Recheck current rights even when replaying a formerly valid request.
            row = self._authorized(db, uid, model_id)
            if row['sync_status'] != 'ready':
                raise AssetError(409, 'model_not_active_on_server')
            if not old:
                db.execute('INSERT OR REPLACE INTO ysm_selections VALUES(?,?,?)', (uid, model_id, row['sha256']))
                self._event(db, 'selection.changed', uid=uid, modelId=model_id, sha256=row['sha256'])
                db.execute('INSERT INTO ysm_requests(uid,key,fingerprint,asset_id,action) VALUES(?,?,?,?,?)',
                           (uid, key, fingerprint, row['id'], 'select'))
            return {'state': 'selected', 'model': self._model(row, public=model_id.startswith('p_')),
                    'selectedModelId': self._selection(db, uid), 'replayed': bool(old)}

    def pending_validation(self):
        """Local adapter only: do not publish paths or hashes in user endpoints."""
        with self._db() as db:
            return [{'sha256': row['sha256'], 'size': row['size'],
                     'storageKey': 'quarantine/' + row['sha256'] + '.bin'}
                    for row in db.execute("SELECT * FROM ysm_contents WHERE verification='pending'")]

    def promote_verified(self, receipt: ValidationReceipt):
        if (not isinstance(receipt, ValidationReceipt) or not self.validation_profile
                or receipt.profile != self.validation_profile or not receipt.passed
                or not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}', receipt.receipt_id)):
            raise AssetError(403, 'trusted_format_validator_not_configured')
        with self._db(True) as db:
            row = db.execute('SELECT * FROM ysm_contents WHERE sha256=?', (receipt.sha256,)).fetchone()
            if row is None:
                raise AssetError(404, 'content_not_found')
            source = self._path(receipt.sha256, row['verification'] == 'verified')
            if not source.is_file() or self._hash_file(source) != (receipt.sha256, receipt.size) or receipt.size != row['size']:
                raise AssetError(409, 'validation_receipt_bytes_mismatch')
            destination = self._path(receipt.sha256, True)
            if source != destination:
                os.replace(source, destination)
            db.execute("UPDATE ysm_contents SET verification='verified',profile=?,receipt_id=?,sync_status='pending' WHERE sha256=?",
                       (receipt.profile, receipt.receipt_id, receipt.sha256))
            return self._event(db, 'content.verified', sha256=receipt.sha256, receiptId=receipt.receipt_id)

    def reject_content(self, sha: str):
        with self._db(True) as db:
            row = db.execute('SELECT verification FROM ysm_contents WHERE sha256=?', (sha,)).fetchone()
            if row is None:
                raise AssetError(404, 'content_not_found')
            db.execute("UPDATE ysm_contents SET verification='rejected',sync_status='blocked',generation=NULL WHERE sha256=?", (sha,))
            db.execute('DELETE FROM ysm_selections WHERE sha256=?', (sha,))
            return self._event(db, 'content.rejected', sha256=sha)

    def bridge_snapshot(self):
        """Machine-only desired permissions. Never place private blobs in a scan root."""
        with self._db() as db:
            models = []
            for content in db.execute("SELECT * FROM ysm_contents WHERE verification='verified' ORDER BY public_id"):
                refs = db.execute('SELECT * FROM ysm_assets WHERE sha256=? AND deleted_at IS NULL ORDER BY uid,id', (content['sha256'],)).fetchall()
                if not refs:
                    continue
                models.append({'contentId': content['public_id'], 'sha256': content['sha256'], 'size': content['size'],
                               'storageKey': 'verified/' + content['sha256'] + '.bin',
                               'public': any(r['visibility'] == 'public' for r in refs),
                               'ownerUids': sorted({r['uid'] for r in refs}), 'modelIds': [r['id'] for r in refs],
                               'references': [{'modelId': r['id'], 'ownerUid': r['uid'],
                                               'visibility': r['visibility']} for r in refs],
                               'validationStatus': 'verified',
                               'validationProfile': content['profile'], 'validationReceiptId': content['receipt_id'],
                               'syncStatus': content['sync_status']})
            selections = [{'uid': r['uid'], 'modelId': r['model_id'], 'contentId': r['public_id']}
                          for r in db.execute('SELECT s.*,c.public_id FROM ysm_selections s JOIN ysm_contents c USING(sha256)')]
            return {'revision': self._revision(db), 'models': models, 'selections': selections}

    def events_after(self, seq: int, limit: int = 500):
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise AssetError(400, 'invalid_event_cursor')
        with self._db() as db:
            return [{'seq': r['seq'], 'revision': r['revision'], 'type': r['kind'], **json.loads(r['payload'])}
                    for r in db.execute('SELECT * FROM ysm_events WHERE seq>? ORDER BY seq LIMIT ?', (seq, min(500, max(1, limit))))]

    def mark_synced(self, sha: str, revision: int, generation: str, *, active: bool = False):
        """Local bridge acknowledgement; active requires real import and ACL proof.

        A filesystem copy acknowledges staged only. Production keeps active=False
        until the actual YSM adapter has established download and selection ACLs.
        """
        if not isinstance(active, bool) or not isinstance(generation, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', generation):
            raise AssetError(400, 'invalid_sync_receipt')
        with self._db(True) as db:
            if isinstance(revision, bool) or not isinstance(revision, int) or revision != self._revision(db):
                raise AssetError(409, 'stale_permission_revision')
            row = db.execute('SELECT * FROM ysm_contents WHERE sha256=?', (sha,)).fetchone()
            if row is None or row['verification'] != 'verified':
                raise AssetError(409, 'model_not_verified')
            if not db.execute('SELECT 1 FROM ysm_assets WHERE sha256=? AND deleted_at IS NULL', (sha,)).fetchone():
                raise AssetError(409, 'content_has_no_active_reference')
            db.execute('UPDATE ysm_contents SET sync_status=?,generation=? WHERE sha256=?',
                       ('ready' if active else 'staged', generation, sha))
            return {'revision': revision, 'syncStatus': 'ready' if active else 'staged'}

    def purge_candidates(self):
        """Read-only maintenance proposal; retained logical assets remain restorable."""
        with self._db() as db:
            return [{'contentId': r['public_id'], 'sha256': r['sha256'], 'eligibleAt': utc_string(r['unreferenced_at'] + self.retention_seconds)}
                    for r in db.execute('SELECT * FROM ysm_contents WHERE unreferenced_at IS NOT NULL AND unreferenced_at<=?',
                                        (self.clock() - self.retention_seconds,))]

    def purge_unreferenced(self, sha: str, *, approved: bool = False):
        """Explicit offline permanent deletion, never scheduled or user-callable."""
        if approved is not True:
            raise AssetError(403, 'permanent_cleanup_requires_approval')
        moved = []
        try:
            with self._db(True) as db:
                row = db.execute('SELECT * FROM ysm_contents WHERE sha256=?', (sha,)).fetchone()
                if row is None:
                    raise AssetError(404, 'content_not_found')
                if (row['unreferenced_at'] is None or row['unreferenced_at'] > self.clock() - self.retention_seconds
                        or db.execute('SELECT 1 FROM ysm_assets WHERE sha256=? AND deleted_at IS NULL', (sha,)).fetchone()):
                    raise AssetError(409, 'content_still_referenced_or_retained')
                # Move to unique trash while holding the database write lock.
                # A concurrent reupload after commit gets a new blob at the
                # canonical digest path, which this cleanup never touches.
                for verified in (False, True):
                    source = self._path(sha, verified)
                    if source.exists():
                        trash = self.incoming / ('purge-' + secrets.token_hex(16) + '.bin')
                        os.replace(source, trash)
                        moved.append((source, trash))
                db.execute('DELETE FROM ysm_selections WHERE sha256=?', (sha,))
                db.execute('DELETE FROM ysm_assets WHERE sha256=?', (sha,))
                db.execute('DELETE FROM ysm_contents WHERE sha256=?', (sha,))
                self._event(db, 'content.purged', sha256=sha, contentId=row['public_id'])
        except BaseException:
            for source, trash in moved:
                if trash.exists() and not source.exists():
                    os.replace(trash, source)
            raise
        for _, trash in moved:
            trash.unlink(missing_ok=True)
