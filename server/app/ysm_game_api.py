"""Versioned machine wire. No credentials or live-connection authority by default."""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from .ysm_assets import AssetError, trusted_uid


@dataclass(frozen=True)
class GameMachine:
    server_id: str


@dataclass(frozen=True)
class GameConnection:
    server_id: str
    connection_id: str
    binding_generation: str
    uid: str
    live: bool = True


def ysm_game_router(store_or_provider, authorize_machine=None, resolve_connection=None):
    """Hooks supplied by the approved machine/listener integration, never HTTP.

    authorize_machine(request, purpose)->GameMachine;
    resolve_connection(machine, claim)->GameConnection. Either may be async.
    Missing hooks fail closed. User Bearer/cookies are not a machine fallback.
    """
    router = APIRouter(prefix='/api/internal/game/ysm', include_in_schema=False)
    headers = {'Cache-Control': 'private, no-store', 'Vary': 'Authorization',
               'X-Content-Type-Options': 'nosniff'}

    def store():
        return store_or_provider() if callable(store_or_provider) else store_or_provider

    async def invoke(hook, *args):
        if hook is None:
            raise HTTPException(503, 'ysm_game_authority_not_configured')
        value = hook(*args)
        return await value if inspect.isawaitable(value) else value

    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value):
            raise HTTPException(400, 'invalid_game_context')
        return value

    async def machine(request, purpose):
        value = await invoke(authorize_machine, request, purpose)
        if not isinstance(value, GameMachine):
            raise HTTPException(403, 'invalid_machine_authority')
        identifier(value.server_id)
        return value

    async def body(request, required, optional=()):
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise HTTPException(415, 'json_required')
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 16384:
                raise HTTPException(413, 'wire_request_too_large')
            raw.extend(chunk)
        try:
            value = json.loads(raw)
            if (not isinstance(value, dict) or not set(required) <= set(value)
                    or set(value) - set(required) - set(optional)):
                raise ValueError()
            return value
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, 'invalid_wire_fields')

    def revision(value):
        if type(value) is not int or value < 0:
            raise HTTPException(400, 'invalid_permission_revision')
        return value

    async def connection(authority, claim):
        if not isinstance(claim, dict) or set(claim) != {'serverId', 'connectionId', 'bindingGeneration', 'uid'}:
            raise HTTPException(400, 'invalid_game_context')
        for field in ('serverId', 'connectionId', 'bindingGeneration'):
            identifier(claim[field])
        if claim['serverId'] != authority.server_id:
            raise HTTPException(403, 'wrong_game_server')
        try:
            claimed_uid = trusted_uid(claim['uid'])
        except AssetError:
            raise HTTPException(403, 'invalid_account_uid')
        live = await invoke(resolve_connection, authority, dict(claim))
        if (not isinstance(live, GameConnection) or live.live is not True
                or live.server_id != authority.server_id
                or live.connection_id != claim['connectionId']
                or live.binding_generation != claim['bindingGeneration']):
            raise HTTPException(409, 'game_connection_changed')
        try:
            actual_uid = trusted_uid(live.uid)
        except AssetError:
            raise HTTPException(403, 'invalid_connection_authority')
        if actual_uid != claimed_uid:
            raise HTTPException(403, 'uid_claim_mismatch')
        return live, actual_uid

    async def checked_connection(authority, claim, expected):
        actual, _ = await connection(authority, claim)
        if actual != expected:
            raise HTTPException(409, 'game_connection_changed')

    async def call(method, *args):
        try:
            return await run_in_threadpool(method, *args)
        except AssetError as error:
            raise HTTPException(error.status, error.code) from None

    @router.post('/snapshot')
    async def snapshot(request: Request):
        await machine(request, 'sync')
        data = await body(request, (), ('afterSeq', 'limit', 'expectedRevision'))
        result = await call(store().bridge_bundle, data.get('afterSeq', 0),
                            data.get('limit', 100), data.get('expectedRevision'))
        return JSONResponse(result, headers=headers)

    @router.post('/catalog')
    async def catalog(request: Request):
        authority = await machine(request, 'player-read')
        data = await body(request, ('connection', 'revision'))
        live, uid = await connection(authority, data['connection'])
        result = await call(store().game_catalog, uid, revision(data['revision']))
        await checked_connection(authority, data['connection'], live)
        return JSONResponse(result, headers=headers)

    @router.post('/blob')
    async def blob(request: Request):
        authority = await machine(request, 'player-read')
        data = await body(request, ('connection', 'revision', 'modelId'))
        live, uid = await connection(authority, data['connection'])
        identifier(data['modelId'])
        payload, digest, size = await call(store().game_blob, uid, data['modelId'], revision(data['revision']))
        await checked_connection(authority, data['connection'], live)
        # Recheck permissions after IO as well as the live binding. No stale
        # buffered response may be released after a concurrent revocation.
        await call(store().game_catalog, uid, data['revision'])
        return Response(payload, media_type='application/octet-stream', headers={**headers,
            'X-Muxi-SHA256': digest, 'Content-Length': str(size),
            'Content-Disposition': 'attachment; filename="model.bin"'})

    @router.post('/staged')
    async def staged(request: Request):
        await machine(request, 'sync')
        data = await body(request, ('contentId', 'sha256', 'revision', 'generation', 'status'))
        if data['status'] != 'staged' or not isinstance(data['generation'], str) or not re.fullmatch(r'g_[0-9a-f]{32}', data['generation']):
            raise HTTPException(400, 'staging_receipt_only')
        result = await call(store().stage_receipt, identifier(data['contentId']), data['sha256'],
                            revision(data['revision']), data['generation'])
        return JSONResponse(result, headers=headers)

    return router
