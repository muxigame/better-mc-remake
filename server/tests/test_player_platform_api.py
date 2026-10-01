import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

temp=tempfile.TemporaryDirectory()
os.environ['BMC_SKIP_DOTENV']='1'
os.environ['BMC_SERVE_WEB']='0'
os.environ['BMC_DATABASE_PATH']=str(Path(temp.name)/'synthetic.db')
os.environ['BMC_PUBLIC_URL']='http://testserver'
os.environ['BMC_GAME_SERVICE_KEY']='synthetic-test-credential-only-00000000'
os.environ['BMC_GAME_PLATFORM_ENABLED']='1'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import main
from app.oidc import WebsiteAccount

class Tests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(main.app)
        self.account=WebsiteAccount(subject='synthetic-10000',uid=10000,username='synthetic',nickname='测试',game_name='10000',email=None,email_verified=False,role='player')
        main.web_auth_store.player_profile(self.account)
        self.cookie=main.web_auth_store.create_session(self.account)
        self.client.cookies.set('bmc_session',self.cookie)
        main.platform_store.initialize_permissions(10000,False,4)
    def test_authentication_and_disabled(self):
        payload={'uid':10000,'day':'2026-09-29','taskId':'api','hard':False}
        self.assertEqual(401,self.client.post('/api/internal/game/task-claims',json=payload).status_code)
        with patch.dict(os.environ,{'BMC_GAME_PLATFORM_ENABLED':'0'}):
            self.assertEqual(503,self.client.post('/api/internal/game/task-claims',json=payload).status_code)
        headers={'x-muxi-server-key':os.environ['BMC_GAME_SERVICE_KEY']}
        self.assertEqual(200,self.client.post('/api/internal/game/task-claims',json=payload,headers=headers).status_code)
        self.assertFalse(self.client.post('/api/internal/game/task-claims',json=payload,headers=headers).json()['credited'])
        payload['points']=100000
        self.assertEqual(400,self.client.post('/api/internal/game/task-claims',json=payload,headers=headers).status_code)
    def test_role_csrf_and_login_unaffected(self):
        endpoint='/api/v1/platform/game-bans/10001'
        data={'banned':True,'reason':'synthetic fixture'}
        self.assertEqual(403,self.client.post(endpoint,json=data,headers={'origin':'http://testserver'}).status_code)
        profile=self.client.get('/api/v1/player/profile').json()
        self.assertFalse(profile['permissions']['platformAdmin']);self.assertEqual(4,profile['permissions']['gameOpLevel'])
        main.platform_store.initialize_permissions(10000,True,0)
        self.assertEqual(403,self.client.post(endpoint,json=data,headers={'origin':'http://evil.example'}).status_code)
        self.assertEqual(200,self.client.post(endpoint,json=data,headers={'origin':'http://testserver'}).status_code)
        main.platform_store.set_ban(10000,10000,True,'synthetic self fixture')
        self.assertEqual(200,self.client.get('/api/v1/auth/me').status_code)
        self.assertEqual(200,self.client.get('/api/v1/player/profile').status_code)
        headers={'x-muxi-server-key':os.environ['BMC_GAME_SERVICE_KEY']}
        self.assertFalse(self.client.get('/api/internal/game/admission/10000',headers=headers).json()['allowed'])
        data['banned']=False
        self.assertEqual(200,self.client.post(endpoint,json=data,headers={'origin':'http://testserver'}).status_code)

    def test_readonly_platform_balance_remains_visible_when_game_banned(self):
        endpoint='/api/internal/game/admission/10000'
        headers={'x-muxi-server-key':os.environ['BMC_GAME_SERVICE_KEY']}
        self.assertEqual(401,self.client.get(endpoint).status_code)
        before=main.platform_store.point_balance(10000)
        main.platform_store.initialize_permissions(10000,True,0)
        main.platform_store.set_ban(10000,10000,True,'synthetic balance fixture')
        response=self.client.get(endpoint,headers=headers)
        self.assertEqual(200,response.status_code);self.assertFalse(response.json()['allowed'])
        self.assertEqual(before,response.json()['points'])
        self.assertIsNone(self.client.get('/api/internal/game/admission/10002',headers=headers).json()['points'])
        self.assertEqual(before,main.platform_store.point_balance(10000))
        self.assertEqual(200,self.client.get('/api/v1/auth/me').status_code)

    def test_minigame_result_service_auth_and_replays(self):
        from uuid import uuid4
        import json
        data={'uid':10000,'game':'outbreak','session':str(uuid4()),'win':True,'score':0,'difficulty':1,'seconds':120}
        endpoint='/api/internal/game/results'
        headers={'x-muxi-server-key':os.environ['BMC_GAME_SERVICE_KEY']}
        self.assertEqual(401,self.client.post(endpoint,json=data).status_code)
        with patch.dict(os.environ,{'BMC_GAME_PLATFORM_ENABLED':'0'}):
            self.assertEqual(503,self.client.post(endpoint,json=data,headers=headers).status_code)
        with patch.dict(os.environ,{'BMC_MINIGAME_REWARDS_JSON':json.dumps({'outbreak':{'1':2}})}):
            first=self.client.post(endpoint,json=data,headers=headers)
            self.assertEqual(200,first.status_code);self.assertTrue(first.json()['credited']);self.assertEqual(2,first.json()['points'])
            self.assertFalse(self.client.post(endpoint,json=data,headers=headers).json()['credited'])
            self.assertEqual(400,self.client.post(endpoint,json={**data,'score':1},headers=headers).status_code)
            self.assertEqual(400,self.client.post(endpoint,json={**data,'points':999},headers=headers).status_code)
            self.assertEqual(409,self.client.post(endpoint,json={**data,'uid':10002},headers=headers).status_code)
        self.assertEqual(413,self.client.post(endpoint,content='x'*4097,headers=headers).status_code)

if __name__=='__main__': unittest.main(verbosity=2)
