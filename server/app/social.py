"""Business-owned UID friendships. No auth-center, claims, chat or currency writes."""
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .game_identity import offline_uuid, uid_login_name


def social_uid(value):
    # Strings are the wire format, because a 16-digit UID may exceed JS's safe integer.
    if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{4,15}', value):
        value = int(value)
    uid_login_name(value)  # Reject bool, floats, whitespace, zeroes and out-of-range integers.
    return value


class SocialStore:
    PRESENCE_TTL = 90

    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS social_players (
                uid INTEGER PRIMARY KEY, subject TEXT UNIQUE, display_name TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS social_requests (
                sender INTEGER NOT NULL, recipient INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(sender,recipient), CHECK(sender != recipient));
              CREATE TABLE IF NOT EXISTS social_friends (
                low_uid INTEGER NOT NULL, high_uid INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(low_uid,high_uid), CHECK(low_uid < high_uid));
              CREATE TABLE IF NOT EXISTS social_blocks (
                owner INTEGER NOT NULL, target INTEGER NOT NULL,
                PRIMARY KEY(owner,target), CHECK(owner != target));
              CREATE TABLE IF NOT EXISTS social_presence (
                uid INTEGER PRIMARY KEY, uuid TEXT NOT NULL, game_name TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS social_presence_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), observed REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS social_receipts (
                actor INTEGER NOT NULL, request TEXT NOT NULL, peer INTEGER NOT NULL,
                action TEXT NOT NULL, changed INTEGER NOT NULL, observed REAL NOT NULL,
                PRIMARY KEY(actor,request));
              CREATE INDEX IF NOT EXISTS social_requests_recipient ON social_requests(recipient);
              CREATE INDEX IF NOT EXISTS social_friends_high ON social_friends(high_uid);
              CREATE INDEX IF NOT EXISTS social_blocks_target ON social_blocks(target);
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

    def register(self, account):
        uid = social_uid(account.uid)
        if not isinstance(account.subject, str) or not account.subject:
            raise ValueError('Verified subject required')
        name = (account.nickname or account.username or str(uid))[:80]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT subject FROM social_players WHERE uid=?', (uid,)).fetchone()
            if existing and existing['subject'] not in (None, account.subject):
                raise PermissionError('UID binding conflict')
            try:
                db.execute('INSERT INTO social_players VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET subject=excluded.subject,display_name=excluded.display_name',
                           (uid, account.subject, name))
            except sqlite3.IntegrityError as error:
                raise PermissionError('Subject binding conflict') from error
        return uid

    @staticmethod
    def _pair(db, actor, peer, known=True):
        actor, peer = social_uid(actor), social_uid(peer)
        if actor == peer:
            raise ValueError('Self relationship is not allowed')
        if known and any(db.execute('SELECT 1 FROM social_players WHERE uid=?', (uid,)).fetchone() is None for uid in (actor, peer)):
            raise LookupError('Player must sign in or join through the trusted server first')
        return actor, peer, min(actor, peer), max(actor, peer)

    @staticmethod
    def _blocked(db, actor, peer):
        return db.execute('SELECT 1 FROM social_blocks WHERE (owner=? AND target=?) OR (owner=? AND target=?)',
                          (actor, peer, peer, actor)).fetchone() is not None

    @staticmethod
    def _friends(db, low, high):
        return db.execute('SELECT 1 FROM social_friends WHERE low_uid=? AND high_uid=?', (low, high)).fetchone() is not None

    def mutate(self, actor, peer, action, request=None):
        if request is not None:
            if not isinstance(request, str) or str(uuid.UUID(request)) != request:
                raise ValueError('Canonical UUID idempotency key required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor, peer = social_uid(actor), social_uid(peer)
            if request is not None:
                receipt = db.execute('SELECT * FROM social_receipts WHERE actor=? AND request=?', (actor, request)).fetchone()
                if receipt:
                    if receipt['peer'] != peer or receipt['action'] != action:
                        raise ValueError('Idempotency key reused for a different action')
                    return bool(receipt['changed'])
                if db.execute('SELECT COUNT(*) FROM social_receipts WHERE actor=? AND observed>?', (actor, self.clock()-60)).fetchone()[0] >= 120:
                    raise ValueError('Social mutation rate limit reached')
            db.execute("DELETE FROM social_requests WHERE created_at < datetime('now','-7 days')")
            changed = self._mutate(db, actor, peer, action)
            if request is not None:
                db.execute('INSERT INTO social_receipts VALUES(?,?,?,?,?,?)', (actor, request, peer, action, int(changed), self.clock()))
            return changed

    def _mutate(self, db, actor, peer, action):
        # Caller holds BEGIN IMMEDIATE; relationship and successful receipt commit together.
        actor, peer, low, high = self._pair(db, actor, peer, known=action in ('request', 'accept'))
        if action == 'request':
            if self._blocked(db, actor, peer):
                raise PermissionError('Relationship unavailable')
            if self._friends(db, low, high):
                return False
            if db.execute('SELECT 1 FROM social_requests WHERE sender=? AND recipient=?', (actor, peer)).fetchone():
                return False
            for uid in (actor, peer):
                if db.execute('SELECT COUNT(*) FROM social_requests WHERE sender=? OR recipient=?', (uid, uid)).fetchone()[0] >= 100:
                    raise ValueError('Pending request limit reached')
            db.execute('INSERT INTO social_requests(sender,recipient) VALUES(?,?)', (actor, peer))
        elif action == 'accept':
            if self._blocked(db, actor, peer):
                raise PermissionError('Relationship unavailable')
            if self._friends(db, low, high):
                return False
            if not db.execute('SELECT 1 FROM social_requests WHERE sender=? AND recipient=?', (peer, actor)).fetchone():
                raise LookupError('No incoming request')
            for uid in (actor, peer):
                if db.execute('SELECT COUNT(*) FROM social_friends WHERE low_uid=? OR high_uid=?', (uid, uid)).fetchone()[0] >= 500:
                    raise ValueError('Friend limit reached')
            db.execute('INSERT INTO social_friends(low_uid,high_uid) VALUES(?,?)', (low, high))
            db.execute('DELETE FROM social_requests WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)', (actor, peer, peer, actor))
        elif action == 'remove':
            return bool(db.execute('DELETE FROM social_friends WHERE low_uid=? AND high_uid=?', (low, high)).rowcount)
        elif action == 'cancel':
            return bool(db.execute('DELETE FROM social_requests WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)', (actor, peer, peer, actor)).rowcount)
        elif action == 'block':
            if not db.execute('SELECT 1 FROM social_blocks WHERE owner=? AND target=?', (actor,peer)).fetchone() and db.execute('SELECT COUNT(*) FROM social_blocks WHERE owner=?', (actor,)).fetchone()[0] >= 1000:
                raise ValueError('Block limit reached')
            changed = db.execute('INSERT OR IGNORE INTO social_blocks VALUES(?,?)', (actor, peer)).rowcount
            db.execute('DELETE FROM social_friends WHERE low_uid=? AND high_uid=?', (low, high))
            db.execute('DELETE FROM social_requests WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)', (actor, peer, peer, actor))
            return bool(changed)
        elif action == 'unblock':
            return bool(db.execute('DELETE FROM social_blocks WHERE owner=? AND target=?', (actor, peer)).rowcount)
        else:
            raise ValueError('Unknown relationship action')
        return True

    def _presence_available(self, db):
        row = db.execute('SELECT observed FROM social_presence_meta WHERE singleton=1').fetchone()
        return row is not None and 0 <= self.clock() - row[0] < self.PRESENCE_TTL

    def _peer(self, db, uid, fresh):
        row = db.execute('SELECT display_name FROM social_players WHERE uid=?', (uid,)).fetchone()
        online = fresh and db.execute('SELECT 1 FROM social_presence WHERE uid=?', (uid,)).fetchone() is not None
        return {'uid': str(uid), 'displayName': row[0] if row else str(uid),
                'gameName': uid_login_name(uid), 'uuid': offline_uuid(uid), 'online': bool(online)}

    def snapshot(self, actor):
        actor = social_uid(actor)
        with self.connect() as db:
            db.execute('BEGIN')
            fresh = self._presence_available(db)
            friends = [r[0] for r in db.execute('SELECT CASE WHEN low_uid=? THEN high_uid ELSE low_uid END FROM social_friends WHERE low_uid=? OR high_uid=? ORDER BY 1', (actor, actor, actor))]
            incoming = [r[0] for r in db.execute("SELECT sender FROM social_requests WHERE recipient=? AND created_at >= datetime('now','-7 days') ORDER BY sender", (actor,))]
            outgoing = [r[0] for r in db.execute("SELECT recipient FROM social_requests WHERE sender=? AND created_at >= datetime('now','-7 days') ORDER BY recipient", (actor,))]
            blocked = [r[0] for r in db.execute('SELECT target FROM social_blocks WHERE owner=? ORDER BY target', (actor,))]
            online = [r[0] for r in db.execute('SELECT uid FROM social_presence WHERE uid!=? ORDER BY uid', (actor,))] if fresh else []
            online = [uid for uid in online if not self._blocked(db,actor,uid)]
            return {'version': 1, 'selfUid': str(actor), 'authenticated': True,
                    'identityMode': 'platform-uid', 'presenceAvailable': fresh,
                    'limits': {'friends':500,'pending':100,'blocked':1000,'online':1024},
                    'onlinePlayers': [self._peer(db,uid,fresh) for uid in online],
                    **{key: [self._peer(db, uid, fresh) for uid in values] for key, values in
                       [('friends', friends), ('incoming', incoming), ('outgoing', outgoing), ('blocked', blocked)]}}

    def presence(self, players):
        if not isinstance(players, list) or len(players) > 1024:
            raise ValueError('Invalid trusted player snapshot')
        checked = []
        seen = set()
        for row in players:
            if not isinstance(row, dict) or set(row) != {'uid', 'uuid', 'gameName'}:
                raise ValueError('Invalid trusted player fields')
            uid = social_uid(row['uid'])
            if uid in seen or row['uuid'] != offline_uuid(uid) or row['gameName'] != uid_login_name(uid):
                raise ValueError('Invalid verified game identity')
            seen.add(uid)
            checked.append((uid, row['uuid'], row['gameName']))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM social_presence')
            db.executemany('INSERT INTO social_presence VALUES(?,?,?)', checked)
            db.executemany('INSERT OR IGNORE INTO social_players(uid,display_name) VALUES(?,?)', [(uid, str(uid)) for uid, _, _ in checked])
            db.execute('INSERT INTO social_presence_meta VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET observed=excluded.observed', (self.clock(),))

    def eligibility(self, actor, peer, source):
        if source not in ('online', 'friends'):
            raise ValueError('Unknown invitation source')
        with self.connect() as db:
            db.execute('BEGIN')
            actor, peer, low, high = self._pair(db, actor, peer)
            fresh = self._presence_available(db)
            connected = fresh and all(db.execute('SELECT 1 FROM social_presence WHERE uid=?', (uid,)).fetchone() is not None for uid in (actor, peer))
            friend = self._friends(db, low, high)
            blocked = self._blocked(db, actor, peer)
            return {'actorUid': str(actor), 'targetUid': str(peer), 'source': source,
                    'allowed': bool(connected and not blocked and (source == 'online' or friend)),
                    'isFriend': friend, 'blocked': blocked, 'presenceAvailable': fresh,
                    'target': self._peer(db, peer, fresh)}
