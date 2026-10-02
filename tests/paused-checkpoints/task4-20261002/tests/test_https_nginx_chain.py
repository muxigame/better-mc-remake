"""Disposable HTTPS/Nginx + real auth/site ASGI chain, synthetic accounts only.

Production Nginx routing is retained. Bind ports, filesystem paths and upstreams
are localized; the unused OSS media upstream is replaced to avoid external DNS.
This verifies local TLS/proxy behavior, not production or the Minecraft runtime.
"""
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import httpx
import uvicorn
from test_terminal_end_to_end import auth, site, pkce_s256, OidcClient

ROOT=Path(__file__).resolve().parents[1]
NGINX=Path(os.environ.get("SSO_TEST_NGINX", str(ROOT/".tools/nginx/nginx.exe")))


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


class HttpsNginxChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="https-nginx-",dir=ROOT/"evidence")
        cls.path=Path(cls.temp.name)
        (cls.path/"logs").mkdir()
        (cls.path/"temp").mkdir()
        cls.ports=[free_port() for _ in range(4)]
        ap,sp,at,st=cls.ports
        cls.auth_url=f"https://127.0.0.1:{at}"
        cls.site_url=f"https://127.0.0.1:{st}"
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"Synthetic loopback SSO")])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),critical=False)
            .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
            .sign(key,hashes.SHA256()))
        (cls.path/"cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (cls.path/"key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cls.context=ssl.create_default_context(cafile=str(cls.path/"cert.pem"))
        cls.ssl_patch=patch("ssl._create_default_https_context",return_value=cls.context)
        cls.ssl_patch.start()
        cls.env_patch=patch.dict(os.environ,{"BMC_PUBLIC_URL":cls.site_url,"BMC_SERVE_WEB":"0","BMC_TERMINAL_SSO_ENABLED":"1"})
        cls.env_patch.start()
        cls.old_oidc=site.oidc_client
        site.oidc_client=OidcClient(cls.auth_url,auth.settings.bmc_web_client_id,auth.settings.bmc_web_client_secret,cls.site_url+"/api/v1/auth/callback")
        cls.servers=[]
        cls.threads=[]
        for app,port in ((auth.app,ap),(site.app,sp)):
            server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="critical",lifespan="off"))
            thread=threading.Thread(target=server.run,daemon=True)
            cls.servers.append(server);cls.threads.append(thread);thread.start()
        deadline=time.monotonic()+10
        while not all(s.started for s in cls.servers):
            if time.monotonic()>deadline: raise RuntimeError("Disposable ASGI startup timed out")
            time.sleep(.05)
        web=(ROOT/"repos/better-mc-remake/server/web").as_posix()
        config=(ROOT/"repos/better-mc-remake/server/web/nginx-container.conf").read_text(encoding="utf-8")
        config=config.replace("server server:8099;",f"server 127.0.0.1:{sp};")
        config=config.replace("listen 8080;",f"listen 127.0.0.1:{st} ssl;\n    ssl_certificate cert.pem;\n    ssl_certificate_key key.pem;")
        config=config.replace("/usr/share/nginx/html",web)
        config=config.replace("https://muxigame-prod-static-cn.oss-cn-hangzhou.aliyuncs.com/bmc/site/assets/",f"http://127.0.0.1:{sp}/unused-media/")
        config=("worker_processes 1;\nevents { worker_connections 128; }\nhttp {\n"
            "default_type text/html;\n"+config+
            f"\nserver {{ listen 127.0.0.1:{at} ssl; ssl_certificate cert.pem; ssl_certificate_key key.pem; "
            f"location / {{ proxy_pass http://127.0.0.1:{ap}; }} }}\n}}\n")
        (cls.path/"nginx.conf").write_text(config,encoding="utf-8")
        cls.command=[str(NGINX),"-p",cls.path.as_posix()+"/","-c","nginx.conf"]
        syntax=subprocess.run([*cls.command,"-t"],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        (ROOT/"evidence/nginx-syntax.log").write_text(syntax.stdout+syntax.stderr,encoding="utf-8")
        if syntax.returncode: raise RuntimeError(syntax.stderr)
        cls.process=subprocess.Popen(cls.command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        cls.admin=httpx.Client(verify=cls.context,trust_env=False,timeout=5)
        deadline=time.monotonic()+10
        while True:
            try:
                if cls.admin.get(cls.site_url+"/healthz").status_code==200: break
            except httpx.HTTPError: pass
            if time.monotonic()>deadline: raise RuntimeError("Disposable Nginx startup timed out")
            time.sleep(.05)

    @classmethod
    def tearDownClass(cls):
        cls.admin.close()
        subprocess.run([*cls.command,"-s","quit"],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        cls.process.wait(timeout=5)
        for server in cls.servers: server.should_exit=True
        for thread in cls.threads: thread.join(timeout=3)
        site.oidc_client=cls.old_oidc
        cls.env_patch.stop();cls.ssl_patch.stop();cls.temp.cleanup()

    def setUp(self):
        self.client=httpx.Client(base_url=self.site_url,verify=self.context,trust_env=False,timeout=5,follow_redirects=False)
        suffix=uuid.uuid4().hex[:8]
        account,verify=auth.store.register(suffix+"@example.com","H_"+suffix,"TLS Player","correct-horse-battery")
        self.account=auth.store.verify_email(verify)
        self.access=auth.store.issue_access_token(account.id,auth.settings.bmc_launcher_client_id,"openid profile",3600)
        self.verifier="v"*43;self.request_id=str(uuid.uuid4());self.game_session=str(uuid.uuid4())

    def tearDown(self): self.client.close()

    def ticket(self):
        boot=self.admin.post(self.auth_url+"/api/launcher/minecraft/terminal-bootstrap",headers={"Authorization":"Bearer "+self.access})
        self.assertEqual(200,boot.status_code)
        proof=self.admin.post(self.auth_url+"/api/launcher/minecraft/terminal-proof",headers={"Authorization":"MuxiTerminal "+boot.json()["credential"]},json={"challenge":pkce_s256(self.verifier),"requestId":self.request_id})
        self.assertEqual(200,proof.status_code)
        result=self.admin.post(self.auth_url+"/api/internal/minecraft/terminal-ticket",headers={"X-Muxi-Server-Key":auth.settings.minecraft_profile_key},json={"proof":proof.json()["proof"],"uid":self.account.uid,"requestId":self.request_id,"gameSession":self.game_session})
        self.assertEqual(200,result.status_code)
        return result.json()["ticket"]

    def exchange(self,ticket,origin=None):
        return self.client.post("/api/v1/auth/terminal/exchange",json={"ticket":ticket,"verifier":self.verifier,"requestId":self.request_id},headers={"Origin":origin or self.site_url,"X-Muxi-Terminal-Action":"1"})

    def test_https_proxy_cookie_rotation_player_center_replay_and_logout(self):
        self.assertEqual(303,self.client.get("/account.html").status_code)
        ticket=self.ticket();response=self.exchange(ticket)
        self.assertEqual(303,response.status_code);self.assertEqual("/account.html",response.headers["location"])
        for flag in ("bmc_session=","secure","httponly","samesite=lax","path=/"):
            self.assertIn(flag,response.headers["set-cookie"].lower())
        self.assertNotIn("domain=",response.headers["set-cookie"].lower())
        page=self.client.get("/account.html")
        self.assertEqual(200,page.status_code);self.assertIn("game-identity",page.text)
        self.assertNotIn("x-accel-redirect",page.headers)
        self.assertEqual(self.account.uid,self.client.get("/api/v1/auth/me").json()["user"]["uid"])
        self.assertEqual(403,self.client.get("/admin.html").status_code)
        first=self.client.cookies.get("bmc_session")
        self.assertEqual(303,self.exchange(self.ticket()).status_code)
        self.assertNotEqual(first,self.client.cookies.get("bmc_session"))
        self.assertEqual(401,self.exchange(ticket).status_code)
        self.assertEqual(200,self.client.post("/api/v1/auth/logout").status_code)
        self.assertEqual(401,self.client.get("/api/v1/auth/me").status_code)

    def test_internal_handoff_page_and_cross_origin_rejection(self):
        self.assertEqual(404,self.client.get("/terminal-login.html").status_code)
        self.assertEqual(404,self.client.get("/__protected/terminal-login.html").status_code)
        page=self.client.get("/api/v1/auth/terminal")
        self.assertEqual(200,page.status_code);self.assertIn("terminal-login.js",page.text)
        self.assertIn("no-store",page.headers["cache-control"])
        ticket=self.ticket()
        self.assertEqual(403,self.exchange(ticket,origin="https://attacker.example").status_code)
        self.assertEqual(303,self.exchange(ticket).status_code)


if __name__=="__main__": unittest.main(verbosity=2)
