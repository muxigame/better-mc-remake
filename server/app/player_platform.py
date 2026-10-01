"""Website-owned points and independent platform/game permissions.

No account-center disabled flag or Minecraft ops file is changed here.
"""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


class PlatformStore:
    def __init__(self, path):
        self.path = Path(path)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS task_point_ledger (
                uid INTEGER NOT NULL, day TEXT NOT NULL, task_id TEXT NOT NULL,
                hard INTEGER NOT NULL CHECK(hard IN (0,1)),
                points INTEGER NOT NULL CHECK(points IN (1,3)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(uid,day,task_id));
              CREATE TABLE IF NOT EXISTS platform_permissions (
                uid INTEGER PRIMARY KEY, platform_admin INTEGER NOT NULL DEFAULT 0,
                game_op_level INTEGER NOT NULL DEFAULT 0 CHECK(game_op_level BETWEEN 0 AND 4));
              CREATE TABLE IF NOT EXISTS game_bans (
                uid INTEGER PRIMARY KEY, reason TEXT NOT NULL, actor_uid INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE IF NOT EXISTS game_ban_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor_uid INTEGER NOT NULL,
                target_uid INTEGER NOT NULL, banned INTEGER NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate_uid(uid):
        if type(uid) is not int or not 10000 <= uid <= 9999999999999999:
            raise ValueError('Invalid UID')

    def permissions(self, uid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM platform_permissions WHERE uid=?', (uid,)).fetchone()
        return {'platformAdmin': bool(row['platform_admin']) if row else False,
                'gameOpLevel': int(row['game_op_level']) if row else 0}

    def credit(self, uid, day, task_id, hard):
        self.validate_uid(uid)
        import re
        if type(hard) is not bool or not isinstance(task_id, str) or not re.fullmatch('[a-z0-9_-]{1,48}', task_id):
            raise ValueError('Invalid task event')
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('Invalid task date')
        # TaskCatalog may use any timezone; local task dates can be one day ahead of UTC.
        if date.fromisoformat(day) > datetime.now(timezone.utc).date() + timedelta(days=1):
            raise ValueError('Future task date')
        points = 3 if hard else 1
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # A UID is mapped by the server-owned OIDC profile, never a client subject.
            rows = db.execute('SELECT subject FROM player_profiles WHERE uid=?', (uid,)).fetchall()
            if len(rows) != 1:
                raise LookupError('Player must sign in to the website first')
            old = db.execute('SELECT hard FROM task_point_ledger WHERE uid=? AND day=? AND task_id=?',
                             (uid, day, task_id)).fetchone()
            if old is not None:
                if bool(old['hard']) != hard:
                    raise ValueError('Conflicting task replay')
                return False
            db.execute('INSERT INTO task_point_ledger(uid,day,task_id,hard,points) VALUES(?,?,?,?,?)',
                       (uid, day, task_id, int(hard), points))
            db.execute('UPDATE player_profiles SET points=points+?, updated_at=CURRENT_TIMESTAMP WHERE subject=?',
                       (points, rows[0]['subject']))
        return True

    def banned(self, uid):
        self.validate_uid(uid)
        with self.connect() as db:
            return db.execute('SELECT 1 FROM game_bans WHERE uid=?', (uid,)).fetchone() is not None

    def set_ban(self, actor_uid, target_uid, banned, reason, account_admin=False):
        self.validate_uid(actor_uid); self.validate_uid(target_uid)
        if type(banned) is not bool or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 300:
            raise ValueError('Provide a reason of 1-300 characters')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT platform_admin FROM platform_permissions WHERE uid=?', (actor_uid,)).fetchone()
            if not account_admin and not (actor and actor[0]):
                raise PermissionError('Platform administrator required')
            if banned:
                db.execute('INSERT INTO game_bans(uid,reason,actor_uid) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET reason=excluded.reason,actor_uid=excluded.actor_uid,created_at=CURRENT_TIMESTAMP',
                           (target_uid, reason.strip(), actor_uid))
            else:
                db.execute('DELETE FROM game_bans WHERE uid=?', (target_uid,))
            db.execute('INSERT INTO game_ban_audit(actor_uid,target_uid,banned,reason) VALUES(?,?,?,?)',
                       (actor_uid,target_uid,int(banned),reason.strip()))

    def initialize_permissions(self, uid, platform_admin, game_op_level):
        """Explicit offline administrative initialization; never called on startup."""
        self.validate_uid(uid)
        if type(platform_admin) is not bool or type(game_op_level) is not int or not 0 <= game_op_level <= 4:
            raise ValueError('Invalid permissions')
        with self.connect() as db:
            db.execute('INSERT INTO platform_permissions VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET platform_admin=excluded.platform_admin,game_op_level=excluded.game_op_level',
                       (uid,int(platform_admin),game_op_level))
