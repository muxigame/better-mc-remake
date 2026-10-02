"""Both real ASGI apps, both real SQLite stores, synthetic credentials, no network/server process."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import uuid

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]/"repos"
IMPORT_TMP=tempfile.TemporaryDirectory(prefix="terminal-sso-import-")
os.environ.update({"MUXI_DATABASE_PATH":str(Path(IMPORT_TMP.name)/"auth.db"),
    "MUXI_SIGNING_KEY_PATH":str(Path(IMPORT_TMP.name)/"signing.pem"),
    "MUXI_ISSUER":"https://account.muxigame.com", "MUXI_TERMINAL_SSO_ENABLED":"1",
    "MUXI_MC_PROFILE_KEY":"synthetic-game-key-32-characters-long",
    "MUXI_BMC_WEB_CLIENT_SECRET":"synthetic-platform-key-32-characters-long",
    "BMC_DATABASE_PATH":str(Path(IMPORT_TMP.name)/"site.db"),
    "BMC_PUBLIC_URL":"https://mc.muxigame.com", "BMC_SERVE_WEB":"1","BMC_TERMINAL_SSO_ENABLED":"1"})

def load_package(name,path):
    spec=importlib.util.spec_from_file_location(name,path/"__init__.py",submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)

load_package("sso_auth",ROOT/"muxi-auth/app")
load_package("sso_site",ROOT/"better-mc-remake/server/app")
from sso_auth import main as auth
from sso_auth.security import pkce_s256
from sso_site import main as site
from sso_site.oidc import OidcClient


class AuthTransport:
    def __init__(self,client): self.client=client;self.calls=[]
    def open(self,request,timeout):
        self.calls.append(request)
        assert request.full_url=="https://account.muxigame.com/api/internal/terminal/exchange"
        response=self.client.post("/api/internal/terminal/exchange",content=request.data,headers=dict(request.header_items()))
        if response.status_code!=200:
            raise urllib.error.HTTPError(request.full_url,response.status_code,"Synthetic auth failure",{},None)
        return io.BytesIO(response.content)


class PlatformEndToEndTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls): IMPORT_TMP.cleanup()

    def setUp(self):
        self.client=TestClient(auth.app,base_url="https://account.muxigame.com")
        self.website=TestClient(site.app,base_url="https://mc.muxigame.com")
        suffix=uuid.uuid4().hex[:8]
        account,verify=auth.store.register(suffix+"@example.com","P_"+suffix,"Player","correct-horse-battery")
        self.account=auth.store.verify_email(verify)
        self.access=auth.store.issue_access_token(account.id,auth.settings.bmc_launcher_client_id,"openid profile",3600)
        site.oidc_client=OidcClient(auth.settings.issuer,auth.settings.bmc_web_client_id,
            auth.settings.bmc_web_client_secret,"https://mc.muxigame.com/api/v1/auth/callback")
        self.transport=AuthTransport(self.client)
        self.opener=patch("urllib.request.build_opener",return_value=self.transport);self.opener.start()
        self.request_id=str(uuid.uuid4());self.verifier="v"*43;self.game_session=str(uuid.uuid4())

    def tearDown(self):
        self.opener.stop();self.client.close();self.website.close()

    def ticket(self):
        bootstrap=self.client.post("/api/launcher/minecraft/terminal-bootstrap",headers={"Authorization":"Bearer "+self.access}).json()["credential"]
        proof=self.client.post("/api/launcher/minecraft/terminal-proof",json={"challenge":pkce_s256(self.verifier),"requestId":self.request_id},
            headers={"Authorization":"MuxiTerminal "+bootstrap}).json()["proof"]
        return self.client.post("/api/internal/minecraft/terminal-ticket",json={"proof":proof,"uid":self.account.uid,"requestId":self.request_id,"gameSession":self.game_session},
            headers={"X-Muxi-Server-Key":auth.settings.minecraft_profile_key}).json()["ticket"]

    def exchange(self,ticket,**headers):
        return self.website.post("/api/v1/auth/terminal/exchange",json={"ticket":ticket,"verifier":self.verifier,"requestId":self.request_id},
            headers={"Origin":"https://mc.muxigame.com","X-Muxi-Terminal-Action":"1",**headers},follow_redirects=False)

    def test_bootstrap_to_real_platform_cookie_and_player_center(self):
        ticket=self.ticket()
        response=self.exchange(ticket)
        self.assertEqual(303,response.status_code)
        self.assertEqual("/account.html",response.headers['location'])
        cookie=response.headers["set-cookie"].lower()
        for flag in ("bmc_session=","httponly","secure","samesite=lax","path=/"): self.assertIn(flag,cookie)
        self.assertNotIn("domain=",cookie);self.assertNotIn("muxi_session",cookie)
        self.assertEqual(self.account.uid,self.website.get("/api/v1/auth/me").json()["user"]["uid"])
        self.assertEqual(self.account.role,self.website.get("/api/v1/auth/me").json()["user"]["role"])
        page=self.website.get("/account.html",follow_redirects=False)
        self.assertEqual(200,page.status_code);self.assertIn("game-identity",page.text)
        self.assertEqual(403,self.website.get("/admin.html",follow_redirects=False).status_code)
        self.assertEqual(401,self.exchange(ticket).status_code)
        self.assertEqual(401,self.client.get("/api/account/me").status_code)
        request=self.transport.calls[0]
        self.assertNotIn(ticket,request.full_url);self.assertNotIn(self.verifier,request.full_url)
        self.assertEqual("https://mc.muxigame.com/account.html",json.loads(request.data)["target"])

    def test_csrf_cannot_consume_a_valid_ticket(self):
        ticket=self.ticket()
        for headers in ({"Origin":"https://attacker.example"},{"Origin":""},{"X-Muxi-Terminal-Action":""}):
            self.assertEqual(403,self.exchange(ticket,**headers).status_code)
        self.assertEqual(0,len(self.transport.calls))
        self.assertEqual(303,self.exchange(ticket).status_code)

    def test_browser_cannot_supply_uid_role_or_return_target(self):
        ticket=self.ticket()
        for extra in ({"uid":self.account.uid+1},{"role":"admin"},{"target":"https://evil.example"}):
            response=self.website.post("/api/v1/auth/terminal/exchange",json={"ticket":ticket,"verifier":self.verifier,"requestId":self.request_id,**extra},
                headers={"Origin":"https://mc.muxigame.com","X-Muxi-Terminal-Action":"1"})
            self.assertEqual(401,response.status_code)
        self.assertEqual(0,len(self.transport.calls));self.assertEqual(303,self.exchange(ticket).status_code)

    def test_upstream_failure_keeps_normal_platform_login_available(self):
        ticket=self.ticket()
        with patch.object(site.oidc_client,"exchange_terminal_ticket",side_effect=RuntimeError("synthetic outage")):
            response=self.exchange(ticket)
        self.assertEqual(401,response.status_code);self.assertNotIn("set-cookie",response.headers)
        self.assertEqual(303,self.website.get("/account.html",follow_redirects=False).status_code)

    def test_malformed_upstream_claims_fail_back_to_normal_login(self):
        ticket=self.ticket()
        for body in ([], {"audience":"better-mc-web", "target":"https://mc.muxigame.com/account.html", "user":[]}):
            with patch.object(self.transport,"open",return_value=io.BytesIO(json.dumps(body).encode())):
                response=self.exchange(ticket)
            self.assertEqual(401,response.status_code)
            self.assertNotIn("set-cookie",response.headers)
        self.assertEqual(303,self.exchange(ticket).status_code)

    def test_terminal_login_page_production_proxy_route(self):
        page=self.website.get("/api/v1/auth/terminal")
        self.assertEqual(200,page.status_code);self.assertIn('/terminal-login.js',page.text)
        with patch.dict(os.environ,{"BMC_SERVE_WEB":"0"}):
            page=self.website.get("/api/v1/auth/terminal")
        self.assertEqual('/__protected/terminal-login.html',page.headers['x-accel-redirect'])

    def test_platform_logout_removes_its_own_session(self):
        self.assertEqual(303,self.exchange(self.ticket()).status_code)
        response=self.website.post('/api/v1/auth/logout')
        self.assertEqual(200,response.status_code)
        self.assertEqual(401,self.website.get('/api/v1/auth/me').status_code)

    def test_game_ban_and_op_level_cannot_change_platform_login_or_role(self):
        # Only the isolated synthetic database is changed, never real permissions.
        site.platform_store.initialize_permissions(self.account.uid,False,4)
        site.platform_store.set_ban(self.account.uid,self.account.uid,True,'Synthetic test',account_admin=True)
        with patch.object(auth,'check_game_admission',side_effect=RuntimeError('Game-only admission must not be called')) as admission:
            self.assertEqual(303,self.exchange(self.ticket()).status_code)
            admission.assert_not_called()
        user=self.website.get('/api/v1/player/profile').json()
        self.assertEqual('player',user['user']['role'])
        self.assertEqual(4,user['permissions']['gameOpLevel'])
        self.assertFalse(user['permissions']['platformAdmin'])
        self.assertEqual(200,self.website.get('/account.html',follow_redirects=False).status_code)
        self.assertEqual(403,self.website.get('/admin.html',follow_redirects=False).status_code)

    def test_disabled_feature_redirects_to_existing_platform_entry(self):
        with patch.dict(os.environ,{"BMC_TERMINAL_SSO_ENABLED":"0"}):
            response=self.website.get('/api/v1/auth/terminal',follow_redirects=False)
            self.assertEqual(303,response.status_code);self.assertEqual('/account.html',response.headers['location'])
            self.assertEqual(404,self.exchange('x'*43).status_code)
