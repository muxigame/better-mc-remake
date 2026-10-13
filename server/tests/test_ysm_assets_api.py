"""HTTP fixtures validate request boundaries, not real production accounts/YSM."""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DLL_HANDLES = [os.add_dll_directory(str(p)) for p in (Path(sys.executable).parent, Path(sys.executable).parent/'DLLs')
               if os.name == 'nt' and hasattr(os, 'add_dll_directory') and p.is_dir()]

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.ysm_assets import ValidationReceipt, YsmAssetStore
from app.ysm_assets_api import ysm_assets_router


TEST_ROOT = Path(__file__).resolve().parents[2] / 'build' / 'owner-131-ysm-assets'
TEST_ROOT.mkdir(parents=True, exist_ok=True)
DATA = b'synthetic opaque HTTP fixture, not a production model'
PROFILE = 'synthetic-http-validator-v1'
BASE = '/api/v1/player/ysm/models'


def fixture_account(request: Request):
    # Known synthetic credentials are deliberately confined to this test app.
    # Never interpret an actor field or query parameter as an authenticated UID.
    credential = request.headers.get('authorization')
    if credential is None:
        credential = request.cookies.get('bmc_session')
    fixtures = {'Bearer fixture-one':10000, 'Bearer fixture-two':10001,
                'fixture-one':10000, 'fixture-two':10001, 'Bearer invalid-bool':True}
    if credential not in fixtures:
        raise HTTPException(401, 'fixture_login_required')
    return SimpleNamespace(uid=fixtures[credential])


class YsmAssetHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.store = YsmAssetStore(Path(self.tmp.name), validation_profile=PROFILE)
        self.env = patch.dict(os.environ, {'BMC_PUBLIC_URL':'http://testserver'})
        self.env.start()
        self.app = FastAPI()
        self.app.include_router(ysm_assets_router(lambda:self.store, fixture_account))
        self.client = TestClient(self.app)
        self.counter = 0

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def key(self):
        self.counter += 1
        return 'synthetic-http-key-' + str(self.counter)

    def headers(self, **kwargs):
        result = {'authorization':'Bearer fixture-one', 'idempotency-key':self.key()}
        result.update(kwargs)
        return result

    def upload(self, **kwargs):
        headers = self.headers(**{'content-type':'application/octet-stream'})
        headers.update(kwargs.pop('headers', {}))
        params = {'filename':'model.ysm'}
        params.update(kwargs.pop('params', {}))
        return self.client.post(BASE, headers=headers, params=params, content=kwargs.pop('content',DATA), **kwargs)

    def mutate(self, method, model_id, suffix='', body=None, **kwargs):
        headers = self.headers(**{'content-type':'application/json'})
        headers.update(kwargs.pop('headers', {}))
        return self.client.request(method, BASE+'/'+model_id+suffix, headers=headers,
                                   json={} if body is None else body, **kwargs)

    def promote(self):
        self.store.promote_verified(ValidationReceipt(hashlib.sha256(DATA).hexdigest(), len(DATA), PROFILE, self.key(), True))

    def test_requires_current_account_and_rejects_boolean_uid(self):
        for path in (BASE, '/api/v1/ysm/models/public'):
            self.assertEqual(401, self.client.get(path).status_code)
            self.assertEqual(403, self.client.get(path, headers={'authorization':'Bearer invalid-bool'}).status_code)

    def test_raw_upload_default_private_and_upload_idempotency(self):
        key = self.key()
        first = self.upload(headers={'idempotency-key':key})
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual('private', first.json()['model']['visibility'])
        self.assertEqual('pending', first.json()['model']['validationStatus'])
        second = self.upload(headers={'idempotency-key':key})
        self.assertTrue(second.json()['replayed'])
        self.assertEqual(first.json()['model']['id'], second.json()['model']['id'])
        self.assertIn('no-store', first.headers['cache-control'])

    def test_invalid_upload_envelope_format_size_empty_and_actor_fields(self):
        cases = (({'params':{'filename':'../../x.ysm'}},400),
                 ({'params':{'filename':'x.exe.ysm'}},400),
                 ({'params':{'uid':'10001'}},400),
                 ({'params':{'visibility':'unlisted'}},400),
                 ({'headers':{'idempotency-key':'bad'}},400),
                 ({'headers':{'content-type':'multipart/form-data'}},415),
                 ({'headers':{'content-encoding':'gzip'}},415),
                 ({'content':b''},400),
                 ({'headers':{'content-length':'9'*5000}},413),
                 ({'headers':{'content-length':str(self.store.max_file_bytes+1)}},413))
        for kwargs, code in cases:
            response = self.upload(**kwargs)
            self.assertEqual(code, response.status_code, response.text)
        self.assertEqual([], self.store.list_models(10000)['models'])
        self.assertEqual([], list(self.store.incoming.glob('*.bin')))

    def test_actual_streamed_size_checked_without_content_length(self):
        self.store = YsmAssetStore(Path(self.tmp.name)/'limited', max_file_bytes=4)
        response = self.upload(content=iter([b'123',b'45']))
        self.assertEqual(413, response.status_code)
        self.assertEqual([], list(self.store.incoming.glob('*.bin')))

    def test_cookie_requires_origin_even_with_explicit_bearer(self):
        self.client.cookies.set('bmc_session','fixture-one')
        self.assertEqual(403, self.upload().status_code)
        self.assertEqual(403, self.upload(headers={'origin':'http://evil.example'}).status_code)
        self.assertEqual(200, self.upload(headers={'origin':'http://testserver'}).status_code)

    def test_expected_uid_is_only_optimistic_guard_and_mismatch_creates_nothing(self):
        self.client.cookies.set('bmc_session','fixture-two')
        response = self.upload(headers={'authorization':'Bearer fixture-two', 'origin':'http://testserver',
                                        'x-muxi-expected-uid':'10000'})
        self.assertEqual(409,response.status_code)
        self.assertEqual('account_changed',response.json()['detail'])
        self.assertEqual([],self.store.list_models(10000)['models'])
        self.assertEqual([],self.store.list_models(10001)['models'])
        self.assertEqual([],list(self.store.incoming.glob('*.bin')))
        for uid in ('true','010001','10001.0','10001 '):
            self.assertEqual(409,self.upload(headers={'origin':'http://testserver','x-muxi-expected-uid':uid}).status_code)
        self.assertEqual(200,self.upload(headers={'origin':'http://testserver','x-muxi-expected-uid':'10000'}).status_code)

    def test_expected_uid_also_guards_private_reads_after_cookie_switch(self):
        model_id = self.upload().json()['model']['id']
        self.promote()
        self.client.cookies.set('bmc_session','fixture-two')
        h = {'x-muxi-expected-uid':'10000'}
        for path in (BASE, BASE+'/'+model_id+'/download', '/api/v1/ysm/models/public'):
            response = self.client.get(path,headers=h)
            self.assertEqual(409,response.status_code)
            self.assertEqual('account_changed',response.json()['detail'])

    def test_bearer_can_be_used_without_origin_and_explicit_bad_origin_is_rejected(self):
        self.assertEqual(200, self.upload().status_code)
        for origin in ('null','http://evil.example','http://testserver@evil.example','http://testserver/path'):
            self.assertEqual(403, self.upload(headers={'origin':origin}).status_code)
        self.assertEqual(403, self.upload(headers={'sec-fetch-site':'cross-site'}).status_code)

    def test_cross_owner_download_delete_restore_visibility_and_select_denied(self):
        model_id = self.upload().json()['model']['id']
        self.promote()
        h = {'authorization':'Bearer fixture-two'}
        self.assertEqual(404, self.client.get(BASE+'/'+model_id+'/download',headers=h).status_code)
        for method, suffix, body in (('DELETE','',{}),('POST','/restore',{}),('POST','/select',{}),
                                     ('PATCH','/visibility',{'visibility':'public'})):
            response = self.mutate(method,model_id,suffix,body,headers=h)
            self.assertEqual(404,response.status_code,response.text)

    def test_actor_and_permanent_cleanup_payloads_rejected(self):
        model_id = self.upload().json()['model']['id']
        for suffix, body in (('/visibility', {'visibility':'public','uid':10001}),
                             ('/restore',{'actorUid':10001}),('/select',{'directory':'../../public'})):
            method = 'PATCH' if suffix == '/visibility' else 'POST'
            self.assertEqual(400,self.mutate(method,model_id,suffix,body).status_code)
        self.assertEqual(400,self.mutate('DELETE',model_id,body={'permanent':True}).status_code)
        self.assertEqual(413,self.mutate('DELETE',model_id,body={'padding':'x'*2048}).status_code)

    def test_pending_public_does_not_download_or_select_and_no_promotion_http(self):
        model_id = self.upload(params={'visibility':'public'}).json()['model']['id']
        h = {'authorization':'Bearer fixture-one'}
        self.assertEqual([], self.client.get('/api/v1/ysm/models/public',headers=h).json()['models'])
        self.assertEqual(409,self.client.get(BASE+'/'+model_id+'/download',headers=h).status_code)
        self.assertEqual(409,self.mutate('POST',model_id,'/select').status_code)
        self.assertEqual(404,self.mutate('POST',model_id,'/verify').status_code)
        self.assertEqual(404,self.mutate('POST',model_id,'/purge').status_code)

    def test_verified_download_is_no_store_and_stage_is_not_selectable(self):
        model_id = self.upload().json()['model']['id']
        self.promote()
        h = {'authorization':'Bearer fixture-one'}
        response = self.client.get(BASE+'/'+model_id+'/download',headers=h)
        self.assertEqual(200,response.status_code)
        self.assertEqual(DATA,response.content)
        self.assertIn('no-store',response.headers['cache-control'])
        self.assertEqual('nosniff',response.headers['x-content-type-options'])
        self.store.mark_synced(hashlib.sha256(DATA).hexdigest(),self.store.bridge_snapshot()['revision'],'copy-only')
        self.assertEqual(409,self.mutate('POST',model_id,'/select').status_code)

    def test_delete_list_restore_and_visibility_json(self):
        model_id = self.upload(params={'visibility':'public'}).json()['model']['id']
        self.assertEqual(200,self.mutate('DELETE',model_id).status_code)
        h = {'authorization':'Bearer fixture-one'}
        self.assertEqual([],self.client.get(BASE,headers=h).json()['models'])
        deleted = self.client.get(BASE,params={'include_deleted':'true'},headers=h).json()['models']
        self.assertEqual(1,len(deleted))
        self.assertTrue(deleted[0]['canRestore'])
        restored = self.mutate('POST',model_id,'/restore')
        self.assertEqual('private',restored.json()['model']['visibility'])
        changed = self.mutate('PATCH',model_id,'/visibility',{'visibility':'public'})
        self.assertEqual(200,changed.status_code)
        self.assertEqual('public',changed.json()['model']['visibility'])

    def test_public_hash_lookup_private_pending_and_unknown_are_same_404(self):
        self.upload()
        digest = hashlib.sha256(DATA).hexdigest()
        h = {'authorization':'Bearer fixture-two'}
        one = self.client.get('/api/v1/ysm/models/public/by-sha256/'+digest,headers=h)
        two = self.client.get('/api/v1/ysm/models/public/by-sha256/'+'f'*64,headers=h)
        self.assertEqual(404,one.status_code)
        self.assertEqual(one.json(),two.json())

    def test_public_reference_without_file_and_revoke_race_denied(self):
        own_id = self.upload(params={'visibility':'public'}).json()['model']['id']
        self.promote()
        digest = hashlib.sha256(DATA).hexdigest()
        h = self.headers(authorization='Bearer fixture-two')
        public_id = self.client.get('/api/v1/ysm/models/public/by-sha256/'+digest,headers=h).json()['model']['id']
        body = {'publicId':public_id,'filename':'saved.ysm','visibility':'private'}
        response = self.client.post(BASE+'/references',headers=h,json=body)
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual('private',response.json()['model']['visibility'])
        self.assertTrue(self.client.post(BASE+'/references',headers=h,json=body).json()['replayed'])
        self.mutate('PATCH',own_id,'/visibility',{'visibility':'private'})
        body['filename']='after-revoke.ysm'
        response = self.client.post(BASE+'/references',headers=self.headers(authorization='Bearer fixture-two'),json=body)
        self.assertEqual(404,response.status_code)
        self.assertEqual(1,len(self.store.list_models(10001)['models']))


if __name__ == '__main__':
    unittest.main()
