import os
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.oidc import OidcClient, WebsiteAccount, WebsiteAuthStore, safe_return_to


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.append(dict(attrs).get('href', ''))


class AuthNavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bmc-auth-navigation-')
        self.store = WebsiteAuthStore(Path(self.tmp.name) / 'test.db')
        self.account = WebsiteAccount(
            subject='navigation-fixture', uid=10000, username='TestPlayer',
            nickname='测试玩家', game_name='TestPlayer', email=None,
            email_verified=False, role='player',
        )
        self.env = patch.dict(os.environ, {'BMC_PUBLIC_URL': 'http://testserver', 'BMC_SERVE_WEB': '1'})
        self.env.start()
        self.store_patch = patch.object(main, 'web_auth_store', self.store)
        self.store_patch.start()
        self.client = TestClient(main.app, follow_redirects=False)
        self.other_browser = TestClient(main.app, follow_redirects=False)
        self.oidc = OidcClient('https://account.muxigame.com', 'better-mc-web', 'fixture-only', 'http://testserver/api/v1/auth/callback')
        self.oidc_patch = patch.object(main, 'oidc_client', self.oidc)
        self.oidc_patch.start()

    def tearDown(self):
        self.client.close()
        self.other_browser.close()
        self.oidc_patch.stop()
        self.store_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def start(self, path='/account.html'):
        response = self.client.get('/api/v1/auth/entry', params={'return_to': path})
        self.assertEqual(303, response.status_code)
        target = urlsplit(response.headers['location'])
        self.assertEqual('account.muxigame.com', target.netloc)
        self.assertEqual('/oauth/authorize', target.path)
        query = parse_qs(target.query)
        self.assertEqual(['S256'], query['code_challenge_method'])
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertNotIn('玩家中心', response.text)
        return query['state'][0]

    def complete(self, state):
        with patch.object(self.oidc, 'exchange_code', return_value={'access_token': 'fixture-token'}), \
             patch.object(self.oidc, 'userinfo', return_value=self.account):
            return self.client.get('/api/v1/auth/callback', params={'state': state, 'code': 'fixture-code'})

    def test_guest_entry_goes_directly_to_authorization(self):
        self.start()

    def test_direct_account_url_has_no_guest_html(self):
        for url in ['/account.html', '/account']:
            response = self.client.get(url)
            self.assertEqual(303, response.status_code)
            self.assertEqual('account.muxigame.com', urlsplit(response.headers['location']).hostname)
            self.assertNotIn('game-name-form', response.text)

    def test_success_returns_to_player_center(self):
        result = self.complete(self.start())
        self.assertEqual('/account.html', result.headers['location'])
        page = self.client.get('/account.html')
        self.assertEqual(200, page.status_code)
        self.assertIn('game-name-form', page.text)
        self.assertIn('no-store', page.headers['cache-control'])
        self.assertNotIn('player-balance', page.text)

    def test_success_can_return_to_original_page_and_fragment(self):
        result = self.complete(self.start('/?view=release#download'))
        self.assertEqual('/?view=release#download', result.headers['location'])

    def test_authenticated_entry_does_not_start_another_oauth_flow(self):
        self.client.cookies.set('bmc_session', self.store.create_session(self.account))
        result = self.client.get('/api/v1/auth/entry')
        self.assertEqual('/account.html', result.headers['location'])
        self.assertNotIn('bmc_oauth_browser', result.headers.get('set-cookie', ''))

    def test_callback_is_bound_to_initiating_browser_and_one_time(self):
        state = self.start()
        with patch.object(self.oidc, 'exchange_code') as exchange:
            forged = self.other_browser.get('/api/v1/auth/callback', params={'state': state, 'code': 'fixture-code'})
            self.assertEqual('/?auth=invalid_state', forged.headers['location'])
            exchange.assert_not_called()
        self.assertEqual('/account.html', self.complete(state).headers['location'])
        self.assertEqual('/?auth=invalid_state', self.complete(state).headers['location'])

    def test_return_target_cannot_escape_origin(self):
        for value in ['https://evil.example/', '//evil.example/', '/\\evil.example', '/%2fexample.com', '/%255cexample.com', '/\r\nLocation: x', '/api/v1/auth/entry', '/__protected/account.html']:
            with self.subTest(value=value):
                self.assertEqual('/account.html', safe_return_to(value))

    def test_expired_state_is_rejected(self):
        state = self.start()
        with self.store.connect() as db:
            db.execute("UPDATE oidc_login_states SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual('/?auth=invalid_state', self.complete(state).headers['location'])

    def test_cancelled_login_returns_home_without_loop(self):
        result = self.client.get('/api/v1/auth/callback', params={'state': self.start(), 'error': 'access_denied'})
        self.assertEqual('/?auth=access_denied', result.headers['location'])

    def test_production_page_uses_authenticated_internal_delivery(self):
        self.client.cookies.set('bmc_session', self.store.create_session(self.account))
        with patch.dict(os.environ, {'BMC_SERVE_WEB': '0'}):
            page = self.client.get('/account.html')
        self.assertEqual('/__protected/account.html', page.headers['x-accel-redirect'])
        self.assertEqual('', page.text)

    def test_admin_page_requires_admin_role(self):
        self.client.cookies.set('bmc_session', self.store.create_session(self.account))
        self.assertEqual(403, self.client.get('/admin.html').status_code)

    def test_homepage_account_links_use_server_entry_even_without_js(self):
        parser = LinkParser()
        parser.feed((main.WEB_ROOT / 'index.html').read_text(encoding='utf-8-sig'))
        self.assertNotIn('/account.html', parser.hrefs)
        self.assertGreaterEqual(sum(href.startswith('/api/v1/auth/entry?') for href in parser.hrefs), 3)

    def test_reverse_proxy_guards_protected_files(self):
        conf = (main.WEB_ROOT / 'nginx-container.conf').read_text(encoding='utf-8')
        self.assertIn('location = /account.html {\n        proxy_pass http://battermc_api;', conf)
        self.assertIn('location = /__protected/account.html {\n        internal;', conf)


if __name__ == '__main__':
    unittest.main()
