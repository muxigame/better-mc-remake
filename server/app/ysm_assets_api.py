"""User routes reuse the existing account dependency and its original credential."""
from __future__ import annotations

import asyncio
import json
import os
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .ysm_assets import AssetError, YsmAssetStore, request_key, safe_filename, trusted_uid, visibility as check_visibility


def ysm_assets_router(store_or_provider, current_player_account):
    """A provider permits normal app tests to replace the store without stale closures."""
    router = APIRouter()

    def store() -> YsmAssetStore:
        return store_or_provider() if callable(store_or_provider) else store_or_provider

    def actor(request: Request, account=Depends(current_player_account)):
        try:
            uid = trusted_uid(account.uid)
        except (AttributeError, AssetError):
            raise HTTPException(403, 'invalid_account_uid')
        expected_uid = request.headers.get('x-muxi-expected-uid')
        if expected_uid is not None:
            try:
                if trusted_uid(expected_uid) != uid:
                    raise HTTPException(409, 'account_changed')
            except AssetError:
                raise HTTPException(409, 'account_changed')
        return uid

    def reply(data):
        return JSONResponse(data, headers={'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'})

    def failure(error: AssetError):
        raise HTTPException(error.status, error.code,
                            headers={'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'})

    def same_origin(value):
        try:
            parts = urlsplit(value)
            if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
                return None
            if parts.path not in ('', '/') or parts.query or parts.fragment:
                return None
            return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == 'https' else 80)
        except (TypeError, ValueError):
            return None

    def write_boundary(request: Request, uid):
        expected_uid = request.headers.get('x-muxi-expected-uid')
        if expected_uid is not None:
            try:
                if trusted_uid(expected_uid) != uid:
                    raise HTTPException(409, 'account_changed')
            except AssetError:
                raise HTTPException(409, 'account_changed')
        if request.headers.get('sec-fetch-site', '').lower() == 'cross-site':
            raise HTTPException(403, 'same_origin_required')
        origin = request.headers.get('origin')
        expected = os.getenv('BMC_PUBLIC_URL', str(request.base_url).rstrip('/'))
        # Any accompanying website cookie keeps the browser CSRF boundary even
        # when explicit Bearer authentication wins account selection.
        if request.cookies.get('bmc_session') or origin is not None:
            if same_origin(origin) is None or same_origin(origin) != same_origin(expected):
                raise HTTPException(403, 'same_origin_required')

    def key_for(request):
        try:
            return request_key(request.headers.get('idempotency-key'))
        except AssetError as error:
            failure(error)

    async def small_json(request, expected_fields):
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise HTTPException(415, 'json_required')
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 1024:
                raise HTTPException(413, 'request_too_large')
            raw.extend(chunk)
        try:
            value = json.loads(raw or b'{}')
            if not isinstance(value, dict) or set(value) != set(expected_fields):
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise HTTPException(400, 'invalid_request_fields')

    @router.get('/api/v1/player/ysm/models')
    def list_models(include_deleted: bool = False, uid=Depends(actor)):
        try:
            return reply(store().list_models(uid, include_deleted))
        except AssetError as error:
            failure(error)

    @router.get('/api/v1/ysm/models/public')
    def public_models(uid=Depends(actor)):
        try:
            return reply(store().public_models(uid))
        except AssetError as error:
            failure(error)

    @router.get('/api/v1/ysm/models/public/by-sha256/{digest}')
    def public_by_sha256(digest: str, uid=Depends(actor)):
        try:
            return reply(store().public_by_sha256(uid, digest))
        except AssetError as error:
            failure(error)

    @router.post('/api/v1/player/ysm/models/references')
    async def create_reference(request: Request, uid=Depends(actor)):
        write_boundary(request, uid)
        data = await small_json(request, {'publicId', 'filename', 'visibility'})
        try:
            return reply(await run_in_threadpool(store().create_reference, uid, data['publicId'], data['filename'],
                                                 key_for(request), data['visibility']))
        except AssetError as error:
            failure(error)

    @router.post('/api/v1/player/ysm/models')
    async def upload(request: Request, filename: str, visibility: str = 'private', uid=Depends(actor)):
        write_boundary(request, uid)
        if set(request.query_params) - {'filename', 'visibility'}:
            raise HTTPException(400, 'invalid_request_fields')
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/octet-stream':
            raise HTTPException(415, 'binary_body_required')
        if request.headers.get('content-encoding', 'identity').lower() != 'identity':
            raise HTTPException(415, 'encoded_body_not_supported')
        asset_store = store()
        try:
            filename = safe_filename(filename)
            # Keep the uploaded visibility as an explicit user intention. Pending
            # opaque files are nevertheless never public or downloadable.
            check_visibility(visibility)
            key = key_for(request)
            length = request.headers.get('content-length')
            if length is not None and (not length.isascii() or not length.isdecimal() or len(length) > 19
                                       or int(length) > asset_store.max_file_bytes):
                raise AssetError(413, 'model_too_large')
            lease, path = await run_in_threadpool(asset_store.begin_upload, uid)
        except AssetError as error:
            failure(error)
        try:
            total, deadline = 0, time.monotonic() + 300
            with path.open('wb') as stream:
                iterator = request.stream().__aiter__()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HTTPException(408, 'upload_timed_out')
                    try:
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=min(30, remaining))
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        raise HTTPException(408, 'upload_timed_out')
                    total += len(chunk)
                    if total > asset_store.max_file_bytes:
                        raise AssetError(413, 'model_too_large')
                    await run_in_threadpool(stream.write, chunk)
            data = await run_in_threadpool(asset_store.ingest, uid, filename, path, key, visibility)
            return reply(data)
        except AssetError as error:
            failure(error)
        finally:
            await run_in_threadpool(asset_store.end_upload, uid, lease, path)

    @router.patch('/api/v1/player/ysm/models/{model_id}/visibility')
    async def set_visibility(model_id: str, request: Request, uid=Depends(actor)):
        write_boundary(request, uid)
        data = await small_json(request, {'visibility'})
        try:
            return reply(await run_in_threadpool(store().mutate, uid, model_id, 'visibility', key_for(request), data['visibility']))
        except AssetError as error:
            failure(error)

    @router.delete('/api/v1/player/ysm/models/{model_id}')
    async def delete(model_id: str, request: Request, uid=Depends(actor)):
        write_boundary(request, uid)
        # DELETE has no actor, directory or permanent-cleanup fields.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 1024:
                raise HTTPException(413, 'request_too_large')
            body.extend(chunk)
        if body not in (b'', b'{}'):
            raise HTTPException(400, 'invalid_request_fields')
        try:
            return reply(await run_in_threadpool(store().mutate, uid, model_id, 'delete', key_for(request)))
        except AssetError as error:
            failure(error)

    @router.post('/api/v1/player/ysm/models/{model_id}/restore')
    async def restore(model_id: str, request: Request, uid=Depends(actor)):
        write_boundary(request, uid)
        await small_json(request, set())
        try:
            return reply(await run_in_threadpool(store().mutate, uid, model_id, 'restore', key_for(request)))
        except AssetError as error:
            failure(error)

    @router.get('/api/v1/player/ysm/models/{model_id}/download')
    def download(model_id: str, uid=Depends(actor)):
        try:
            path, name = store().download(uid, model_id)
        except AssetError as error:
            failure(error)
        return FileResponse(path, media_type='application/octet-stream', filename=name,
                            headers={'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie',
                                     'X-Content-Type-Options': 'nosniff'})

    @router.post('/api/v1/player/ysm/models/{model_id}/select')
    async def select(model_id: str, request: Request, uid=Depends(actor)):
        write_boundary(request, uid)
        await small_json(request, set())
        try:
            return reply(await run_in_threadpool(store().select, uid, model_id, key_for(request)))
        except AssetError as error:
            failure(error)

    return router
