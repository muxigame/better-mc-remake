from __future__ import annotations

import base64
import hashlib
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
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/account.html"
    return value


@dataclass(frozen=True)
class WebsiteAccount:
    subject: str
    username: str
    email: str
    role: str
    created_at: str | None = None
    last_login_at: str | None = None

    @classmethod
    def from_userinfo(cls, data: dict) -> "WebsiteAccount":
        subject = str(data.get("sub", ""))
        username = str(data.get("username") or data.get("preferred_username") or "")
        email = str(data.get("email", ""))
        if not subject or not username or not email:
            raise ValueError("统一账户返回的用户信息不完整")
        return cls(
            subject=subject,
            username=username,
            email=email,
            role=str(data.get("role", "player")),
            created_at=data.get("created_at"),
            last_login_at=data.get("last_login_at"),
        )

    def public(self) -> dict:
        return {
            "id": self.subject,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "verified": True,
            "createdAt": self.created_at,
            "lastLoginAt": self.last_login_at,
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
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS oidc_login_states (
                    state_hash TEXT PRIMARY KEY,
                    code_verifier TEXT NOT NULL,
                    return_to TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oidc_web_sessions (
                    token_hash TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    username TEXT NOT NULL,
                    email TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at_claim TEXT,
                    last_login_at_claim TEXT,
                    expires_at TEXT NOT NULL
                );
                """
            )

    def create_login(self, return_to: str) -> tuple[str, str, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        with self._lock, self.connect() as db:
            now = utc_now()
            db.execute("DELETE FROM oidc_login_states WHERE expires_at < ?", (iso(now),))
            db.execute(
                "INSERT INTO oidc_login_states(state_hash,code_verifier,return_to,expires_at) VALUES(?,?,?,?)",
                (token_hash(state), verifier, safe_return_to(return_to), iso(now + timedelta(minutes=10))),
            )
        return state, verifier, pkce_challenge(verifier)

    def consume_login(self, state: str) -> tuple[str, str] | None:
        with self._lock, self.connect() as db:
            row = db.execute(
                "SELECT code_verifier,return_to,expires_at FROM oidc_login_states WHERE state_hash=?",
                (token_hash(state),),
            ).fetchone()
            db.execute("DELETE FROM oidc_login_states WHERE state_hash=?", (token_hash(state),))
        if row is None or datetime.fromisoformat(str(row["expires_at"])) < utc_now():
            return None
        return str(row["code_verifier"]), str(row["return_to"])

    def create_session(self, account: WebsiteAccount, days: int = 14) -> str:
        raw = secrets.token_urlsafe(40)
        with self._lock, self.connect() as db:
            db.execute(
                """INSERT INTO oidc_web_sessions
                   (token_hash,subject,username,email,role,created_at_claim,last_login_at_claim,expires_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    token_hash(raw), account.subject, account.username, account.email, account.role,
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
            subject=str(row["subject"]), username=str(row["username"]), email=str(row["email"]),
            role=str(row["role"]), created_at=row["created_at_claim"], last_login_at=row["last_login_at_claim"],
        )

    def logout(self, raw: str | None) -> None:
        if not raw:
            return
        with self._lock, self.connect() as db:
            db.execute("DELETE FROM oidc_web_sessions WHERE token_hash=?", (token_hash(raw),))


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
