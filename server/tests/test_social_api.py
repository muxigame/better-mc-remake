import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

temp = tempfile.TemporaryDirectory()
os.environ['BMC_SKIP_DOTENV'] = '1'
os.environ['BMC_DATABASE_PATH'] = str(Path(temp.name)/'synthetic.db')
os.environ['BMC_PUBLIC_URL'] = 'http://testserver'
os.environ['BMC_GAME_SERVICE_KEY'] = 'synthetic-test-credential-only-00000000'
os.environ['BMC_GAME_PLATFORM_ENABLED'] = '0'
os.environ['BMC_GAME_SOCIAL_ENABLED'] = '1'
os.environ['BMC_GAME_SOCIAL_KEY'] = 'synthetic-social-only-credential-00000000'
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import main
from app.game_identity import offline_uuid
from app.oidc import WebsiteAccount
from app.social_api import social_router


class SocialAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'social.db'
        app = FastAPI()
        app.include_router(social_router(self.path,main.current_player_account))
        self.client = TestClient(app)
        self.accounts = [WebsiteAccount(subject=f'social-test-{uid}',uid=uid,username='synthetic',nickname='same display',game_name=str(uid),email=None,email_verified=False,role='player') for uid in (10000,10001)]
        self.cookies = [main.web_auth_store.create_session(a) for a in self.accounts]
        for cookie in self.cookies:
            self.client.cookies.set('bmc_session',cookie)
            self.assertEqual(200,self.client.get('/api/v1/player/social').status_code)
        self.client.cookies.set('bmc_session',self.cookies[0])

    def tearDown(self): self.tmp.cleanup()

    def write(self, method, path, **kwargs):
        headers = {'origin':'http://testserver','content-type':'application/json','idempotency-key':str(uuid.uuid4())}
        headers.update(kwargs.pop('headers',{}))
        return self.client.request(method,path,headers=headers,content=kwargs.pop('content','{}'),**kwargs)

    def test_cookie_bearer_actor_csrf_and_forged_actor(self):
        path = '/api/v1/player/social/requests/10001'
        self.assertEqual(403,self.write('PUT',path,headers={'origin':'http://evil.example'}).status_code)
        self.assertEqual(403,self.write('PUT',path,headers={'content-type':'text/plain'}).status_code)
        self.assertEqual(400,self.write('PUT',path,content='{"actorUid":"10001"}').status_code)
        self.assertEqual(400,self.write('PUT',path,headers={'idempotency-key':'@a\n'}).status_code)
        self.client.cookies.clear()
        self.assertEqual(401,self.client.get('/api/v1/player/social').status_code)
        with patch.object(main.oidc_client,'userinfo',return_value=self.accounts[0]):
            response=self.write('PUT',path,headers={'authorization':'Bearer synthetic-only','origin':''})
            self.assertEqual(200,response.status_code)
            self.assertEqual('10000',self.client.get('/api/v1/player/social',headers={'authorization':'Bearer synthetic-only'}).json()['selfUid'])
        self.client.cookies.set('bmc_session',self.cookies[0])
        self.assertEqual(403,self.write('PUT',path,headers={'authorization':'Bearer fake','origin':''}).status_code)

    def test_receipt_pair_shared_views_accept_remove_block(self):
        key=str(uuid.uuid4()); path='/api/v1/player/social/requests/10001'
        first=self.write('PUT',path,headers={'idempotency-key':key})
        self.assertEqual(200,first.status_code)
        self.assertEqual(first.json(),self.write('PUT',path,headers={'idempotency-key':key}).json())
        self.assertEqual(400,self.write('PUT','/api/v1/player/social/blocks/10001',headers={'idempotency-key':key}).status_code)
        self.assertEqual(404,self.write('POST','/api/v1/player/social/requests/10001/accept').status_code)
        self.client.cookies.set('bmc_session',self.cookies[1])
        self.assertEqual('10000',self.client.get('/api/v1/player/social').json()['incoming'][0]['uid'])
        self.assertEqual(200,self.write('POST','/api/v1/player/social/requests/10000/accept').status_code)
        self.assertEqual('10000',self.client.get('/api/v1/player/social').json()['friends'][0]['uid'])
        self.assertEqual(200,self.write('PUT','/api/v1/player/social/blocks/10000').status_code)
        self.client.cookies.set('bmc_session',self.cookies[0])
        self.assertEqual([],self.client.get('/api/v1/player/social').json()['friends'])
        self.assertEqual(403,self.write('PUT',path).status_code)

    def test_only_service_credential_can_publish_presence(self):
        path='/api/internal/game/social/presence'
        body={'players':[{'uid':'10000','uuid':offline_uuid(10000),'gameName':'10000'},{'uid':'10001','uuid':offline_uuid(10001),'gameName':'10001'}]}
        self.assertEqual(401,self.client.put(path,json=body).status_code)
        headers={'x-muxi-server-key':os.environ['BMC_GAME_SOCIAL_KEY']}
        self.assertEqual(200,self.client.put(path,json=body,headers=headers).status_code)
        endpoint='/api/internal/game/social/eligibility/10000/10001'
        self.assertTrue(self.client.get(endpoint,headers=headers).json()['allowed'])
        self.assertFalse(self.client.get(endpoint+'?source=friends',headers=headers).json()['allowed'])
        with patch.dict(os.environ,{'BMC_GAME_SOCIAL_ENABLED':'0'}):
            self.assertEqual(503,self.client.put(path,json=body,headers=headers).status_code)
        body['players'][1]['gameName']='@a'
        self.assertEqual(400,self.client.put(path,json=body,headers=headers).status_code)


if __name__ == '__main__': unittest.main()
