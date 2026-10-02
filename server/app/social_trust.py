"""Social-only server credential. Does not enable game admission, rewards, or OP sync."""
import hmac
import os

from fastapi import HTTPException, Request


def social_service_key(request: Request):
    key = os.getenv('BMC_GAME_SOCIAL_KEY', '')
    # Reject accidental reuse even when the broader integration is currently disabled.
    broader_key = os.getenv('BMC_GAME_SERVICE_KEY', '')
    if (os.getenv('BMC_GAME_SOCIAL_ENABLED') != '1' or len(key) < 32
            or (broader_key and hmac.compare_digest(key.encode(), broader_key.encode()))):
        raise HTTPException(503, 'Game social integration is disabled')
    supplied = request.headers.get('x-muxi-server-key', '')
    if not hmac.compare_digest(key.encode(), supplied.encode()):
        raise HTTPException(401, 'Invalid game social credential')
