from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from .game_identity import uid_login_name, offline_uuid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier: str) -> str:
    return base64url(hashlib.sha256(verifier.encode("ascii")).digest())


def safe_return_to(value: str | None) -> str:
    if not value or len(value) > 2048:
        return "/account.html"
    # Check decoded forms too: browsers normalize backslashes and encoded slashes.
    decoded = value
    for _ in range(4):
        if (not decoded.startswith("/") or decoded.startswith("//")
                or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded)):
            return "/account.html"
        parts = urllib.parse.urlsplit(decoded)
        if parts.scheme or parts.netloc or parts.path.startswith(("/api/", "/__protected/")):
            return "/account.html"
        unquoted = urllib.parse.unquote(decoded)
        if unquoted == decoded:
            break
        decoded = unquoted
    return value


@dataclass(frozen=True)
class WebsiteAccount:
    subject: str
    uid: int
    username: str
    nickname: str
    game_name: str
    email: str | None
    email_verified: bool
    role: str
    created_at: str | None = None
    last_login_at: str | None = None

    @classmethod
    def from_userinfo(cls, data: dict) -> "WebsiteAccount":
        subject = str(data.get("sub", ""))
        username = str(data.get("username") or data.get("preferred_username") or "")
        nickname = str(data.get("nickname") or data.get("name") or username)
        try:
            uid = int(data.get("muxi_uid"))
        except (TypeError, ValueError):
            uid = 0
        email = str(data.get("email")) if data.get("email") else None
        email_verified = bool(data.get("email_verified")) if email else False
        if not subject or not username or not 10000 <= uid <= 9999999999999999:
            raise ValueError("统一账户返回的用户信息不完整")
        game_name = uid_login_name(uid)
        return cls(
            subject=subject,
            uid=uid,
            username=username,
            nickname=nickname,
            game_name=game_name,
            email=email,
            email_verified=email_verified,
            role=str(data.get("role", "player")),
            created_at=data.get("created_at"),
            last_login_at=data.get("last_login_at"),
        )

    def public(self) -> dict:
        return {
            "id": self.subject,
            "uid": self.uid,
            "username": self.username,
            "nickname": self.nickname,
            "gameName": uid_login_name(self.uid),
            "email": self.email,
            "emailVerified": self.email_verified,
            "role": self.role,
            "verified": self.email_verified,
            "createdAt": self.created_at,
            "lastLoginAt": self.last_login_at,
        }


@dataclass(frozen=True)
class PlayerProfile:
    subject: str
    uid: int
    game_name: str
    points: int
    balance_cents: int
    created_at: str
    updated_at: str
    display_name: str = ""
    legacy_game_name: str | None = None

    def public(self) -> dict:
        return {
            "subject": self.subject,
            "uid": self.uid,
            "gameName": uid_login_name(self.uid),
            "loginName": uid_login_name(self.uid),
            "offlineUuid": offline_uuid(self.uid),
            "displayName": self.display_name or uid_login_name(self.uid),
            "legacyGameName": self.legacy_game_name,
            "identityMode": "platform-uid",
            "points": self.points,
            "balanceCents": self.balance_cents,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class WebsiteAuthStore:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _init_schema(self) -> None:
        with self._lock, self.connect() as db:
            columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(oidc_web_sessions)").fetchall()}
            if columns and not {"uid", "nickname", "game_name", "email_verified"}.issubset(columns):
                db.execute("DROP TABLE oidc_web_sessions")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS oidc_login_states (
                    state_hash TEXT PRIMARY KEY,
                    code_verifier TEXT NOT NULL,
                    return_to TEXT NOT NULL,
                    browser_hash TEXT,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oidc_web_sessions (
                    token_hash TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    uid INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    game_name TEXT NOT NULL,
                    email TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    role TEXT NOT NULL,
                    created_at_claim TEXT,
                    last_login_at_claim TEXT,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS player_profiles (
                    subject TEXT PRIMARY KEY,
                    uid INTEGER NOT NULL,
                    game_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    points INTEGER NOT NULL DEFAULT 0,
                    balance_cents INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

            state_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(oidc_login_states)")}
            if "browser_hash" not in state_columns:
                db.execute("ALTER TABLE oidc_login_states ADD COLUMN browser_hash TEXT")

    def create_login(self, return_to: str, browser_token: str) -> tuple[str, str, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        with self._lock, self.connect() as db:
            now = utc_now()
            db.execute("DELETE FROM oidc_login_states WHERE expires_at < ?", (iso(now),))
            db.execute(
                "INSERT INTO oidc_login_states(state_hash,code_verifier,return_to,browser_hash,expires_at) VALUES(?,?,?,?,?)",
                (token_hash(state), verifier, safe_return_to(return_to), token_hash(browser_token), iso(now + timedelta(minutes=20))),
            )
        return state, verifier, pkce_challenge(verifier)

    def consume_login(self, state: str, browser_token: str | None) -> tuple[str, str] | None:
        if not state or not browser_token:
            return None
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT code_verifier,return_to,browser_hash,expires_at FROM oidc_login_states WHERE state_hash=?",
                (token_hash(state),),
            ).fetchone()
            if (row is None or not row["browser_hash"]
                    or not hmac.compare_digest(str(row["browser_hash"]), token_hash(browser_token))):
                return None
            db.execute("DELETE FROM oidc_login_states WHERE state_hash=?", (token_hash(state),))
        if row is None or datetime.fromisoformat(str(row["expires_at"])) < utc_now():
            return None
        return str(row["code_verifier"]), str(row["return_to"])

    def create_session(self, account: WebsiteAccount, days: int = 14) -> str:
        raw = secrets.token_urlsafe(40)
        with self._lock, self.connect() as db:
            db.execute(
                """INSERT INTO oidc_web_sessions
                   (token_hash,subject,uid,username,nickname,game_name,email,email_verified,role,
                    created_at_claim,last_login_at_claim,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    token_hash(raw), account.subject, account.uid, account.username, account.nickname,
                    account.game_name, account.email, 1 if account.email_verified else 0, account.role,
                    account.created_at, account.last_login_at, iso(utc_now() + timedelta(days=days)),
                ),
            )
        return raw

    def session(self, raw: str | None) -> WebsiteAccount | None:
        if not raw:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM oidc_web_sessions WHERE token_hash=? AND expires_at>?",
                (token_hash(raw), iso(utc_now())),
            ).fetchone()
        if row is None:
            return None
        return WebsiteAccount(
            subject=str(row["subject"]), uid=int(row["uid"]), username=str(row["username"]),
            nickname=str(row["nickname"]), game_name=str(row["game_name"]),
            email=str(row["email"]) if row["email"] is not None else None,
            email_verified=bool(row["email_verified"]),
            role=str(row["role"]), created_at=row["created_at_claim"], last_login_at=row["last_login_at_claim"],
        )

    def logout(self, raw: str | None) -> None:
        if not raw:
            return
        with self._lock, self.connect() as db:
            db.execute("DELETE FROM oidc_web_sessions WHERE token_hash=?", (token_hash(raw),))

    def player_profile(self, account: WebsiteAccount) -> PlayerProfile:
        now = iso(utc_now())
        with self._lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM player_profiles WHERE subject=?",
                (account.subject,),
            ).fetchone()
            if row is None:
                game_name = uid_login_name(account.uid)
                try:
                    db.execute(
                        """INSERT INTO player_profiles
                           (subject,uid,game_name,points,balance_cents,created_at,updated_at)
                           VALUES(?,?,?,0,0,?,?)""",
                        (account.subject, account.uid, game_name, now, now),
                    )
                except sqlite3.IntegrityError:
                    # 极端情况下历史数据发生重名，用 UID 生成一个稳定兜底名。
                    game_name = f"BMC{account.uid}"[:16]
                    db.execute(
                        """INSERT INTO player_profiles
                           (subject,uid,game_name,points,balance_cents,created_at,updated_at)
                           VALUES(?,?,?,0,0,?,?)""",
                        (account.subject, account.uid, game_name, now, now),
                    )
                row = db.execute(
                    "SELECT * FROM player_profiles WHERE subject=?",
                    (account.subject,),
                ).fetchone()
            return PlayerProfile(
                subject=str(row["subject"]),
                uid=int(row["uid"]),
                game_name=uid_login_name(account.uid),
                points=int(row["points"]),
                balance_cents=int(row["balance_cents"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                display_name=account.nickname or account.username,
                legacy_game_name=str(row["game_name"]) if str(row["game_name"]) != uid_login_name(account.uid) else None,
            )

class OidcClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str, redirect_uri: str):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def authorize_url(self, state: str, challenge: str) -> str:
        params = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "openid profile email",
                "state": state,
                "nonce": secrets.token_urlsafe(24),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.issuer}/oauth/authorize?{params}"

    @staticmethod
    def _json_request(request: urllib.request.Request, timeout: int = 15) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                payload = json.load(error)
                detail = payload.get("error_description") or payload.get("detail") or payload.get("error")
            except Exception:
                detail = None
            raise RuntimeError(detail or f"统一账户服务返回 HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError("统一账户服务暂时无法连接，请稍后重试") from error

    def exchange_code(self, code: str, verifier: str) -> dict:
        data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.issuer}/oauth/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        return self._json_request(request)

    def userinfo(self, access_token: str) -> WebsiteAccount:
        request = urllib.request.Request(
            f"{self.issuer}/oauth/userinfo",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        return WebsiteAccount.from_userinfo(self._json_request(request))

    def list_users(self, limit: int = 200) -> list[dict]:
        credential = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(
            f"{self.issuer}/api/admin/users?limit={max(1, min(limit, 500))}",
            headers={"Authorization": f"Basic {credential}", "Accept": "application/json"},
        )
        payload = self._json_request(request)
        return list(payload.get("users") or [])
