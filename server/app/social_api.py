"""Shared player-center / terminal API, installed through existing auth dependencies."""
import os
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from .social import SocialStore, social_uid


def social_router(path, current_player_account, platform_service_key):
    store = SocialStore(path)
    router = APIRouter()

    def failure(error):
        if isinstance(error, PermissionError):
            raise HTTPException(403, 'Relationship unavailable')
        if isinstance(error, LookupError):
            raise HTTPException(404, 'Player or incoming request unavailable')
        raise HTTPException(400, str(error))

    def actor(account=Depends(current_player_account)):
        try:
            return store.register(account)
        except (ValueError, LookupError, PermissionError) as error:
            failure(error)

    @router.get('/api/v1/player/social')
    def snapshot(uid=Depends(actor)):
        return store.snapshot(uid)

    async def mutate(request, uid, peer, action):
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise HTTPException(403, 'JSON request required')
        # Cookie authentication wins in current_player_account, so cookie presence must
        # always require origin even if an attacker also supplies a bearer header.
        if request.cookies.get('bmc_session'):
            expected = os.getenv('BMC_PUBLIC_URL', str(request.base_url).rstrip('/')).rstrip('/')
            if request.headers.get('origin') != expected:
                raise HTTPException(403, 'Same-origin JSON request required')
        if request.headers.get('content-length', '0').isdigit() and int(request.headers.get('content-length','0')) > 1024:
            raise HTTPException(413, 'Social request too large')
        raw = await request.body()
        if len(raw) > 1024:
            raise HTTPException(413, 'Social request too large')
        try:
            if json.loads(raw or b'{}') != {}:
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(400, 'Empty JSON object required; actor is authenticated')
        key = request.headers.get('idempotency-key')
        if not key:
            raise HTTPException(400, 'Idempotency-Key required')
        try:
            changed = store.mutate(uid, social_uid(peer), action, key)
        except (ValueError, LookupError, PermissionError) as error:
            failure(error)
        return {'version': 1, 'request': key, 'status': 'ok', 'ok': True, 'changed': changed}

    @router.put('/api/v1/player/social/requests/{peer}')
    async def send(peer: str, request: Request, uid=Depends(actor)):
        return await mutate(request, uid, peer, 'request')

    @router.post('/api/v1/player/social/requests/{peer}/accept')
    async def accept(peer: str, request: Request, uid=Depends(actor)):
        return await mutate(request, uid, peer, 'accept')

    @router.delete('/api/v1/player/social/requests/{peer}')
    async def cancel(peer: str, request: Request, uid=Depends(actor)):
        return await mutate(request, uid, peer, 'cancel')

    @router.delete('/api/v1/player/social/friends/{peer}')
    async def remove(peer: str, request: Request, uid=Depends(actor)):
        return await mutate(request, uid, peer, 'remove')

    @router.put('/api/v1/player/social/blocks/{peer}')
    async def block(peer: str, request: Request, uid=Depends(actor)):
        return await mutate(request, uid, peer, 'block')

    @router.delete('/api/v1/player/social/blocks/{peer}')
    async def unblock(peer: str, request: Request, uid=Depends(actor)):
        return await mutate(request, uid, peer, 'unblock')

    @router.put('/api/internal/game/social/presence', dependencies=[Depends(platform_service_key)], include_in_schema=False)
    async def presence(request: Request):
        if len(await request.body()) > 256 * 1024:
            raise HTTPException(413, 'Presence snapshot too large')
        try:
            data = await request.json()
            if not isinstance(data, dict) or set(data) != {'players'}:
                raise ValueError('Invalid presence fields')
            store.presence(data['players'])
        except (ValueError, TypeError) as error:
            failure(error)
        return {'version': 1, 'ok': True, 'expiresInSeconds': store.PRESENCE_TTL}

    @router.get('/api/internal/game/social/eligibility/{acting_uid}/{target_uid}', dependencies=[Depends(platform_service_key)], include_in_schema=False)
    def eligibility(acting_uid: str, target_uid: str, source: str = 'online'):
        try:
            return {'version': 1, **store.eligibility(acting_uid, target_uid, source)}
        except (ValueError, LookupError, PermissionError) as error:
            failure(error)

    return router
