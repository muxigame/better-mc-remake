from __future__ import annotations

import base64
import hmac
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request as UrlRequest, urlopen

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .player_platform import PlatformStore
from .oidc import OidcClient, WebsiteAuthStore, safe_return_to
from .skins import MAX_BYTES as SKIN_MAX_BYTES, SkinError, SkinStore
from .crash_reports import MAX_BUNDLE_BYTES, CrashReportError, CrashReportStore
from .client_updates import update_required, validate_policy, version_key
from .announcement import public_announcement
from .tunnel_registry import router as tunnel_router


SERVER_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = SERVER_ROOT.parent
WEB_ROOT = SERVER_ROOT / "web"
SITE_FILE = SERVER_ROOT / "site.json"
RELEASE_FILES = (
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


if os.getenv("BMC_SKIP_DOTENV") != "1":
    load_dotenv(WORKSPACE_ROOT / ".env")

database_setting = os.getenv("BMC_DATABASE_PATH", "server/data/battermc.db")
database_path = Path(database_setting)
if not database_path.is_absolute():
    database_path = WORKSPACE_ROOT / database_path
web_auth_store = WebsiteAuthStore(database_path)
platform_store = PlatformStore(database_path)
skin_store = SkinStore(database_path, database_path.parent / "skins")
crash_store = CrashReportStore(database_path, database_path.parent / "crash-reports")
oidc_issuer = os.getenv("BMC_AUTH_ISSUER", "https://account.muxigame.com").rstrip("/")
oidc_client = OidcClient(
    oidc_issuer,
    os.getenv("BMC_AUTH_CLIENT_ID", "better-mc-web"),
    os.getenv("BMC_AUTH_CLIENT_SECRET", ""),
    os.getenv("BMC_AUTH_REDIRECT_URI", "https://mc.muxigame.com/api/v1/auth/callback"),
)

app = FastAPI(
    title="Batter MC Remake",
    version="1.0.0",
    docs_url="/api/docs" if os.getenv("BMC_ENABLE_DOCS") == "1" else None,
    redoc_url=None,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(tunnel_router)


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
    if (request.url.path.startswith(("/api/v1/auth", "/api/v1/admin", "/api/v1/player"))
            or request.url.path in {"/account", "/account.html", "/admin.html"}):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


def current_account(request: Request):
    account = web_auth_store.session(request.cookies.get("bmc_session"))
    if account is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return account


def current_player_account(request: Request):
    account = web_auth_store.session(request.cookies.get("bmc_session"))
    if account is not None:
        return account

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            try:
                return oidc_client.userinfo(token)
            except (RuntimeError, ValueError) as error:
                raise HTTPException(status_code=401, detail="muxi 账户 会话无效") from error
    raise HTTPException(status_code=401, detail="请先登录")


def admin_account(account=Depends(current_account)):
    if account.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return account


def site_config() -> dict:
    config = read_json(SITE_FILE)
    config["ossBaseUrl"] = os.getenv("BMC_OSS_BASE_URL", config["ossBaseUrl"]).rstrip("/")
    config["clientLatestUrl"] = os.getenv(
        "BMC_CLIENT_LATEST_URL",
        config.get("clientLatestUrl", ""),
    )
    manifest_object = str(config.get("manifestObject", "manifest.json")).lstrip("/")
    files_prefix = str(config.get("filesPrefix", "files")).strip("/")
    config["manifestUrl"] = os.getenv(
        "BMC_MANIFEST_URL",
        f'{config["ossBaseUrl"]}/{quote(manifest_object)}',
    )
    config["filesBaseUrl"] = os.getenv(
        "BMC_FILES_BASE_URL",
        f'{config["ossBaseUrl"]}/{files_prefix}',
    ).rstrip("/")
    # Minecraft 本体（资源对象、运行库、客户端 jar）的镜像根。置空即让客户端直连
    # Mojang / NeoForge——上游在国内很慢而且部分玩家连不上，所以默认指向我们的 OSS。
    config["mirrorBaseUrl"] = os.getenv(
        "BMC_MIRROR_BASE_URL",
        config.get("mirrorBaseUrl", ""),
    ).rstrip("/")
    return config


def launcher_release(config: dict) -> dict:
    release = None
    latest_url = str(config.get("clientLatestUrl") or "").strip()
    if latest_url:
        request = UrlRequest(
            latest_url,
            headers={"User-Agent": "BatterMC-Website/1.0", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=6) as response:
                release = json.loads(response.read().decode("utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError):
            # 网络/OSS 故障时退回镜像内最后一次成功发布快照；
            # 正常情况下 OSS latest metadata 是唯一当前版本真相源。
            release = None

    if release is None:
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
    release_url = release.get("url") or f'{config["ossBaseUrl"]}/{quote(object_name)}'
    try:
        policy = validate_policy(release, str(release.get("version", "1.0.0")))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=503, detail="客户端发布策略无效") from error
    return {
        "version": release.get("version", "1.0.0"),
        "url": release_url,
        "ossObject": release.get("ossObject"),
        "releaseObject": release.get("releaseObject"),
        "sha256": release.get("sha256", ""),
        "size": int(release.get("size", 0)),
        "sizeText": human_size(int(release.get("size", 0))),
        "signature": release.get("signature", ""),
        "pubDate": release.get("pubDate"),
        "notes": release.get("notes", ""),
        "mandatory": release.get("mandatory") is True,
        **policy,
    }


def semver_tuple(value: str) -> tuple:
    return version_key(value)


def load_manifest() -> dict:
    config = site_config()
    request = UrlRequest(
        config["manifestUrl"],
        headers={"User-Agent": "BatterMC-Website/1.0", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail="无法读取当前整合包 manifest") from error


@app.get("/healthz")
def health() -> dict:
    config = site_config()
    return {
        "ok": True,
        "service": "website",
        "manifestUrl": config["manifestUrl"],
    }


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
        },
        "announcement": public_announcement(config),
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
def latest_launcher(current_version: str | None = None, response: Response = None) -> dict:
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    release = launcher_release(site_config())
    if current_version is not None:
        try:
            release["mandatory"] = update_required(release, current_version)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="客户端版本号无效") from error
    return release


@app.get("/api/v1/launcher/updater/{target}/{arch}/{current_version}")
def tauri_updater(target: str, arch: str, current_version: str, response: Response = None):
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    # 当前只发布 Windows x64。Tauri 对没有更新的情况要求 204。
    if target != "windows" or arch not in {"x86_64", "x86-64", "amd64"}:
        return Response(status_code=204)

    release = launcher_release(site_config())
    latest = str(release.get("version", "0.0.0"))
    try:
        if version_key(latest) <= version_key(current_version):
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        required = update_required(release, current_version)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="客户端版本号无效") from error

    signature = str(release.get("signature") or "").strip()
    if not signature:
        raise HTTPException(status_code=503, detail="当前客户端发布缺少 Tauri updater 签名")

    return {
        "version": latest,
        "pub_date": release.get("pubDate"),
        "url": release["url"],
        "signature": signature,
        "notes": release.get("notes", ""),
        "size": release.get("size", 0),
        "mandatory": required,
        "minSupportedVersion": release.get("minSupportedVersion"),
        "blockedVersions": release.get("blockedVersions", []),
        "updateReason": release.get("updateReason", ""),
    }


@app.get("/api/v1/manifest")
def manifest() -> JSONResponse:
    config = site_config()
    return JSONResponse(
        {
            "manifestUrl": config["manifestUrl"],
            "filesBaseUrl": config["filesBaseUrl"],
            "mirrorBaseUrl": config["mirrorBaseUrl"],
            "announcement": public_announcement(config),
            "launcher": launcher_release(config),
        },
        headers={"Cache-Control": "public, max-age=30"},
    )


@app.get("/account.html", include_in_schema=False)
@app.get("/account", include_in_schema=False)
def account_page(request: Request):
    account = web_auth_store.session(request.cookies.get("bmc_session"))
    if account is None:
        return begin_login(request, "/account.html")
    return protected_page("account.html")


def protected_page(filename: str):
    # In production the HTML lives only in the web image. Nginx serves this
    # internal location ONLY after the API has authenticated the request.
    if os.getenv("BMC_SERVE_WEB", "1") == "0":
        return Response(headers={
            "X-Accel-Redirect": f"/__protected/{filename}",
            "Cache-Control": "private, no-store",
        }, media_type="text/html")
    return FileResponse(WEB_ROOT / filename, headers={"Cache-Control": "private, no-store"})


@app.get("/admin.html", include_in_schema=False)
def admin_page(request: Request):
    account = web_auth_store.session(request.cookies.get("bmc_session"))
    if account is None:
        return begin_login(request, "/admin.html")
    if account.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return protected_page("admin.html")


def begin_login(request: Request, return_to: str) -> RedirectResponse:
    browser_token = request.cookies.get("bmc_oauth_browser", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", browser_token):
        browser_token = secrets.token_urlsafe(32)
    state, _verifier, challenge = web_auth_store.create_login(safe_return_to(return_to), browser_token)
    response = RedirectResponse(oidc_client.authorize_url(state, challenge), status_code=303)
    response.set_cookie(
        "bmc_oauth_browser", browser_token, max_age=1200, httponly=True,
        secure=os.getenv("BMC_PUBLIC_URL", "").lower().startswith("https://"),
        samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/api/v1/auth/entry")
def account_entry(request: Request, return_to: str = "/account.html") -> RedirectResponse:
    destination = safe_return_to(return_to)
    if web_auth_store.session(request.cookies.get("bmc_session")) is not None:
        return RedirectResponse(destination, status_code=303)
    return begin_login(request, destination)


@app.get("/api/v1/auth/login")
def login(request: Request, return_to: str = "/account.html") -> RedirectResponse:
    return account_entry(request, return_to)


@app.get("/api/v1/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = "") -> RedirectResponse:
    login_state = web_auth_store.consume_login(state, request.cookies.get("bmc_oauth_browser")) if state else None
    if login_state is None:
        return RedirectResponse("/?auth=invalid_state", status_code=303)
    verifier, return_to = login_state
    if error or not code:
        return RedirectResponse(f"/?auth={quote(error or 'missing_code', safe='')}", status_code=303)
    try:
        tokens = oidc_client.exchange_code(code, verifier)
        account = oidc_client.userinfo(str(tokens.get("access_token", "")))
    except (RuntimeError, ValueError):
        return RedirectResponse("/?auth=failed", status_code=303)
    token = web_auth_store.create_session(account)
    # Rotate any previous website session when completing a new login.
    web_auth_store.logout(request.cookies.get("bmc_session"))
    response = RedirectResponse(return_to, status_code=303)
    response.set_cookie(
        "bmc_session",
        token,
        max_age=14 * 24 * 3600,
        httponly=True,
        secure=os.getenv("BMC_PUBLIC_URL", "").lower().startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


@app.get("/api/v1/auth/terminal", include_in_schema=False)
def terminal_login_page(request: Request):
    if os.getenv("BMC_TERMINAL_SSO_ENABLED", "0") != "1":
        return RedirectResponse("/account.html", status_code=303)
    return protected_page("terminal-login.html")


@app.post("/api/v1/auth/terminal/exchange", include_in_schema=False)
async def terminal_login_exchange(request: Request):
    if os.getenv("BMC_TERMINAL_SSO_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail="Terminal login unavailable")
    public_url = os.getenv("BMC_PUBLIC_URL", "").rstrip("/")
    if not public_url or request.headers.get("origin") != public_url or request.headers.get("x-muxi-terminal-action") != "1":
        raise HTTPException(status_code=403, detail="Invalid terminal request origin")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(status_code=403, detail="Invalid terminal request type")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1024:
            raise HTTPException(status_code=413, detail="Terminal request too large")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"ticket", "verifier", "requestId"}:
            raise ValueError()
        if any(not isinstance(payload[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}",payload[k]) for k in ("ticket","verifier")):
            raise ValueError()
        if not isinstance(payload["requestId"],str) or not re.fullmatch(r"[0-9a-f-]{36}",payload["requestId"]):
            raise ValueError()
        account = await run_in_threadpool(oidc_client.exchange_terminal_ticket,payload)
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=401, detail="Terminal login expired; use normal platform login") from None
    token = web_auth_store.create_session(account)
    web_auth_store.logout(request.cookies.get("bmc_session"))
    response = RedirectResponse("/account.html", status_code=303, headers={"Cache-Control":"private, no-store"})
    response.set_cookie("bmc_session",token,max_age=14*86400,httponly=True,
                        secure=public_url.lower().startswith("https://"),samesite="lax",path="/")
    return response


@app.get("/api/v1/auth/me")
def me(account=Depends(current_account)) -> dict:
    return {"user": account.public()}


@app.get("/api/v1/player/profile")
def player_profile(account=Depends(current_player_account)) -> dict:
    profile = web_auth_store.player_profile(account)
    return {"user": account.public(), "player": profile.public(), "gameOp": platform_store.op_status(account.uid), "permissions": {**platform_store.permissions(account.uid), "canManageGameOp": platform_store.permissions(account.uid)["platformAdmin"], "platformAdmin": account.role == "admin" or platform_store.permissions(account.uid)["platformAdmin"]}}


def player_skin_payload(uid: int) -> dict:
    # 启动器的页面只允许 data: 图片，贴图直接随响应带回去，省一趟请求。
    # default 是没上传时别人看到的样子（Steve），启动器拿它做预览。
    def with_png(skin: dict | None) -> dict | None:
        if skin is not None:
            png = skin_store.texture(skin["hash"])
            skin["png"] = base64.b64encode(png).decode("ascii") if png else None
        return skin
    return {"skin": with_png(skin_store.get(uid)), "default": with_png(skin_store.default())}


@app.get("/api/v1/player/skin")
def player_skin(account=Depends(current_player_account)) -> dict:
    return player_skin_payload(account.uid)


@app.put("/api/v1/player/skin")
async def save_player_skin(request: Request, account=Depends(current_player_account)) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=400, detail="请求格式不对") from error
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求格式不对")
    model = str(body.get("model") or "default")
    png = None
    encoded = body.get("png")
    if encoded:
        encoded = str(encoded).split(",", 1)[-1] if str(encoded).startswith("data:") else str(encoded)
        if len(encoded) > (SKIN_MAX_BYTES // 3 + 1) * 4:
            raise HTTPException(status_code=400, detail="皮肤文件太大（上限 64 KB）")
        try:
            png = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="皮肤数据编码不对") from error
    try:
        await run_in_threadpool(skin_store.save, account.uid, model, png)
    except SkinError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return await run_in_threadpool(player_skin_payload, account.uid)


@app.delete("/api/v1/player/skin")
def delete_player_skin(account=Depends(current_player_account)) -> dict:
    skin_store.delete(account.uid)
    return player_skin_payload(account.uid)


# CustomSkinLoader 的 CustomSkinAPI：{root}{登录名}.json 与 {root}textures/{hash}，见 skins.py。
# 贴图按内容命名，永远不变；档案短缓存，换了皮肤别人重进服就能看到。
# 没上传过的玩家也有档案，指向默认的 Steve。
@app.get("/api/v1/skins/csl/textures/{digest}")
def csl_texture(digest: str) -> Response:
    path = skin_store.texture_path(digest)
    if path is None:
        raise HTTPException(status_code=404, detail="没有这张贴图")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/v1/skins/csl/{login_name}.json")
def csl_profile(login_name: str) -> JSONResponse:
    profile = skin_store.csl_profile(login_name)
    if profile is None:
        # 不是玩家 UID。CSL 把 404 当"这个源没有"，回落到原版默认皮肤
        return JSONResponse({"detail": "不是玩家"}, status_code=404,
                            headers={"Cache-Control": "public, max-age=30"})
    return JSONResponse(profile, headers={"Cache-Control": "public, max-age=30"})


# ── 崩溃日志：启动器上传，玩家中心查看。见 crash_reports.py ──

def crash_report_for(report_id: str, account) -> dict:
    """本人或管理员才能看；别人的报告和不存在的一样回 404，不暴露编号是否存在。"""
    report = crash_store.get(report_id)
    if report is None or (report["uid"] != account.uid and account.role != "admin"):
        raise HTTPException(status_code=404, detail="没有这份崩溃日志")
    return report


@app.post("/api/v1/player/crash-reports")
async def upload_crash_report(request: Request, account=Depends(current_player_account)) -> dict:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BUNDLE_BYTES:
        raise HTTPException(status_code=413, detail="日志包太大（上限 25 MB）")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks += chunk
        if len(chunks) > MAX_BUNDLE_BYTES:
            raise HTTPException(status_code=413, detail="日志包太大（上限 25 MB）")
    try:
        report = await run_in_threadpool(crash_store.add, account.uid, bytes(chunks))
    except CrashReportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"report": report}


@app.get("/api/v1/player/crash-reports")
def list_crash_reports(account=Depends(current_player_account)) -> dict:
    return {"reports": crash_store.list(account.uid)}


@app.get("/api/v1/player/crash-reports/{report_id}")
def crash_report_detail(report_id: str, account=Depends(current_player_account)) -> dict:
    return {"report": crash_report_for(report_id, account)}


@app.get("/api/v1/player/crash-reports/{report_id}/files/{name}")
def crash_report_file(report_id: str, name: str, account=Depends(current_player_account)) -> Response:
    crash_report_for(report_id, account)
    content = crash_store.read_file(report_id, name)
    if content is None:
        raise HTTPException(status_code=404, detail="没有这个文件")
    text, truncated = content
    return Response(text, media_type="text/plain; charset=utf-8",
                    headers={"X-Truncated": "1" if truncated else "0"})


@app.get("/api/v1/player/crash-reports/{report_id}/download")
def crash_report_download(report_id: str, account=Depends(current_player_account)) -> Response:
    crash_report_for(report_id, account)
    path = crash_store.bundle_path(report_id)
    if path is None:
        raise HTTPException(status_code=404, detail="没有这份崩溃日志")
    return FileResponse(path, media_type="application/zip", filename=f"crash-{report_id}.zip")


@app.get("/api/v1/admin/crash-reports")
def admin_crash_reports(limit: int = 100, _account=Depends(admin_account)) -> dict:
    return {"reports": crash_store.recent(limit)}


@app.delete("/api/v1/player/crash-reports/{report_id}")
def delete_crash_report(report_id: str, account=Depends(current_player_account)) -> dict:
    if not crash_store.delete(account.uid, report_id):
        raise HTTPException(status_code=404, detail="没有这份崩溃日志")
    return {"ok": True}


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response) -> dict:
    web_auth_store.logout(request.cookies.get("bmc_session"))
    response.delete_cookie("bmc_session", path="/")
    return {"ok": True}


@app.get("/api/v1/admin/users")
def admin_users(limit: int = 200, _account=Depends(admin_account)) -> dict:
    if not oidc_client.client_secret:
        raise HTTPException(status_code=503, detail="统一账户管理凭据尚未配置")
    try:
        return {"users": oidc_client.list_users(limit)}
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/download")
def download_launcher() -> JSONResponse:
    return JSONResponse(launcher_release(site_config()))


def platform_service_key(request: Request):
    key = os.getenv("BMC_GAME_SERVICE_KEY", "")
    if os.getenv("BMC_GAME_PLATFORM_ENABLED") != "1" or len(key) < 32:
        raise HTTPException(503, "Game platform integration is disabled")
    supplied = request.headers.get("x-muxi-server-key", "")
    if not hmac.compare_digest(key.encode(), supplied.encode()):
        raise HTTPException(401, "Invalid game service credential")


@app.post("/api/internal/game/task-claims", dependencies=[Depends(platform_service_key)], include_in_schema=False)
async def game_task_claim(request: Request):
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "Event too large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"uid", "day", "taskId", "hard"}:
            raise ValueError("Invalid event fields")
        added = platform_store.credit(data["uid"], data["day"], data["taskId"], data["hard"])
    except LookupError as error:
        raise HTTPException(409, str(error))
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error))
    return {"ok": True, "credited": added}


@app.get("/api/internal/game/admission/{uid}", dependencies=[Depends(platform_service_key)], include_in_schema=False)
def game_admission(uid: int):
    try:
        return {"uid": uid, "allowed": not platform_store.banned(uid), "points": platform_store.point_balance(uid)}
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.post("/api/internal/game/results", dependencies=[Depends(platform_service_key)], include_in_schema=False)
async def game_result(request: Request):
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "Event too large")
    try:
        rewards = json.loads(os.getenv("BMC_MINIGAME_REWARDS_JSON", "{}"))
    except (ValueError, TypeError):
        raise HTTPException(503, "Invalid minigame reward configuration") from None
    try:
        result = platform_store.credit_game_result(json.loads(raw), rewards)
    except LookupError as error:
        raise HTTPException(409, str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True, **result}


@app.post("/api/v1/platform/game-bans/{uid}")
async def platform_game_ban(uid: int, request: Request, account=Depends(current_account)):
    # Cookie writes require the actual site origin; arbitrary cross-site forms are refused.
    origin = request.headers.get("origin", "")
    expected = os.getenv("BMC_PUBLIC_URL", str(request.base_url).rstrip("/"))
    if origin != expected.rstrip("/") or request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(403, "Same-origin JSON request required")
    try:
        data = await request.json()
        platform_store.set_ban(account.uid, uid, data["banned"], data["reason"], account.role == "admin")
    except PermissionError as error:
        raise HTTPException(403, str(error))
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(400, str(error))
    return {"ok": True, "uid": uid, "banned": platform_store.banned(uid)}



def platform_op_admin(account=Depends(current_account)):
    if not platform_store.permissions(account.uid)["platformAdmin"]:
        raise HTTPException(403, "Platform administrator required")
    return account


@app.get("/api/v1/player/game-op")
def own_game_op(account=Depends(current_player_account)):
    return {"uid": str(account.uid), **platform_store.op_status(account.uid)}


@app.get("/api/v1/platform/game-ops/{uid}")
def platform_op_target(uid: int, account=Depends(platform_op_admin)):
    try:
        return {"target": platform_store.op_target(account.uid, uid)}
    except PermissionError as error:
        raise HTTPException(403, str(error))
    except LookupError as error:
        raise HTTPException(404, str(error))
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.post("/api/v1/platform/game-ops/{uid}")
async def platform_set_op(uid: int, request: Request, account=Depends(platform_op_admin)):
    expected = os.getenv("BMC_PUBLIC_URL", str(request.base_url).rstrip("/")).rstrip("/")
    if request.headers.get("origin") != expected or request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(403, "Same-origin JSON request required")
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "Request too large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"level","expectedRevision","expectedLevel","identityConfirmation","reason"}:
            raise ValueError("Invalid OP request fields")
        result = platform_store.set_op(account.uid, uid, data["level"], data["expectedRevision"],
            data["expectedLevel"], data["identityConfirmation"], data["reason"])
    except PermissionError as error:
        raise HTTPException(403, str(error))
    except LookupError as error:
        raise HTTPException(404, str(error))
    except RuntimeError as error:
        raise HTTPException(409, str(error))
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error))
    return {"uid": str(uid), **result}


def op_sync_service_key(request: Request):
    # Existing website server-key trust, with independent explicit rollout opt-in.
    if os.getenv("BMC_GAME_OP_SYNC_ENABLED") != "1":
        raise HTTPException(503, "Game OP synchronization is disabled")
    key = os.getenv("BMC_GAME_SERVICE_KEY", "")
    supplied = request.headers.get("x-muxi-server-key", "")
    if len(key) < 32 or not hmac.compare_digest(key.encode(), supplied.encode()):
        raise HTTPException(401, "Server authentication required")


@app.get("/api/internal/game/ops-sync/", dependencies=[Depends(op_sync_service_key)], include_in_schema=False)
def game_op_feed(afterUid: int = 0):
    try:
        return platform_store.op_feed(afterUid)
    except ValueError as error:
        raise HTTPException(400, str(error))
    except (LookupError, RuntimeError) as error:
        raise HTTPException(409, str(error))


@app.post("/api/internal/game/ops-sync/ack", dependencies=[Depends(op_sync_service_key)], include_in_schema=False)
async def game_op_ack(request: Request):
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "Acknowledgement too large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"uid","revision","applied","observedLevel","error"}:
            raise ValueError("Invalid acknowledgement fields")
        # UID is sent as canonical decimal text to avoid browser/JSON integer rounding.
        uid_text = data["uid"]
        if not isinstance(uid_text, str) or not uid_text.isascii() or not uid_text.isdecimal() or str(int(uid_text)) != uid_text:
            raise ValueError("Invalid UID")
        result = platform_store.acknowledge_op(int(uid_text), data["revision"], data["applied"], data["observedLevel"], data["error"])
    except (RuntimeError, LookupError) as error:
        raise HTTPException(409, str(error))
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error))
    return {"ok": True, **result}



@app.post("/api/internal/game/ops-sync/observations", dependencies=[Depends(op_sync_service_key)], include_in_schema=False)
async def game_op_observations(request: Request):
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(413, "Observation batch too large")
    try:
        accepted = platform_store.observe_ops(json.loads(raw))
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error))
    return {"ok": True, "accepted": accepted}


if os.getenv("BMC_SERVE_WEB", "1") == "1":
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(WEB_ROOT / "favicon.svg", media_type="image/svg+xml")

    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="website")
