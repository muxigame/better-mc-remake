import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

temp=tempfile.TemporaryDirectory()
os.environ['BMC_SKIP_DOTENV']='1'
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

if __name__=='__main__': unittest.main(verbosity=2)
