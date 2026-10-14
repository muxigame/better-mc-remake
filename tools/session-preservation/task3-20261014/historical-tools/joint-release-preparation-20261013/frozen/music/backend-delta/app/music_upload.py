"""Disabled-by-default music upload service; storage and identity are injected.

No platform cookies, source URL, filename, AccessKey secret or client UID is
accepted by these endpoints. A deployment must mount the router explicitly.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import sqlite3
import time
import uuid
import wave
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable, Protocol

MAX_BYTES = 32 * 1024 * 1024
DAILY_BYTES = 256 * 1024 * 1024
TOTAL_BYTES = 10 * 1024 * 1024 * 1024
TICKET_SECONDS = 90
JOB_SECONDS = 600
LICENSES = {"CC0-1.0", "CC-BY-4.0", "OWNED-REDISTRIBUTION"}


class MusicError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


@dataclass(frozen=True)
class Credentials:
    # The provider holds the backend's already managed identity. Never serialize.
    access_id: str = field(repr=False)
    secret: str = field(repr=False)
    security_token: str = field(default="", repr=False)
    expires_at: float = field(default=float("inf"), repr=False)


def post_policy(bucket: str, region: str, key: str, size: int,
                credential: Credentials, now: float) -> dict:
    """OSS V4 POST: exact object, exact file size, immutable private staging."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
        raise MusicError(503, "storage_unconfigured")
    if not re.fullmatch(r"cn-[a-z0-9-]+", region):
        raise MusicError(503, "storage_unconfigured")
    if not 1 <= size <= MAX_BYTES or not re.fullmatch(r"incoming/\d+/[0-9a-f-]{36}\.wav", key):
        raise MusicError(400, "invalid_object")
    expiry = min(now + TICKET_SECONDS, credential.expires_at - 5)
    if expiry <= now + 10 or not credential.access_id or not credential.secret:
        raise MusicError(503, "storage_identity_unavailable")
    dt = datetime.fromtimestamp(now, timezone.utc)
    date = dt.strftime("%Y%m%d")
    fields = {"key": key, "Content-Type": "application/octet-stream",
              "success_action_status": "201", "x-oss-object-acl": "private",
              "x-oss-forbid-overwrite": "true",
              "x-oss-signature-version": "OSS4-HMAC-SHA256",
              "x-oss-credential": f"{credential.access_id}/{date}/{region}/oss/aliyun_v4_request",
              "x-oss-date": dt.strftime("%Y%m%dT%H%M%SZ")}
    if credential.security_token:
        fields["x-oss-security-token"] = credential.security_token
    conditions = [{"bucket": bucket}, ["content-length-range", size, size]]
    conditions.extend({k: v} for k, v in fields.items())
    policy = {"expiration": datetime.fromtimestamp(expiry, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
              "conditions": conditions}
    encoded = base64.b64encode(json.dumps(policy, separators=(",", ":")).encode()).decode()
    derived = ("aliyun_v4" + credential.secret).encode()
    for segment in (date, region, "oss", "aliyun_v4_request"):
        derived = hmac.new(derived, segment.encode(), hashlib.sha256).digest()
    fields["policy"] = encoded
    fields["x-oss-signature"] = hmac.new(derived, encoded.encode(), hashlib.sha256).hexdigest()
    return {"url": f"https://{bucket}.oss-{region}.aliyuncs.com/", "fields": fields,
            "expiresAt": expiry}


class Storage(Protocol):
    bucket: str
    region: str
    def ticket(self, key: str, size: int, now: float) -> dict: ...
    def read(self, key: str, max_bytes: int) -> bytes: ...
    def publish(self, key: str, audio: bytes) -> None: ...
    def delete(self, key: str) -> None: ...


class ManagedOssStorage:
    """Adapter for an existing OSS SDK Bucket, never constructs credentials."""
    def __init__(self, bucket, region: str, credentials: Callable[[], Credentials]):
        self.sdk, self.bucket, self.region, self.credentials = bucket, bucket.bucket_name, region, credentials

    def ticket(self, key, size, now):
        return post_policy(self.bucket, self.region, key, size, self.credentials(), now)

    def read(self, key, max_bytes):
        head = self.sdk.head_object(key)
        if not 1 <= head.content_length <= max_bytes:
            raise MusicError(422, "invalid_audio_size")
        stream = self.sdk.get_object(key)
        try:
            data = stream.read(max_bytes + 1)
            if len(data) != head.content_length or len(data) > max_bytes:
                raise MusicError(422, "invalid_audio_size")
            return data
        finally:
            stream.close()

    def publish(self, key, audio):
        # Upload the validated bytes, not a mutable staging object via CopyObject.
        self.sdk.put_object(key, audio, headers={"Content-Type": "audio/mpeg" if key.endswith('.mp3') else "audio/wav",
            "x-oss-object-acl": "private", "x-oss-forbid-overwrite": "true"})

    def delete(self, key):
        self.sdk.delete_object(key)


def wav_duration(data: bytes) -> int:
    """Initial supported burn format: real PCM WAV, including the CC0 QA tone."""
    try:
        with wave.open(io.BytesIO(data)) as audio:
            channels, width, rate, frames = (audio.getnchannels(), audio.getsampwidth(),
                                            audio.getframerate(), audio.getnframes())
            if audio.getcomptype() != "NONE" or channels not in (1, 2) or width not in (1, 2):
                raise ValueError()
            if not 8000 <= rate <= 96000 or not 1 <= frames <= rate * 600:
                raise ValueError()
            if len(audio.readframes(frames)) != frames * channels * width:
                raise ValueError()
            return max(1, (frames + rate - 1) // rate)
    except (wave.Error, EOFError, ValueError):
        raise MusicError(422, "unsupported_or_truncated_audio") from None


class MusicUploads:
    def __init__(self, database: Path, storage: Storage | None, clock=time.time, normalizer=None, receipts=None, job_factory=None,*,daily_intent_limit=100,cumulative_quotas=False):
        self.database, self.storage, self.clock = database, storage, clock
        self.normalizer, self.receipts = normalizer, receipts
        if type(daily_intent_limit) is not int or daily_intent_limit<1:raise ValueError('invalid_daily_music_limit')
        self.daily_intent_limit,self.cumulative_quotas=daily_intent_limit,cumulative_quotas
        # Internal allocation only; HTTP clients cannot choose storage keys.
        self.job_factory = job_factory or uuid.uuid4
        self.lock = RLock()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS music_uploads (
                id TEXT PRIMARY KEY, uid INTEGER NOT NULL, session TEXT NOT NULL,
                request_id TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                license TEXT NOT NULL, credit TEXT NOT NULL, title TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL,
                duration INTEGER, receipt TEXT, consumed REAL,
                UNIQUE(uid, session, request_id))""")
            columns = {row[1] for row in db.execute('PRAGMA table_info(music_uploads)')}
            for name, declaration in (('burn_nonce', "TEXT NOT NULL DEFAULT ''"),
                    ('media_ext', "TEXT NOT NULL DEFAULT 'wav'"), ('media_sha', 'TEXT'), ('media_size', 'INTEGER'),
                    ('failure_phase','TEXT'),('failure_code','TEXT'),('failure_kind','TEXT')):
                if name not in columns:
                    db.execute(f'ALTER TABLE music_uploads ADD COLUMN {name} {declaration}')
            db.execute('CREATE TABLE IF NOT EXISTS music_daily_intents(uid INTEGER NOT NULL,day TEXT NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(uid,day))')
            # Preserve today's existing intents when upgrading. Shanghai dates
            # use UTC+08:00; counters never depend on the client's Netease user.
            db.execute("INSERT OR IGNORE INTO music_daily_intents SELECT uid,date(created,'unixepoch','+8 hours'),count(*) FROM music_uploads GROUP BY uid,date(created,'unixepoch','+8 hours')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enabled(self):
        if self.storage is None:
            raise MusicError(503, "music_upload_unconfigured")
        return self.storage

    @staticmethod
    def own(db, job, uid, session):
        row = db.execute("SELECT * FROM music_uploads WHERE id=? AND uid=? AND session=?",
                         (job, uid, session)).fetchone()
        if row is None:
            raise MusicError(404, "job_not_found")
        return row

    def create(self, uid, session, request_id, size, sha256, license_id, credit, title, burn_nonce=''):
        storage, now = self.enabled(), self.clock()
        if self.receipts is not None and not burn_nonce:
            raise MusicError(400, 'burn_context_required')
        try:
            if str(uuid.UUID(session)) != session or str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
            if burn_nonce and str(uuid.UUID(burn_nonce)) != burn_nonce:
                raise ValueError()
        except (ValueError, AttributeError):
            raise MusicError(400, "invalid_correlation") from None
        if not 10000 <= uid <= 9999999999999999 or type(size) is not int or not 1 <= size <= MAX_BYTES:
            raise MusicError(400, "invalid_size")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256) or license_id not in LICENSES:
            raise MusicError(400, "redistribution_permission_required")
        if not isinstance(credit, str) or not 1 <= len(credit) <= 1024 or any(ord(c) < 32 for c in credit):
            raise MusicError(400, "redistribution_permission_required")
        if not isinstance(title, str) or not 1 <= len(title) <= 120 or any(ord(c) < 32 for c in title):
            raise MusicError(400, "invalid_title")
        if len(json.dumps({'title':title,'credit':credit}, ensure_ascii=True, separators=(',',':')).encode()) > 4096:
            raise MusicError(400, 'invalid_metadata_size')
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE music_uploads SET state='expired' WHERE state IN ('pending','validating') AND expires<=?", (now,))
            old = db.execute("SELECT * FROM music_uploads WHERE uid=? AND session=? AND request_id=?", (uid, session, request_id)).fetchone()
            if old:
                if tuple(old[k] for k in ("size", "sha256", "license", "credit", "title", "burn_nonce")) != (size, sha256, license_id, credit, title, burn_nonce):
                    raise MusicError(409, "request_id_reused")
                if old['state'] != 'pending':
                    raise MusicError(409, "job_not_pending")
                job = old['id']
            else:
                active = db.execute("SELECT count(*) FROM music_uploads WHERE uid=? AND state IN ('pending','validating')", (uid,)).fetchone()[0]
                daily = db.execute("SELECT count(*),coalesce(sum(size),0) FROM music_uploads WHERE uid=? AND created>=?", (uid, now-86400)).fetchone()
                # Conservatively count every issued upload intent, including failure/cancel.
                # Releasing it before expired POST requests drain can hide orphan storage.
                total = db.execute("SELECT coalesce(sum(size),0) FROM music_uploads").fetchone()[0]
                if active >= 2 or self.cumulative_quotas and (daily[1] + size > DAILY_BYTES or total + size > TOTAL_BYTES):
                    raise MusicError(429, "music_upload_quota")
                from datetime import datetime,timezone,timedelta
                day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).date().isoformat()
                db.execute('INSERT OR IGNORE INTO music_daily_intents VALUES(?,?,0)',(uid,day))
                admitted=db.execute('UPDATE music_daily_intents SET count=count+1 WHERE uid=? AND day=? AND count<?',(uid,day,self.daily_intent_limit))
                if not admitted.rowcount:raise MusicError(429,'music_daily_upload_limit')
                try:
                    job = str(self.job_factory())
                    allocated = uuid.UUID(job)
                    if allocated.version != 4 or str(allocated) != job:
                        raise ValueError()
                except (ValueError, TypeError, AttributeError, StopIteration):
                    raise MusicError(503, 'music_job_allocation_failed') from None
                db.execute("""INSERT INTO music_uploads
                    (id,uid,session,request_id,size,sha256,license,credit,title,created,expires,state,duration,receipt,consumed,burn_nonce)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (job, uid, session, request_id, size, sha256, license_id, credit, title,
                            now, now + JOB_SECONDS, 'pending', None, None, None, burn_nonce))
            ticket = storage.ticket(f"incoming/{uid}/{job}.wav", size, now)
        return {"jobId": job, "upload": ticket}

    def complete(self, uid, session, job):
        storage, now = self.enabled(), self.clock()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.own(db, job, uid, session)
            if row['state'] == 'ready':
                return self.receipt(row)
            if row['state'] != 'pending' or row['expires'] <= now:
                raise MusicError(409, "job_not_pending")
            db.execute("UPDATE music_uploads SET state='validating' WHERE id=?", (job,))
        incoming, published = f"incoming/{uid}/{job}.wav", f"media/{uid}/{job}.wav"
        phase="source_read"
        try:
            data = storage.read(incoming, MAX_BYTES)
            phase="source_hash"
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise MusicError(422, "audio_digest_mismatch")
            phase="audio_decode"
            if self.normalizer is None:
                from .music_audio import VerifiedAudio
                verified = VerifiedAudio(data, 'wav', wav_duration(data))
            else:
                verified = self.normalizer(data)
            duration = verified.duration
            published = f"media/{uid}/{job}.{verified.extension}"
            # Cancellation can arrive during network reads; serializes only final promotion.
            with self.lock, self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self.own(db, job, uid, session)
                if current['state'] != 'validating' or current['expires'] <= self.clock():
                    raise MusicError(409, "job_cancelled_or_expired")
                phase="media_publish"
                storage.publish(published, verified.data)
                receipt = str(uuid.uuid4())
                db.execute("""UPDATE music_uploads SET state='ready',duration=?,receipt=?,media_ext=?,media_sha=?,media_size=? WHERE id=?""",
                    (duration, receipt, verified.extension, hashlib.sha256(verified.data).hexdigest(), len(verified.data), job))
                ready = self.own(db, job, uid, session)
        except Exception as error:
            with self.lock, self.connect() as db:
                # Persist only our stable code and a class name, never provider
                # exception messages, response bodies, URLs or credentials.
                code=error.code if isinstance(error,MusicError) and re.fullmatch(r'[a-z_]{1,80}',error.code) else 'storage_operation_failed'
                kind=type(error).__name__
                if not re.fullmatch(r'[A-Za-z_]{1,60}',kind):kind='ProviderError'
                db.execute("UPDATE music_uploads SET state=CASE WHEN state='validating' THEN 'failed' ELSE state END,failure_phase=?,failure_code=?,failure_kind=? WHERE id=?", (phase,code,kind,job))
            # Preserve the stable public error, never stringify provider exceptions/secrets.
            if isinstance(error, MusicError):
                raise
            raise MusicError(503, "storage_operation_failed") from None
        try:
            storage.delete(incoming)
        except Exception:
            pass  # Private staging lifecycle is the backstop, not a public fallback.
        return self.receipt(ready)

    def receipt(self, row):
        storage = self.enabled()
        result = {"version": 1, "jobId": row['id'], "receiptId": row['receipt'],
                "sha256": row['media_sha'] or row['sha256'], "sourceSha256": row['sha256'],
                "bytes": row['media_size'] or row['size'], "duration": row['duration'],
                "title": row['title'], "license": row['license'], "credit": row['credit'],
                "url": f"https://{storage.bucket}.oss-{storage.region}.aliyuncs.com/media/{row['uid']}/{row['id']}.{row['media_ext']}"}
        if self.receipts is not None:
            result['proof'] = self.receipts.sign(row, result)
        return result

    def redeem(self, proof, live_identity):
        if self.receipts is None:
            raise MusicError(503, 'burn_receipts_unconfigured')
        claims = self.receipts.verify(proof)
        live_identity(claims['uid'], claims['gameSession'])
        with self.lock, self.connect() as db:
            row = self.own(db, claims['jobId'], claims['uid'], claims['gameSession'])
            if row['burn_nonce'] != claims['burnNonce']:
                raise MusicError(401, 'invalid_burn_proof')
        return self.consume(claims['uid'], claims['gameSession'], claims['jobId'], claims['receiptId'])

    def cancel(self, uid, session, job):
        self.enabled()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.own(db, job, uid, session)
            if row['state'] == 'ready':
                raise MusicError(409, "already_published")
            db.execute("UPDATE music_uploads SET state='cancelled' WHERE id=?", (job,))
        # Do not delete while a 90s POST is still valid: cleanup after expiry/lifecycle.
        return {"cancelled": True}

    def playback(self, caller_uid, session, reference,refresh=False):
        """Caller was authenticated/live-bound by the router; only redeemed,
        backend-verified redistribution media can receive a transient GET URL.
        CD references remain immutable and contain no temporary credential.
        """
        from urllib.parse import urlsplit
        storage=self.enabled()
        try:
            if type(refresh) is not bool or type(caller_uid) is not int or not 10000<=caller_uid<=9999999999999999 or str(uuid.UUID(session))!=session:raise ValueError()
            parsed=urlsplit(reference)
            if parsed.scheme!='https' or parsed.netloc!=f'{storage.bucket}.oss-{storage.region}.aliyuncs.com' or parsed.query or parsed.fragment:raise ValueError()
            match=re.fullmatch(r'/media/([1-9][0-9]{4,15})/([0-9a-f-]{36})\.(mp3|wav)',parsed.path)
            if not match:raise ValueError()
            owner,job,extension=match.groups()
            if str(uuid.UUID(job))!=job or uuid.UUID(job).version!=4:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise MusicError(400,'invalid_media_reference') from None
        with self.lock,self.connect() as db:
            row=db.execute('SELECT * FROM music_uploads WHERE id=? AND uid=?',(job,int(owner))).fetchone()
            if row is None or row['state']!='ready' or row['consumed'] is None or row['media_ext']!=extension or not row['media_sha'] or not row['media_size'] or row['license'] not in LICENSES:
                raise MusicError(404,'verified_media_unavailable')
            # Completed metadata is durable. Upload-intent/proof expiry is not
            # a CD expiry; cancelled/failed/unredeemed objects never qualify.
            key=f'media/{row["uid"]}/{row["id"]}.{row["media_ext"]}'
            duration,size,digest=row['duration'],row['media_size'],row['media_sha']
        try:
            signed=storage.signed_read(key,duration,size,refresh=refresh)
            return {**signed,'reference':reference,'sha256':digest,'duration':duration}
        except MusicError:raise
        except Exception:raise MusicError(503,'music_playback_unavailable') from None

    def consume(self, uid, session, job, receipt_id):
        self.enabled()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.own(db, job, uid, session)
            if row['state'] != 'ready' or row['consumed'] is not None or row['expires'] <= self.clock():
                raise MusicError(409, "receipt_unavailable")
            if not isinstance(receipt_id, str) or not hmac.compare_digest(row['receipt'], receipt_id):
                raise MusicError(401, "invalid_receipt")
            db.execute("UPDATE music_uploads SET consumed=? WHERE id=?", (self.clock(), job))
            return self.receipt(row)


def music_upload_router(service_getter, launcher_identity, server_identity, live_identity=None):
    """Auth callbacks return verified (uid, gameSession); never trust payload UID.

    Existing muxi-auth launcher token plus live TerminalAccessStore connection
    must implement launcher_identity. Existing dedicated game-server authority
    plus live connection must implement server_identity. Missing callback denies.
    FastAPI runs these sync routes in its worker pool, not the event loop.
    """
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, ConfigDict, Field

    class Session(BaseModel):
        model_config = ConfigDict(extra="forbid")
        gameSession: str = Field(pattern=r"^[0-9a-f-]{36}$")

    class Intent(Session):
        burnNonce: str = Field(pattern=r"^[0-9a-f-]{36}$")
        requestId: str = Field(pattern=r"^[0-9a-f-]{36}$")
        bytes: int = Field(strict=True, ge=1, le=MAX_BYTES)
        sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
        license: str
        credit: str = Field(min_length=1, max_length=1024)
        title: str = Field(min_length=1, max_length=120)

    class Consume(Session):
        receiptId: str = Field(pattern=r"^[0-9a-f-]{36}$")

    class Playback(Session):
        reference: str = Field(min_length=1,max_length=512)
        refresh: bool = Field(default=False,strict=True)

    router = APIRouter()

    def run(identity, request, session, action):
        try:
            if identity is None:
                raise MusicError(503, "music_identity_unconfigured")
            uid, verified_session = identity(request, session)
            if verified_session != session:
                raise MusicError(401, "game_connection_mismatch")
            return JSONResponse(action(service_getter(), uid), headers={"Cache-Control": "no-store"})
        except MusicError as error:
            return JSONResponse({"error": error.code}, status_code=error.status,
                                headers={"Cache-Control": "no-store"})

    # Resolve function-local model annotations explicitly (future annotations).
    def create(payload, request):
        return run(launcher_identity, request, payload.gameSession,
            lambda s, uid: s.create(uid, payload.gameSession, payload.requestId,
                payload.bytes, payload.sha256, payload.license, payload.credit, payload.title, payload.burnNonce))

    def complete(job, payload, request):
        return run(launcher_identity, request, payload.gameSession,
            lambda s, uid: s.complete(uid, payload.gameSession, job))

    def cancel(job, payload, request):
        return run(launcher_identity, request, payload.gameSession,
            lambda s, uid: s.cancel(uid, payload.gameSession, job))

    def consume(job, payload, request):
        return run(server_identity, request, payload.gameSession,
            lambda s, uid: s.consume(uid, payload.gameSession, job, payload.receiptId))

    def playback(payload,request):
        return run(launcher_identity,request,payload.gameSession,
            lambda s,uid:s.playback(uid,payload.gameSession,payload.reference,payload.refresh))

    for fn, model, path in ((create, Intent, "/api/launcher/music/uploads"),
        (complete, Session, "/api/launcher/music/uploads/{job}/complete"),
        (cancel, Session, "/api/launcher/music/uploads/{job}/cancel"),
        (consume, Consume, "/api/internal/minecraft/music/uploads/{job}/consume"),
        (playback, Playback, "/api/launcher/music/playback")):
        fn.__annotations__ = {"payload": model, "request": Request}
        router.add_api_route(path, fn, methods=["POST"], include_in_schema=False)

    class Proof(BaseModel):
        model_config = ConfigDict(extra='forbid')
        proof: str = Field(min_length=1, max_length=8192)

    def redeem(payload):
        try:
            if live_identity is None: raise MusicError(503, 'music_identity_unconfigured')
            return JSONResponse(service_getter().redeem(payload.proof, live_identity), headers={'Cache-Control':'no-store'})
        except MusicError as error:
            return JSONResponse({'error':error.code}, status_code=error.status, headers={'Cache-Control':'no-store'})
    redeem.__annotations__ = {'payload':Proof}
    router.add_api_route('/api/minecraft/music/burn-receipt/redeem', redeem, methods=['POST'], include_in_schema=False)

    def capabilities():
        service = service_getter()
        return JSONResponse({'enabled':service.storage is not None and service.receipts is not None,
            'formats':service.normalizer.formats if service.normalizer else ['wav'],
            'maxBytes':MAX_BYTES, 'maxSeconds':600,'playback':'private-signed-v1' if callable(getattr(service.storage,'signed_read',None)) else 'unconfigured'}, headers={'Cache-Control':'no-store'})
    router.add_api_route('/api/music/capabilities', capabilities, methods=['GET'], include_in_schema=False)
    return router
