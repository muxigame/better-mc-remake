from __future__ import annotations

import hashlib
import os
import secrets
import smtplib
import sqlite3
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Iterator

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
SESSION_DAYS = 14
VERIFY_HOURS = 24


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Account:
    id: int
    email: str
    username: str
    role: str
    verified: bool
    created_at: str
    last_login_at: str | None

    def public(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "role": self.role,
            "verified": self.verified,
            "createdAt": self.created_at,
            "lastLoginAt": self.last_login_at,
        }


class AuthStore:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'player',
                    verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS email_verifications (
                    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_account_id ON sessions(account_id);
                """
            )

    @staticmethod
    def _account(row: sqlite3.Row | None) -> Account | None:
        if row is None:
            return None
        return Account(
            id=int(row["id"]),
            email=str(row["email"]),
            username=str(row["username"]),
            role=str(row["role"]),
            verified=bool(row["verified"]),
            created_at=str(row["created_at"]),
            last_login_at=row["last_login_at"],
        )

    def register(self, email: str, username: str, password: str) -> tuple[Account, str]:
        now = utc_now()
        raw_token = secrets.token_urlsafe(32)
        with self._lock, self.connect() as db:
            try:
                cursor = db.execute(
                    "INSERT INTO accounts(email, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (email.lower(), username, PASSWORD_HASHER.hash(password), iso(now)),
                )
            except sqlite3.IntegrityError as error:
                message = str(error).lower()
                if "email" in message:
                    raise ValueError("该邮箱已经注册") from error
                raise ValueError("该玩家名已经被使用") from error
            account_id = int(cursor.lastrowid)
            db.execute(
                "INSERT INTO email_verifications(account_id, token_hash, expires_at) VALUES (?, ?, ?)",
                (account_id, token_hash(raw_token), iso(now + timedelta(hours=VERIFY_HOURS))),
            )
            row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._account(row), raw_token  # type: ignore[return-value]

    def verify_email(self, raw_token: str) -> Account | None:
        with self._lock, self.connect() as db:
            row = db.execute(
                "SELECT account_id, expires_at FROM email_verifications WHERE token_hash = ?",
                (token_hash(raw_token),),
            ).fetchone()
            if row is None or datetime.fromisoformat(row["expires_at"]) < utc_now():
                return None
            account_id = int(row["account_id"])
            db.execute("UPDATE accounts SET verified = 1 WHERE id = ?", (account_id,))
            db.execute("DELETE FROM email_verifications WHERE account_id = ?", (account_id,))
            return self._account(db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())

    def login(self, identity: str, password: str, kind: str) -> tuple[Account, str] | None:
        with self._lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM accounts WHERE email = ? COLLATE NOCASE OR username = ? COLLATE NOCASE",
                (identity, identity),
            ).fetchone()
            if row is None:
                return None
            try:
                PASSWORD_HASHER.verify(row["password_hash"], password)
            except (VerifyMismatchError, InvalidHashError):
                return None
            if PASSWORD_HASHER.check_needs_rehash(row["password_hash"]):
                db.execute(
                    "UPDATE accounts SET password_hash = ? WHERE id = ?",
                    (PASSWORD_HASHER.hash(password), int(row["id"])),
                )
            account = self._account(row)
            if account is None or not account.verified:
                raise PermissionError("请先完成邮箱验证")
            raw_token = secrets.token_urlsafe(40)
            now = utc_now()
            db.execute(
                "INSERT INTO sessions(token_hash, account_id, kind, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (token_hash(raw_token), account.id, kind, iso(now), iso(now + timedelta(days=SESSION_DAYS))),
            )
            db.execute("UPDATE accounts SET last_login_at = ? WHERE id = ?", (iso(now), account.id))
            row = db.execute("SELECT * FROM accounts WHERE id = ?", (account.id,)).fetchone()
            return self._account(row), raw_token  # type: ignore[return-value]

    def session(self, raw_token: str | None, kind: str | None = None) -> Account | None:
        if not raw_token:
            return None
        with self._lock, self.connect() as db:
            query = """
                SELECT a.* FROM sessions s
                JOIN accounts a ON a.id = s.account_id
                WHERE s.token_hash = ? AND s.expires_at > ?
            """
            params: list[str] = [token_hash(raw_token), iso(utc_now())]
            if kind:
                query += " AND s.kind = ?"
                params.append(kind)
            return self._account(db.execute(query, params).fetchone())

    def logout(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        with self._lock, self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(raw_token),))

    def list_accounts(self, limit: int = 200) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM accounts ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._account(row).public() for row in rows]  # type: ignore[union-attr]

    def promote_admin(self, email: str) -> Account | None:
        with self._lock, self.connect() as db:
            db.execute("UPDATE accounts SET role = 'admin' WHERE email = ? COLLATE NOCASE", (email,))
            return self._account(
                db.execute("SELECT * FROM accounts WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
            )


class SlidingWindowLimiter:
    def __init__(self, attempts: int, seconds: int):
        self.attempts = attempts
        self.seconds = seconds
        self._values: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            values = [item for item in self._values.get(key, []) if now - item < self.seconds]
            if len(values) >= self.attempts:
                self._values[key] = values
                return False
            values.append(now)
            self._values[key] = values
            return True


def send_verification_email(recipient: str, username: str, verify_url: str) -> None:
    host = os.getenv("BMC_SMTP_HOST", "").strip()
    sender = os.getenv("BMC_SMTP_FROM", "").strip()
    if not host or not sender:
        if os.getenv("BMC_AUTH_DEV_VERIFY") == "1":
            print(f"[DEV MAIL] verify {username}: {verify_url}", flush=True)
            return
        raise RuntimeError("邮件服务尚未配置")

    port = int(os.getenv("BMC_SMTP_PORT", "465"))
    username_smtp = os.getenv("BMC_SMTP_USERNAME", "")
    password_smtp = os.getenv("BMC_SMTP_PASSWORD", "")
    use_ssl = os.getenv("BMC_SMTP_SSL", "1") == "1"

    message = EmailMessage()
    message["Subject"] = "验证你的 Batter MC Remake 账号"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"你好，{username}！\n\n请在 24 小时内打开下面的地址完成邮箱验证：\n{verify_url}\n\n如果不是你发起的注册，可以忽略这封邮件。"
    )

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as smtp:
            if username_smtp:
                smtp.login(username_smtp, password_smtp)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls(context=context)
            if username_smtp:
                smtp.login(username_smtp, password_smtp)
            smtp.send_message(message)
