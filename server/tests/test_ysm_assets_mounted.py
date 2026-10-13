"""Real app mounting, existing account actor selection and protected-page boundary."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main
from app.oidc import WebsiteAccount, WebsiteAuthStore
from app.ysm_assets import YsmAssetStore


class MountedYsmAssetTests(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).resolve().parents[2] / 'build' / 'owner-131-ysm-tests'
        folder.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='mounted-', dir=folder)
        self.root = Path(self.temp.name)
        self.auth = WebsiteAuthStore(self.root / 'website.sqlite3')
        self.assets = YsmAssetStore(self.root / 'separate-user-assets')
        self.accounts = [WebsiteAccount(subject=f'fixture-{uid}', uid=uid,
            username=f'player-{uid}', nickname='Fixture', game_name=str(uid),
            email=None, email_verified=False, role='player') for uid in (10000, 10001)]
        self.patches = [patch.object(main, 'web_auth_store', self.auth),
            patch.object(main, 'ysm_asset_store', self.assets),
            patch.dict(os.environ, BMC_PUBLIC_URL='http://testserver', BMC_SERVE_WEB='1')]
        for p in self.patches:
            p.start()
        self.client = TestClient(main.app, follow_redirects=False)

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def upload(self, **kwargs):
        headers = {'content-type': 'application/octet-stream',
                   'idempotency-key': 'fixture-upload-10000', 'origin': 'http://testserver'}
        headers.update(kwargs.pop('headers', {}))
        return self.client.post('/api/v1/player/ysm/models?filename=fixture.ysm',
                                content=b'opaque-unvalidated-fixture', headers=headers, **kwargs)

    def test_existing_bearer_selects_uid_over_older_browser_cookie(self):
        self.client.cookies.set('bmc_session', self.auth.create_session(self.accounts[1]))
        with patch.object(main.oidc_client, 'userinfo', return_value=self.accounts[0]) as info:
            response = self.upload(headers={'authorization': 'Bearer original-fixture-account'})
            self.assertEqual(200, response.status_code, response.text)
            models = self.client.get('/api/v1/player/ysm/models',
                headers={'authorization': 'Bearer original-fixture-account'})
            self.assertEqual(1, len(models.json()['models']))
            self.assertEqual(200, self.client.get('/ysm-models.html',
                headers={'authorization': 'Bearer original-fixture-account'}).status_code)
            self.assertTrue(all(c.args == ('original-fixture-account',) for c in info.call_args_list))
        self.assertEqual([], self.client.get('/api/v1/player/ysm/models').json()['models'])

    def test_invalid_bearer_does_not_fall_back_to_cookie(self):
        self.client.cookies.set('bmc_session', self.auth.create_session(self.accounts[0]))
        with patch.object(main.oidc_client, 'userinfo', side_effect=RuntimeError('expired')):
            for path in ('/ysm-models.html', '/api/v1/player/ysm/models', '/api/v1/ysm/models/public'):
                self.assertEqual(401, self.client.get(path,
                    headers={'authorization': 'Bearer expired-fixture'}).status_code)

    def test_anonymous_api_denied_and_page_uses_existing_oidc_login(self):
        self.assertEqual(401, self.client.get('/api/v1/player/ysm/models').status_code)
        response = self.client.get('/ysm-models.html')
        self.assertEqual(303, response.status_code)
        self.assertIn('/authorize?', response.headers['location'])
        self.assertNotIn('bmc_session', response.cookies)

    def test_cookie_write_has_csrf_origin_boundary(self):
        self.client.cookies.set('bmc_session', self.auth.create_session(self.accounts[0]))
        self.assertEqual(403, self.upload(headers={'origin': 'https://untrusted.example'}).status_code)
        self.assertEqual(200, self.upload().status_code)

    def test_pending_upload_never_appears_in_public_or_download(self):
        self.client.cookies.set('bmc_session', self.auth.create_session(self.accounts[0]))
        response = self.upload()
        self.assertEqual(200, response.status_code, response.text)
        model = response.json()['model']
        self.assertEqual('private', model['visibility'])
        self.assertEqual('pending', model['validationStatus'])
        self.assertFalse(model['canDownload'])
        self.assertFalse(model['canSelect'])
        self.assertEqual([], self.client.get('/api/v1/ysm/models/public').json()['models'])
        self.assertNotEqual(200, self.client.get(
            '/api/v1/player/ysm/models/' + model['id'] + '/download').status_code)

    def test_uid_from_query_cannot_override_authenticated_owner(self):
        self.client.cookies.set('bmc_session', self.auth.create_session(self.accounts[0]))
        response = self.client.post('/api/v1/player/ysm/models?filename=fixture.ysm&uid=10001',
            content=b'opaque-fixture', headers={'origin': 'http://testserver',
            'content-type': 'application/octet-stream', 'idempotency-key': 'fixture-owner-forgery'})
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self.assets.list_models(10000)['models'])
        self.assertEqual([], self.assets.list_models(10001)['models'])

    def test_old_page_cannot_upload_under_a_new_cookie_account(self):
        self.client.cookies.set('bmc_session', self.auth.create_session(self.accounts[1]))
        response = self.upload(headers={'x-muxi-expected-uid': '10000'})
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual('account_changed', response.json()['detail'])
        self.assertEqual([], self.assets.list_models(10000)['models'])
        self.assertEqual([], self.assets.list_models(10001)['models'])
        self.assertEqual([], list(self.assets.incoming.iterdir()))

    def test_web_image_page_is_internal_and_account_links_to_manager(self):
        self.client.cookies.set('bmc_session', self.auth.create_session(self.accounts[0]))
        with patch.dict(os.environ, BMC_SERVE_WEB='0'):
            response = self.client.get('/ysm-models.html')
            self.assertEqual('/__protected/ysm-models.html', response.headers['x-accel-redirect'])
        response = self.client.get('/account.html')
        self.assertIn('href="/ysm-models.html"', response.text)
        self.assertIn('no-store', response.headers['cache-control'])

    def test_catalog_and_player_pages_are_not_shared_cacheable(self):
        self.client.cookies.set('bmc_session', self.auth.create_session(self.accounts[0]))
        for path in ('/ysm-models.html', '/api/v1/player/ysm/models', '/api/v1/ysm/models/public'):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code)
            self.assertIn('no-store', response.headers['cache-control'])
            self.assertIn('Authorization', response.headers['vary'])

    def test_proxy_hops_allow_bounded_stream_and_keep_page_internal(self):
        server = Path(__file__).resolve().parents[1]
        for relative in ('web/nginx-container.conf', 'deploy/nginx-muxigame.conf',
                         'deploy/nginx-muxigame-http.conf'):
            config = (server / relative).read_text(encoding='utf-8-sig')
            upload = re.search(r'location \^~ /api/v1/player/ysm/ \{([^}]+)\}', config)
            self.assertIsNotNone(upload, relative)
            self.assertIn('client_max_body_size 64m;', upload.group(1))
            self.assertIn('proxy_request_buffering off;', upload.group(1))
            self.assertIn('proxy_pass ', upload.group(1))
        config = (server / 'web/nginx-container.conf').read_text(encoding='utf-8-sig')
        internal = re.search(r'location = /__protected/ysm-models.html \{([^}]+)\}', config)
        self.assertIn('internal;', internal.group(1))
        public = re.search(r'location = /ysm-models.html \{([^}]+)\}', config)
        self.assertIn('proxy_pass http://battermc_api;', public.group(1))
        self.assertNotIn('alias ', public.group(1))


if __name__ == '__main__':
    unittest.main()
