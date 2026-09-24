"""玩家上传的崩溃日志。

以前整合包带 Crash Assistant：游戏一崩就弹它自己的窗口，日志传到 mclo.gs，元数据发给
第三方，还夹着盗版提示和「模组被改过」提示，按钮带倒计时关不掉。现在它从包里拿掉了，
由启动器发现游戏异常退出、问玩家要不要上传，传到这里，按账号存。

一份报告就是启动器打的一个 zip：report.json（摘要）+ 若干日志文本。服务端不解开存，
原样落盘；列表只看 report.json 里的摘要，查看单个文件时才从 zip 里读、按上限截断。

玩家在网站玩家中心能看到、下载、删除自己的报告；管理员凭报告编号可以查看任何人的
（玩家把编号发给管理员就行，不用传文件）。
"""
from __future__ import annotations

import io
import json
import re
import secrets
import sqlite3
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MAX_BUNDLE_BYTES = 25 * 1024 * 1024
MAX_ENTRIES = 32
# 解压后的总量上限。日志压缩比大概 10:1，远超这个比例的包不正常（zip 炸弹）
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_SUMMARY_BYTES = 256 * 1024
VIEW_LIMIT_BYTES = 2 * 1024 * 1024
KEEP_PER_PLAYER = 30
UPLOADS_PER_HOUR = 10

ENTRY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
# 念给管理员听也不会听错：去掉了 0/O、1/I/L
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
REPORT_ID = re.compile(r"^[2-9A-HJKMNP-Z]{10}$")

# report.json 里只留这些键，别的一律丢掉；字符串截断到合理长度
_SUMMARY_FIELDS = {
    "kind": 16, "reason": 400, "summary": 600, "exitCode": 0, "uptimeSeconds": 0,
    "launcherVersion": 32, "packVersion": 32, "minecraftVersion": 32, "loaderVersion": 48,
    "crashedAt": 40, "environmentIncluded": 0, "logsIncluded": 0,
    # 意见反馈里玩家写的话
    "message": 2000,
}
# crash：游戏异常退出后上传；feedback：右下角「意见反馈」；manual：早期的手动上传
KINDS = ("crash", "feedback", "manual")


class CrashReportError(ValueError):
    """给玩家看的拒收原因。"""


def _new_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(10))


def _clean_summary(raw: dict) -> dict:
    summary: dict = {}
    for key, limit in _SUMMARY_FIELDS.items():
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            summary[key] = value
        elif isinstance(value, (int, float)) and limit == 0:
            summary[key] = int(value)
        elif isinstance(value, str) and limit > 0:
            summary[key] = value[:limit]
    if summary.get("kind") not in KINDS:
        summary["kind"] = "crash"
    return summary


def inspect_bundle(data: bytes) -> tuple[dict, list[dict]]:
    """校验启动器打的包，返回（摘要，文件列表）。"""
    if len(data) > MAX_BUNDLE_BYTES:
        raise CrashReportError("日志包太大（上限 25 MB）")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise CrashReportError("日志包格式不对") from error
    with archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ENTRIES:
            raise CrashReportError("日志包里的文件数量不对")
        files = []
        total = 0
        for entry in entries:
            if entry.is_dir() or not ENTRY_NAME.match(entry.filename):
                raise CrashReportError(f"日志包里有不认识的文件：{entry.filename[:80]}")
            total += entry.file_size
            files.append({"name": entry.filename, "size": entry.file_size})
        if total > MAX_UNCOMPRESSED_BYTES:
            raise CrashReportError("日志包解压后太大")
        try:
            info = archive.getinfo("report.json")
        except KeyError as error:
            raise CrashReportError("日志包缺少 report.json") from error
        if info.file_size > MAX_SUMMARY_BYTES:
            raise CrashReportError("report.json 太大")
        try:
            raw = json.loads(archive.read(info).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, zipfile.BadZipFile) as error:
            raise CrashReportError("report.json 读不出来") from error
    if not isinstance(raw, dict):
        raise CrashReportError("report.json 格式不对")
    return _clean_summary(raw), sorted(files, key=lambda f: f["name"])


class CrashReportStore:
    def __init__(self, database: Path, root: Path):
        self.database = database
        self.root = root
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._lock, self.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS crash_reports (
                    id TEXT PRIMARY KEY,
                    uid INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    files TEXT NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS crash_reports_uid ON crash_reports (uid, created_at)")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _path(self, report_id: str) -> Path:
        return self.root / f"{report_id}.zip"

    @staticmethod
    def _row(row: sqlite3.Row, with_files: bool = False) -> dict:
        report = {
            "id": str(row["id"]),
            "uid": int(row["uid"]),
            "createdAt": int(row["created_at"]),
            "size": int(row["size"]),
            **json.loads(row["summary"]),
        }
        if with_files:
            report["files"] = json.loads(row["files"])
        return report

    def add(self, uid: int, data: bytes, now: float | None = None) -> dict:
        summary, files = inspect_bundle(data)
        now = int(now if now is not None else time.time())
        with self._lock:
            with self.connect() as db:
                recent = db.execute(
                    "SELECT COUNT(*) FROM crash_reports WHERE uid=? AND created_at>?",
                    (uid, now - 3600)).fetchone()[0]
                if recent >= UPLOADS_PER_HOUR:
                    raise CrashReportError("上传太频繁了，过一会儿再试")
                report_id = _new_id()
                while db.execute("SELECT 1 FROM crash_reports WHERE id=?", (report_id,)).fetchone():
                    report_id = _new_id()
                temp = self._path(report_id).with_suffix(".part")
                temp.write_bytes(data)
                temp.replace(self._path(report_id))
                db.execute(
                    "INSERT INTO crash_reports (id, uid, created_at, size, summary, files) VALUES (?,?,?,?,?,?)",
                    (report_id, uid, now, len(data), json.dumps(summary, ensure_ascii=False),
                     json.dumps(files, ensure_ascii=False)))
                # 每人只留最近的若干份，旧的连文件一起删
                stale = db.execute(
                    "SELECT id FROM crash_reports WHERE uid=? ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?",
                    (uid, KEEP_PER_PLAYER)).fetchall()
                for row in stale:
                    db.execute("DELETE FROM crash_reports WHERE id=?", (row["id"],))
                    self._path(str(row["id"])).unlink(missing_ok=True)
            return self.get(report_id) or {}

    def list(self, uid: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM crash_reports WHERE uid=? ORDER BY created_at DESC, rowid DESC", (uid,)).fetchall()
        return [self._row(row) for row in rows]

    def recent(self, limit: int = 100) -> list[dict]:
        """管理后台用：所有人最近的崩溃日志和反馈。"""
        limit = max(1, min(int(limit), 500))
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM crash_reports ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(row) for row in rows]

    def get(self, report_id: str) -> dict | None:
        if not REPORT_ID.match(report_id or ""):
            return None
        with self.connect() as db:
            row = db.execute("SELECT * FROM crash_reports WHERE id=?", (report_id,)).fetchone()
        return self._row(row, with_files=True) if row else None

    def bundle_path(self, report_id: str) -> Path | None:
        path = self._path(report_id)
        return path if REPORT_ID.match(report_id or "") and path.is_file() else None

    def read_file(self, report_id: str, name: str) -> tuple[str, bool] | None:
        """读包里的一个文本文件。太长的留尾部——崩溃原因几乎都在日志末尾。"""
        path = self.bundle_path(report_id)
        if path is None or not ENTRY_NAME.match(name or ""):
            return None
        with zipfile.ZipFile(path) as archive:
            try:
                info = archive.getinfo(name)
            except KeyError:
                return None
            with archive.open(info) as stream:
                if info.file_size <= VIEW_LIMIT_BYTES:
                    data = stream.read(VIEW_LIMIT_BYTES)
                    truncated = False
                else:
                    stream.seek(info.file_size - VIEW_LIMIT_BYTES)
                    data = stream.read(VIEW_LIMIT_BYTES)
                    truncated = True
        return data.decode("utf-8", errors="replace"), truncated

    def delete(self, uid: int, report_id: str) -> bool:
        with self._lock, self.connect() as db:
            gone = db.execute("DELETE FROM crash_reports WHERE id=? AND uid=?", (report_id, uid)).rowcount
        if gone:
            self._path(report_id).unlink(missing_ok=True)
        return bool(gone)
