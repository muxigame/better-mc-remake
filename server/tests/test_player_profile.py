import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from app.main import app
from app.oidc import WebsiteAccount, WebsiteAuthStore
from app.game_identity import uid_login_name, offline_uuid

class PlayerProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='bmc-player-')
        self.store=WebsiteAuthStore(Path(self.tmp.name)/'bmc.db')
        self.account=WebsiteAccount(subject='sub-10000',uid=10000,username='MuxiUser',nickname='洛可',game_name='MuxiUser',email='user@example.com',email_verified=True,role='player')
    def tearDown(self): self.tmp.cleanup()
    def test_numeric_uid_is_login_identity(self):
        p=self.store.player_profile(self.account).public()
        self.assertEqual('10000',p['gameName']);self.assertEqual('10000',p['loginName'])
        self.assertEqual('洛可',p['displayName']);self.assertEqual('platform-uid',p['identityMode'])
        self.assertEqual('bac84aa3-61d5-3a42-acb0-02ab44e17ee2',p['offlineUuid'])
    def test_rename_never_changes_uuid(self):
        before=self.store.player_profile(self.account).public()
        after=self.store.player_profile(replace(self.account,username='Changed',nickname='新昵称')).public()
        self.assertEqual(before['offlineUuid'],after['offlineUuid'])
        self.assertEqual('新昵称',after['displayName'])
    def test_duplicate_chinese_nicknames_have_distinct_uid_identities(self):
        a=self.store.player_profile(self.account)
        b=self.store.player_profile(replace(self.account,subject='sub-10001',uid=10001))
        self.assertNotEqual(a.public()['offlineUuid'],b.public()['offlineUuid'])
        self.assertEqual(a.display_name,b.display_name)
    def test_old_identity_and_business_data_remain_untouched(self):
        self.store.player_profile(self.account)
        with self.store.connect() as db:
            db.execute('UPDATE player_profiles SET game_name=?,points=?,balance_cents=? WHERE subject=?',('OldName',120,3456,self.account.subject))
        p=self.store.player_profile(self.account).public()
        self.assertEqual('OldName',p['legacyGameName']);self.assertEqual('10000',p['gameName'])
        self.assertEqual(120,p['points']);self.assertEqual(3456,p['balanceCents'])
        with self.store.connect() as db:
            self.assertEqual('OldName',db.execute('SELECT game_name FROM player_profiles').fetchone()[0])
    def test_game_name_write_api_is_removed(self):
        methods = set()
        for route in app.routes:
            if getattr(route, 'path', None) == '/api/v1/player/profile':
                methods.update(getattr(route, 'methods', set()) or set())
        self.assertIn('GET', methods)
        self.assertNotIn('PATCH', methods)
    def test_oidc_does_not_require_obsolete_game_name(self):
        p=WebsiteAccount.from_userinfo({'sub':'s','muxi_uid':10000,'username':'Roc','nickname':'洛可'})
        self.assertEqual('10000',p.game_name)
    def test_invalid_uid_does_not_truncate(self):
        for value in (True,'10000',9999,10000000000000000):
            with self.assertRaises(ValueError): uid_login_name(value)

if __name__=='__main__': unittest.main()
