"""Website-owned points and independent platform/game permissions.

No account-center disabled flag or Minecraft ops file is changed here.
"""
import sqlite3
import hashlib
from .game_identity import offline_uuid
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
              CREATE TABLE IF NOT EXISTS game_op_observations (
                uid INTEGER PRIMARY KEY, level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 4),
                observed_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS game_op_sync (
                uid INTEGER PRIMARY KEY, subject TEXT NOT NULL, revision INTEGER NOT NULL,
                desired_level INTEGER NOT NULL CHECK(desired_level BETWEEN 0 AND 4),
                state TEXT NOT NULL, observed_level INTEGER, observed_at TEXT, error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE IF NOT EXISTS game_op_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor_uid INTEGER NOT NULL,
                target_uid INTEGER NOT NULL, revision INTEGER NOT NULL,
                before_level INTEGER NOT NULL, after_level INTEGER NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE IF NOT EXISTS game_op_sync_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, target_uid INTEGER NOT NULL,
                revision INTEGER NOT NULL, applied INTEGER NOT NULL, observed_level INTEGER,
                error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE IF NOT EXISTS game_bans (
                uid INTEGER PRIMARY KEY, reason TEXT NOT NULL, actor_uid INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE IF NOT EXISTS game_ban_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor_uid INTEGER NOT NULL,
                target_uid INTEGER NOT NULL, banned INTEGER NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE IF NOT EXISTS minigame_result_ledger (
                uid INTEGER NOT NULL, game TEXT NOT NULL, session TEXT NOT NULL,
                event TEXT NOT NULL, points INTEGER NOT NULL CHECK(points BETWEEN 0 AND 10000),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(uid,game,session));
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

    def point_balance(self, uid):
        self.validate_uid(uid)
        with self.connect() as db:
            rows = db.execute('SELECT points FROM player_profiles WHERE uid=?', (uid,)).fetchall()
        return str(rows[0]['points']) if len(rows) == 1 else None

    def banned(self, uid):
        self.validate_uid(uid)
        with self.connect() as db:
            return db.execute('SELECT 1 FROM game_bans WHERE uid=?', (uid,)).fetchone() is not None

    @staticmethod
    def flight_contribution_points(score):
        """Configurable diminishing curve: recommended score 14 -> 10 points, no hard ten cap."""
        import os
        from math import isqrt
        reference_score = os.getenv('BMC_FLIGHT_REWARD_REFERENCE_SCORE', '14')
        reference_points = os.getenv('BMC_FLIGHT_REWARD_REFERENCE_POINTS', '10')
        if not reference_score.isascii() or not reference_score.isdigit() or not reference_points.isascii() or not reference_points.isdigit():
            raise ValueError('Invalid flight reward curve')
        unit, points = int(reference_score), int(reference_points)
        if not 1 <= unit <= 10000 or not 1 <= points <= 100 or type(score) is not int or not 0 <= score <= 10000:
            raise ValueError('Invalid flight contribution or reward curve')
        return isqrt(score * points * points // unit)

    def credit_game_result(self, event, rewards=None):
        """Service-authenticated results. Reward policy belongs to this server, never the packet.

        Existing local game scores/coins are not website points. Empty policy records
        results with zero points until the operator configures the reviewed amounts.
        Bans control participation, not replay of an already earned durable result.
        """
        import json
        from uuid import UUID
        if not isinstance(event, dict) or set(event) != {'uid', 'game', 'session', 'win', 'score', 'difficulty', 'seconds'}:
            raise ValueError('Invalid result fields')
        self.validate_uid(event['uid'])
        game, session = event['game'], event['session']
        if game not in ('zombie-challenge', 'outbreak', 'flight', 'horse_racing') or not isinstance(session, str):
            raise ValueError('Unknown game or invalid session')
        parsed = UUID(session)
        if str(parsed) != session or parsed.version != 4:
            raise ValueError('Canonical UUID4 session required')
        if type(event['win']) is not bool:
            raise ValueError('Invalid win')
        for key, maximum in (('score', 10**12), ('difficulty', {'zombie-challenge': 4, 'outbreak': 3, 'flight': 5, 'horse_racing': 5}[game]), ('seconds', 10**9)):
            if type(event[key]) is not int or not 0 <= event[key] <= maximum:
                raise ValueError('Invalid result ' + key)
        if game == 'flight' and (event['win'] or event['score'] > 10000):
            raise ValueError('Flight contribution requires no declared win and bounded server score')
        if game == 'horse_racing' and (not 1 <= event['difficulty'] <= 5 or not 1 <= event['score'] <= 10 or event['win'] != (event['score'] == 10)):
            raise ValueError('Horse score must be 1..10, winner 10, difficulty 1..5')
        policy = {} if rewards is None else rewards
        if not isinstance(policy, dict) or any(name not in ('zombie-challenge', 'outbreak') for name in policy):
            raise ValueError('Invalid reward policy')
        for name, amounts in policy.items():
            if not isinstance(amounts, dict) or any(str(level) not in [str(n) for n in range(5 if name == 'zombie-challenge' else 4)] or type(amount) is not int or not 0 <= amount <= 10000 for level, amount in amounts.items()):
                raise ValueError('Invalid reward policy')
        points = self.flight_contribution_points(event['score']) if game == 'flight' else event['score'] if game == 'horse_racing' else (policy.get(game, {}).get(str(event['difficulty']), 0) if event['win'] else 0)
        canonical = json.dumps(event, sort_keys=True, separators=(',', ':'))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT event,points FROM minigame_result_ledger WHERE uid=? AND game=? AND session=?', (event['uid'], game, session)).fetchone()
            if old is not None:
                if old['event'] != canonical:
                    raise ValueError('Conflicting result replay')
                return {'credited': False, 'points': old['points']}
            rows = db.execute('SELECT subject FROM player_profiles WHERE uid=?', (event['uid'],)).fetchall()
            if len(rows) != 1:
                raise LookupError('Player must sign in to the website first')
            db.execute('INSERT INTO minigame_result_ledger(uid,game,session,event,points) VALUES(?,?,?,?,?)', (event['uid'], game, session, canonical, points))
            db.execute('UPDATE player_profiles SET points=points+?, updated_at=CURRENT_TIMESTAMP WHERE subject=?', (points, rows[0]['subject']))
        return {'credited': True, 'points': points}

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

    @staticmethod
    def _player(db, uid):
        rows = db.execute('SELECT subject FROM player_profiles WHERE uid=?', (uid,)).fetchall()
        if len(rows) != 1:
            raise LookupError('Player must sign in to the website first')
        return rows[0]['subject']

    @staticmethod
    def _sync_public(row):
        if row is None:
            return {'revision': 0, 'state': 'not_requested', 'observedLevel': None, 'observedAt': None}
        state = row['state']
        # Success is an observation, not a promise that an offline server still has this level.
        if row['observed_at'] and state in ('applied', 'changed_in_game'):
            observed = datetime.fromisoformat(row['observed_at'])
            if datetime.now(timezone.utc) - observed > timedelta(minutes=5):
                state = 'stale'
        return {'revision': row['revision'], 'state': state, 'observedLevel': row['observed_level'],
                'observedAt': row['observed_at'], 'error': row['error']}

    def op_status(self, uid):
        self.validate_uid(uid)
        with self.connect() as db:
            row = db.execute('SELECT * FROM game_op_sync WHERE uid=?', (uid,)).fetchone()
            permission = db.execute('SELECT game_op_level FROM platform_permissions WHERE uid=?', (uid,)).fetchone()
            observation = db.execute('SELECT * FROM game_op_observations WHERE uid=?', (uid,)).fetchone()
        sync = self._sync_public(row)
        if observation:
            sync['observedLevel'] = observation['level']
            sync['observedAt'] = observation['observed_at']
            stale = datetime.now(timezone.utc) - datetime.fromisoformat(observation['observed_at']) > timedelta(minutes=5)
            if row is None:
                sync['state'] = 'stale' if stale else 'observed'
            elif row['state'] in ('applied','changed_in_game'):
                sync['state'] = 'stale' if stale else ('applied' if observation['level'] == row['desired_level'] else 'changed_in_game')
        # Actual-state freshness is independent of the latest authorization event's outcome.
        # A pending/failed new event must not make an old game observation look current.
        sync['observedState'] = 'unknown'
        if sync['observedLevel'] is not None and sync['observedAt']:
            stale = datetime.now(timezone.utc) - datetime.fromisoformat(sync['observedAt']) > timedelta(minutes=5)
            sync['observedState'] = 'stale' if stale else 'fresh'
        return {'desiredLevel': permission[0] if permission else 0, 'sync': sync}

    def op_target(self, actor_uid, uid):
        self.validate_uid(actor_uid); self.validate_uid(uid)
        with self.connect() as db:
            actor = db.execute('SELECT platform_admin FROM platform_permissions WHERE uid=?', (actor_uid,)).fetchone()
            if not actor or not actor[0]:
                raise PermissionError('Platform administrator required')
            subject = self._player(db, uid)
            identity = db.execute('SELECT username,nickname FROM oidc_web_sessions WHERE uid=? AND subject=? ORDER BY expires_at DESC LIMIT 1', (uid, subject)).fetchone()
        if identity is None:
            raise LookupError('Target identity is unavailable; ask the player to sign in again')
        return {'uid': str(uid), 'username': identity['username'], 'displayName': identity['nickname'],
                'offlineUuid': offline_uuid(uid), 'identityConfirmation': hashlib.sha256(subject.encode()).hexdigest(),
                **self.op_status(uid)}

    def set_op(self, actor_uid, target_uid, level, expected_revision, expected_level, identity_confirmation, reason):
        self.validate_uid(actor_uid); self.validate_uid(target_uid)
        if type(level) is not int or not 0 <= level <= 4 or type(expected_level) is not int or not 0 <= expected_level <= 4:
            raise ValueError('OP level must be an integer from 0 to 4')
        if type(expected_revision) is not int or not 0 <= expected_revision < 9007199254740991:
            raise ValueError('Invalid expected revision')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 300:
            raise ValueError('Provide a reason of 1-300 characters')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT platform_admin FROM platform_permissions WHERE uid=?', (actor_uid,)).fetchone()
            if not actor or not actor[0]:
                raise PermissionError('Platform administrator required')
            subject = self._player(db, target_uid)
            if identity_confirmation != hashlib.sha256(subject.encode()).hexdigest():
                raise RuntimeError('Target identity changed; look up the UID again')
            old = db.execute('SELECT * FROM game_op_sync WHERE uid=?', (target_uid,)).fetchone()
            permission = db.execute('SELECT game_op_level FROM platform_permissions WHERE uid=?', (target_uid,)).fetchone()
            before = permission[0] if permission else 0
            if expected_revision != (old['revision'] if old else 0) or expected_level != before:
                raise RuntimeError('Permissions changed; look up the UID again')
            revision = expected_revision + 1
            # Only this column changes. A game OP never acquires platform administration.
            db.execute('INSERT INTO platform_permissions(uid,game_op_level) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET game_op_level=excluded.game_op_level', (target_uid, level))
            db.execute("""INSERT INTO game_op_sync(uid,subject,revision,desired_level,state) VALUES(?,?,?,?,'pending')
                ON CONFLICT(uid) DO UPDATE SET subject=excluded.subject,revision=excluded.revision,
                desired_level=excluded.desired_level,state='pending',error=NULL,updated_at=CURRENT_TIMESTAMP""", (target_uid,subject,revision,level))
            db.execute('INSERT INTO game_op_audit(actor_uid,target_uid,revision,before_level,after_level,reason) VALUES(?,?,?,?,?,?)', (actor_uid,target_uid,revision,before,level,reason.strip()))
        return self.op_status(target_uid)

    def op_feed(self, after_uid=0):
        if type(after_uid) is not int or not 0 <= after_uid <= 9999999999999999:
            raise ValueError('Invalid cursor')
        with self.connect() as db:
            rows = db.execute('SELECT * FROM game_op_sync WHERE uid>? ORDER BY uid LIMIT 101', (after_uid,)).fetchall()
            records = []
            for row in rows[:100]:
                if self._player(db, row['uid']) != row['subject']:
                    raise RuntimeError('OP target identity changed')
                records.append({'uid':str(row['uid']), 'offlineUuid':offline_uuid(row['uid']),
                                'revision':row['revision'], 'level':row['desired_level']})
        return {'records':records, 'nextUid':str(rows[99]['uid']) if len(rows)>100 else None}

    def acknowledge_op(self, uid, revision, applied, observed_level, error):
        self.validate_uid(uid)
        if type(revision) is not int or not 1 <= revision <= 9007199254740991 or type(applied) is not bool:
            raise ValueError('Invalid acknowledgement')
        if observed_level is not None and (type(observed_level) is not int or not 0 <= observed_level <= 4):
            raise ValueError('Invalid observed OP level')
        if applied and observed_level is None:
            raise ValueError('Successful acknowledgement requires an observation')
        # Fixed error codes keep credentials and arbitrary remote text out of the database/UI.
        if error not in (None, 'apply_failed', 'identity_mismatch', 'journal_failed'):
            raise ValueError('Invalid sync error')
        if applied == bool(error):
            raise ValueError('Invalid sync outcome')
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM game_op_sync WHERE uid=?', (uid,)).fetchone()
            if old is None or old['revision'] != revision:
                raise RuntimeError('Stale OP acknowledgement')
            if self._player(db, uid) != old['subject']:
                raise RuntimeError('OP target identity changed')
            if applied:
                db.execute('INSERT INTO game_op_observations VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET level=excluded.level,observed_at=excluded.observed_at', (uid,observed_level,now))
            state = ('applied' if observed_level == old['desired_level'] else 'changed_in_game') if applied else 'failed'
            # Repeated observations refresh freshness; identical retries do not duplicate the audit.
            if old['state'] != state or old['observed_level'] != observed_level or old['error'] != error:
                db.execute('INSERT INTO game_op_sync_audit(target_uid,revision,applied,observed_level,error) VALUES(?,?,?,?,?)', (uid,revision,int(applied),observed_level,error))
            db.execute('UPDATE game_op_sync SET state=?,observed_level=?,observed_at=?,error=? WHERE uid=?', (state,observed_level,now,error,uid))
        return self.op_status(uid)

    def observe_ops(self, records):
        if not isinstance(records, list) or len(records) > 100:
            raise ValueError('Invalid observation batch')
        from .game_identity import uid_login_name
        validated = []
        seen = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {'uid','offlineUuid','level'}:
                raise ValueError('Invalid observation fields')
            text = record['uid']
            if not isinstance(text, str) or not text.isascii() or not text.isdecimal():
                raise ValueError('Invalid UID')
            uid = int(text)
            if uid_login_name(uid) != text or record['offlineUuid'] != offline_uuid(uid) or uid in seen:
                raise ValueError('Invalid observed identity')
            if type(record['level']) is not int or not 0 <= record['level'] <= 4:
                raise ValueError('Invalid observed level')
            seen.add(uid); validated.append((uid,record['level']))
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Unknown website users are not auto-created by game reports.
            known = [(uid,level,now) for uid,level in validated
                     if len(db.execute('SELECT subject FROM player_profiles WHERE uid=?',(uid,)).fetchall()) == 1]
            db.executemany('INSERT INTO game_op_observations VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET level=excluded.level,observed_at=excluded.observed_at', known)
        return len(known)
