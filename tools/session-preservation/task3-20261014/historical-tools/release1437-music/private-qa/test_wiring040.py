import sys,types,importlib.util,json,asyncio,unittest,tempfile
from pathlib import Path
N=Path(__file__).resolve().parent
sys.path.insert(0,r'C:\Users\ranzh\Documents\Codex\2026-10-02\task-2\music-oss-candidate\build\native-qa-deps')
import httpx
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from wiring import Dispatch,loopback_app,FIXED_JOB,allowed
pkg=types.ModuleType('isolated_music');pkg.__path__=[str(N.parent/'frozen-release040/backend-delta/app')];sys.modules[pkg.__name__]=pkg
spec=importlib.util.spec_from_file_location('isolated_music.terminal_server_auth',r'C:\Users\ranzh\workspace\dev\muxigame\muxi-auth\app\terminal_server_auth.py');module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
from isolated_music.music_upload import music_upload_router,MusicError
from isolated_music.music_upload_identity import music_identities,music_live_identity
S='11111111-1111-4111-8111-111111111111';TOKEN='offline-fixture-only'
class Fixture:
    preflight_passed=False
    def __init__(self):
        self.calls=0;self.playbacks=0;self.live=True
        account=types.SimpleNamespace(id=7,uid=10000)
        self.store=types.SimpleNamespace(access_token=lambda token:(account,'launcher') if token==TOKEN else None,account_by_uid=lambda uid:account if uid==10000 else None)
        settings=types.SimpleNamespace(terminal_sso_enabled=True,terminal_sso_server_key='fixture-server-key-not-real-0000000',minecraft_profile_key='different-fixture-only',bmc_launcher_client_id='launcher')
        bindings=types.SimpleNamespace(active=lambda aid,session,key:self.live and aid==7 and session==S)
        launcher,server=music_identities(lambda:settings,lambda:self.store,lambda:bindings)
        service=types.SimpleNamespace(storage=types.SimpleNamespace(signed_read=True),normalizer=types.SimpleNamespace(formats=['wav']),playback=self.playback)
        self.router=music_upload_router(lambda:service,launcher,server,music_live_identity(lambda:settings,lambda:self.store,lambda:bindings))
        self.budget=types.SimpleNamespace(snapshot=lambda:{'requests':45+self.calls})
    def preflight(self):self.calls+=2;self.preflight_passed=True;return {'absent':True}
    def stop(self):self.preflight_passed=False
    def playback(self,*a,**k):self.playbacks+=1;return {'fixture':True}
class Tests(unittest.TestCase):
    def setUp(self):self.qa=Fixture();self.d=Dispatch(self.qa)
    def call(self,action,body):return asyncio.run(self.d.call(action,body))
    def frame(self,token=TOKEN,session=S):return dict(method='POST',path='/api/launcher/music/playback',headers={'authorization':'Bearer '+token,'content-type':'application/json'},body=json.dumps({'gameSession':session,'reference':'fixture'}))
    def test_no_cloud_on_construct(self):self.assertEqual(self.qa.calls,0)
    def test_preflight_requires_owner(self):
        with self.assertRaises(ValueError):self.call('preflight',{})
        self.assertEqual(self.qa.calls,0)
    def test_once_preflight(self):
        self.d.preflight_authorized=True;self.call('preflight',{})
        with self.assertRaises(ValueError):self.call('preflight',{})
        self.assertEqual(self.qa.calls,2)
    def test_unarmed_denies(self):self.assertEqual(self.call('http',self.frame())['status'],503)
    def test_real_router_rejects_bad_bearer(self):
        self.qa.preflight_passed=True;self.assertEqual(self.call('http',self.frame('wrong'))['status'],401);self.assertEqual(self.qa.playbacks,0)
    def test_real_router_rejects_inactive_binding(self):
        self.qa.preflight_passed=True;self.qa.live=False;self.assertEqual(self.call('http',self.frame())['status'],401)
    def test_original_callbacks_accept_verified_fixture(self):
        self.qa.preflight_passed=True;self.assertEqual(self.call('http',self.frame())['status'],200);self.assertEqual(self.qa.playbacks,1)
    def test_cannot_forward_user_uid_or_cookie(self):
        f=self.frame();f['headers']['x-muxi-player-uid']='10000'
        with self.assertRaises(ValueError):self.call('http',f)
        f=self.frame();f['headers']['cookie']='fixture'
        with self.assertRaises(ValueError):self.call('http',f)
    def test_paths_closed(self):
        for path in ['/oauth/token','/oauth/jwks.json','/api/launcher/music/uploads/11111111-1111-4111-8111-111111111111/complete','/api/launcher/music/playback?url=x']:
            self.assertFalse(allowed('POST',path))
    def test_stop_closes(self):
        self.call('stop',{})
        with self.assertRaises(ValueError):self.call('http',self.frame())
    def test_loopback_capability_and_headers(self):
        cap='x'*40;seen=[]
        def rpc(action,body):seen.append(body);return {'status':200,'body':{'ok':True}}
        app=loopback_app(rpc,cap)
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1111)),base_url='https://127.0.0.1') as client:
                self.assertEqual((await client.get('/api/music/capabilities')).status_code,403)
                r=await client.post('/api/launcher/music/playback',headers={'X-Muxi-Music-QA':cap,'authorization':'Bearer fixture','cookie':'should-not-forward'},json={'gameSession':S})
                self.assertEqual(r.status_code,200);self.assertNotIn('cookie',seen[0]['headers']);self.assertNotIn('x-muxi-music-qa',seen[0]['headers'])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('<PRIVATE_HOST>',1111)),base_url='https://127.0.0.1') as client:
                self.assertEqual((await client.get('/api/music/capabilities',headers={'X-Muxi-Music-QA':cap})).status_code,403)
        asyncio.run(run())
if __name__=='__main__':unittest.main()
