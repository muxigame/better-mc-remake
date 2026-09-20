from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from .auth import AuthStore, SlidingWindowLimiter, send_verification_email


SERVER_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = SERVER_ROOT.parent
WEB_ROOT = SERVER_ROOT / "web"
PUBLISH_ROOT = SERVER_ROOT / "publish"
MANIFEST_FILE = PUBLISH_ROOT / "manifest.json"
SITE_FILE = SERVER_ROOT / "site.json"
RELEASE_FILES = (
    WORKSPACE_ROOT / "artifacts" / "client" / "launcher-release.json",
    SERVER_ROOT / "launcher-release.json",
)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def read_json(path: Path, fallback: dict | None = None) -> dict:
    if not path.is_file():
        if fallback is not None:
            return fallback
        raise HTTPException(status_code=503, detail=f"缺少发布文件：{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail=f"发布文件不可读：{path.name}") from error


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


load_dotenv(WORKSPACE_ROOT / ".env")

database_setting = os.getenv("BMC_DATABASE_PATH", "server/data/battermc.db")
database_path = Path(database_setting)
if not database_path.is_absolute():
    database_path = WORKSPACE_ROOT / database_path
auth_store = AuthStore(database_path)
login_limiter = SlidingWindowLimiter(attempts=10, seconds=300)
register_limiter = SlidingWindowLimiter(attempts=5, seconds=3600)

app = FastAPI(
    title="Batter MC Remake",
    version="1.0.0",
    docs_url="/api/docs" if os.getenv("BMC_ENABLE_DOCS") == "1" else None,
    redoc_url=None,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    )
    if request.url.scheme == "https" or os.getenv("BMC_PUBLIC_URL", "").lower().startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith(("/api/v1/auth", "/api/v1/admin")):
        response.headers["Cache-Control"] = "no-store"
    return response


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=16)
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(BaseModel):
    identity: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


def client_key(request: Request, purpose: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{purpose}:{host}"


def bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.cookies.get("bmc_session")


def current_account(request: Request):
    account = auth_store.session(bearer_token(request))
    if account is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return account


def admin_account(account=Depends(current_account)):
    if account.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return account


def site_config() -> dict:
    config = read_json(SITE_FILE)
    config["ossBaseUrl"] = os.getenv("BMC_OSS_BASE_URL", config["ossBaseUrl"]).rstrip("/")
    return config


def launcher_release(config: dict) -> dict:
    release_file = next((path for path in RELEASE_FILES if path.is_file()), RELEASE_FILES[0])
    release = read_json(
        release_file,
        {
            "version": "1.0.0",
            "installer": config["launcherObject"],
            "sha256": "",
            "size": 0,
        },
    )
    object_name = release.get("installer") or config["launcherObject"]
    return {
        "version": release.get("version", "1.0.0"),
        "url": f'{config["ossBaseUrl"]}/{quote(object_name)}',
        "sha256": release.get("sha256", ""),
        "size": int(release.get("size", 0)),
        "sizeText": human_size(int(release.get("size", 0))),
        "notes": "下载客户端后，由客户端完成整合包、Java 与 NeoForge 的安装和更新。",
        "mandatory": False,
    }


def load_manifest() -> dict:
    return read_json(MANIFEST_FILE)


@app.get("/healthz")
def health() -> dict:
    manifest = load_manifest()
    return {"ok": True, "pack": manifest.get("pack", {}).get("version"), "service": "website"}


@app.get("/api/v1/site")
def site() -> dict:
    config = site_config()
    manifest = load_manifest()
    files = manifest.get("files", [])
    pack = manifest.get("pack", {})
    minecraft = manifest.get("minecraft", {})
    return {
        "site": {
            "name": config["name"],
            "edition": config["edition"],
            "serverAddress": config["serverAddress"],
        },
        "launcher": launcher_release(config),
        "pack": {
            "name": pack.get("name", "BatterMC5Remake"),
            "version": pack.get("version", "—"),
            "released": pack.get("released"),
            "minecraft": minecraft.get("version", "1.21.1"),
            "loader": minecraft.get("loader", "NeoForge"),
            "loaderVersion": minecraft.get("loaderVersion", ""),
            "fileCount": len(files),
            "size": sum(int(item.get("size", 0)) for item in files),
            "sizeText": human_size(sum(int(item.get("size", 0)) for item in files)),
        },
    }


@app.get("/api/v1/launcher/latest")
def latest_launcher() -> dict:
    return launcher_release(site_config())


@app.get("/api/v1/manifest")
def manifest() -> JSONResponse:
    config = site_config()
    payload = copy.deepcopy(load_manifest())
    base = config["ossBaseUrl"]
    prefix = config["filesPrefix"].strip("/")
    for item in payload.get("files", []):
        if not item.get("url"):
            encoded = "/".join(quote(part, safe="") for part in item["path"].split("/"))
            item["url"] = f"{base}/{prefix}/{encoded}"
    payload["launcher"] = launcher_release(config)
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=60"})


@app.post("/api/v1/auth/register", status_code=201)
def register(payload: RegisterRequest, request: Request, background: BackgroundTasks) -> dict:
    if not register_limiter.allow(client_key(request, "register")):
        raise HTTPException(status_code=429, detail="注册请求过于频繁，请稍后再试")
    username = payload.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", username):
        raise HTTPException(status_code=422, detail="玩家名只能使用 3–16 位字母、数字和下划线")
    if not os.getenv("BMC_SMTP_HOST") and os.getenv("BMC_AUTH_DEV_VERIFY") != "1":
        raise HTTPException(status_code=503, detail="邮件验证服务尚未启用")
    try:
        account, raw_token = auth_store.register(str(payload.email), username, payload.password)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    public_url = os.getenv("BMC_PUBLIC_URL", str(request.base_url).rstrip("/")).rstrip("/")
    verify_url = f"{public_url}/api/v1/auth/verify?token={quote(raw_token)}"
    background.add_task(send_verification_email, account.email, account.username, verify_url)
    result = {"ok": True, "message": "验证邮件已发送，请在 24 小时内完成验证"}
    if os.getenv("BMC_AUTH_DEV_VERIFY") == "1":
        result["verificationUrl"] = verify_url
    return result


@app.get("/api/v1/auth/verify")
def verify_email(token: str = "") -> RedirectResponse:
    if not token or auth_store.verify_email(token) is None:
        return RedirectResponse("/account.html?verified=0", status_code=303)
    return RedirectResponse("/account.html?verified=1", status_code=303)


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    if not login_limiter.allow(client_key(request, "login")):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    try:
        result = auth_store.login(payload.identity.strip(), payload.password, "web")
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=401, detail="账号或密码不正确")
    account, token = result
    secure = os.getenv("BMC_PUBLIC_URL", "").lower().startswith("https://")
    response.set_cookie(
        "bmc_session",
        token,
        max_age=14 * 24 * 3600,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return {"user": account.public()}


@app.post("/api/v1/auth/launcher-login")
def launcher_login(payload: LoginRequest, request: Request) -> dict:
    if not login_limiter.allow(client_key(request, "launcher-login")):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    try:
        result = auth_store.login(payload.identity.strip(), payload.password, "launcher")
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=401, detail="账号或密码不正确")
    account, token = result
    return {"token": token, "expiresIn": 14 * 24 * 3600, "user": account.public()}


@app.get("/api/v1/auth/me")
def me(account=Depends(current_account)) -> dict:
    return {"user": account.public()}


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response) -> dict:
    auth_store.logout(bearer_token(request))
    response.delete_cookie("bmc_session", path="/")
    return {"ok": True}


@app.get("/api/v1/admin/users")
def admin_users(limit: int = 200, _account=Depends(admin_account)) -> dict:
    return {"users": auth_store.list_accounts(limit)}


@app.get("/download")
def download_launcher() -> JSONResponse:
    return JSONResponse(launcher_release(site_config()))


if os.getenv("BMC_SERVE_WEB", "1") == "1":
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(WEB_ROOT / "favicon.svg", media_type="image/svg+xml")

    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="website")
