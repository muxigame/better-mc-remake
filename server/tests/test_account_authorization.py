import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main
from app.oidc import WebsiteAccount, WebsiteAuthStore


class AccountAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WebsiteAuthStore(Path(self.temp.name) / 'accounts.db')
        self.accounts = [WebsiteAccount(
            subject=f'account-authority-{uid}', uid=uid, username=f'player-{uid}',
            nickname='Fixture', game_name=str(uid), email=None,
            email_verified=False, role='player') for uid in (10000, 10001)]
        self.store_patch = patch.object(main, 'web_auth_store', self.store)
        self.store_patch.start()
        self.env_patch = patch.dict(os.environ, {
            'BMC_PUBLIC_URL': 'http://testserver', 'BMC_SERVE_WEB': '1'})
        self.env_patch.start()
        self.client = TestClient(main.app, follow_redirects=False)

    def tearDown(self):
        self.client.close()
        self.env_patch.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    def test_same_bearer_actor_for_account_profile_and_social(self):
        headers = {'authorization': 'Bearer fixture-current-account'}
        with patch.object(main.oidc_client, 'userinfo', return_value=self.accounts[1]) as userinfo:
            for path in ('/api/v1/auth/me', '/api/v1/player/profile'):
                response = self.client.get(path, headers=headers)
                self.assertEqual(200, response.status_code)
                self.assertEqual(10001, response.json()['user']['uid'])
            response = self.client.get('/api/v1/player/social', headers=headers)
            self.assertEqual('10001', response.json()['selfUid'])
            self.assertEqual(200, self.client.get('/account.html', headers=headers).status_code)
            self.assertEqual('/account.html', self.client.get('/api/v1/auth/entry', headers=headers).headers['location'])
            self.assertEqual(403, self.client.get('/admin.html', headers=headers).status_code)
            self.assertTrue(all(call.args == ('fixture-current-account',) for call in userinfo.call_args_list))
        self.assertNotIn('bmc_session', self.client.cookies)

    def test_explicit_bearer_overrides_previous_browser_account(self):
        self.client.cookies.set('bmc_session', self.store.create_session(self.accounts[0]))
        with patch.object(main.oidc_client, 'userinfo', return_value=self.accounts[1]):
            for path in ('/api/v1/auth/me', '/api/v1/player/profile'):
                response = self.client.get(path, headers={'authorization': 'Bearer fixture-current-account'})
                self.assertEqual(10001, response.json()['user']['uid'])
            response = self.client.get('/api/v1/player/social', headers={'authorization': 'Bearer fixture-current-account'})
            self.assertEqual('10001', response.json()['selfUid'])

    def test_invalid_or_expired_bearer_never_falls_back_to_cookie(self):
        self.client.cookies.set('bmc_session', self.store.create_session(self.accounts[0]))
        paths = ('/api/v1/auth/me', '/api/v1/player/profile', '/api/v1/player/social', '/account.html', '/api/v1/auth/entry')
        with patch.object(main.oidc_client, 'userinfo', side_effect=RuntimeError('expired')):
            for path in paths:
                self.assertEqual(401, self.client.get(path, headers={'authorization': 'Bearer expired-fixture'}).status_code)
        with patch.object(main.oidc_client, 'userinfo') as userinfo:
            for value in ('Bearer ', 'Basic fixture', ''):
                self.assertEqual(401, self.client.get('/api/v1/auth/me', headers={'authorization': value}).status_code)
            userinfo.assert_not_called()

    def test_cookie_navigation_still_works_and_credentials_are_not_cacheable(self):
        self.assertEqual(303, self.client.get('/account.html').status_code)
        self.client.cookies.set('bmc_session', self.store.create_session(self.accounts[0]))
        self.assertEqual(200, self.client.get('/account.html').status_code)
        response = self.client.get('/api/v1/auth/me')
        self.assertEqual(10000, response.json()['user']['uid'])
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertIn('Authorization', response.headers['vary'])


if __name__ == '__main__':
    unittest.main()
