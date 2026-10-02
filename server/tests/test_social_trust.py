"""Synthetic credentials only; prove dedicated social trust cannot authorize wider routes."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['BMC_SKIP_DOTENV'] = '1'
_database = tempfile.TemporaryDirectory()
os.environ['BMC_DATABASE_PATH'] = str(Path(_database.name)/'trust.db')
from fastapi.testclient import TestClient
from app import main
from app.game_identity import offline_uuid

SOCIAL = 'synthetic-social-only-credential-00000000'
PLATFORM = 'synthetic-platform-only-credential-00000000'


class SocialTrustTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'BMC_GAME_SOCIAL_ENABLED':'1','BMC_GAME_SOCIAL_KEY':SOCIAL,
            'BMC_GAME_PLATFORM_ENABLED':'0','BMC_GAME_OP_SYNC_ENABLED':'0',
            'BMC_GAME_SERVICE_KEY':PLATFORM,
        })
        self.env.start()
        self.client=TestClient(main.app)
        self.body={'players':[{'uid':'10000','uuid':offline_uuid(10000),'gameName':'10000'},
                              {'uid':'10001','uuid':offline_uuid(10001),'gameName':'10001'}]}
        self.headers={'x-muxi-server-key':SOCIAL}

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def test_social_works_without_platform_or_op_enablement(self):
        self.assertEqual(200,self.client.put('/api/internal/game/social/presence',json=self.body,headers=self.headers).status_code)
        reply=self.client.get('/api/internal/game/social/eligibility/10000/10001',headers=self.headers)
        self.assertEqual(200,reply.status_code)
        self.assertTrue(reply.json()['allowed'])
        self.assertEqual('0',os.environ['BMC_GAME_PLATFORM_ENABLED'])
        self.assertEqual('0',os.environ['BMC_GAME_OP_SYNC_ENABLED'])

    def test_missing_legacy_or_forged_credential_is_rejected(self):
        for credential in ('',PLATFORM,'synthetic-untrusted-client-credential-00000000'):
            with self.subTest(credential_kind='missing' if not credential else 'other'):
                headers={'x-muxi-server-key':credential}
                self.assertEqual(401,self.client.put('/api/internal/game/social/presence',json=self.body,headers=headers).status_code)
                self.assertEqual(401,self.client.get('/api/internal/game/social/eligibility/10000/10001',headers=headers).status_code)

    def test_social_key_cannot_access_platform_and_op_even_when_enabled(self):
        with patch.dict(os.environ,{'BMC_GAME_PLATFORM_ENABLED':'1','BMC_GAME_OP_SYNC_ENABLED':'1'}):
            for method,path,body in [
                ('POST','/api/internal/game/task-claims',{}),
                ('POST','/api/internal/game/results',{}),
                ('GET','/api/internal/game/admission/10000',None),
                ('GET','/api/internal/game/ops-sync/',None),
                ('POST','/api/internal/game/ops-sync/ack',{}),
                ('POST','/api/internal/game/ops-sync/observations',{}),
            ]:
                with self.subTest(path=path):
                    response=self.client.request(method,path,headers=self.headers,json=body)
                    self.assertEqual(401,response.status_code)

    def test_accidental_key_reuse_disabled_and_legacy_never_fallback(self):
        for env in ({'BMC_GAME_SOCIAL_KEY':PLATFORM},{'BMC_GAME_SOCIAL_KEY':''},{'BMC_GAME_SOCIAL_ENABLED':'0'}):
            with patch.dict(os.environ,env):
                self.assertEqual(503,self.client.put('/api/internal/game/social/presence',json=self.body,headers=self.headers).status_code)


if __name__ == '__main__': unittest.main()
