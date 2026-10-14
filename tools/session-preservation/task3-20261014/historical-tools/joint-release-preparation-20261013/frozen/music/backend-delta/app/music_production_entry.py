"""Explicit existing-service ASGI entrypoint; inactive unless owner enables it.

Deploy as app.music_production_entry:app only after reviewing configuration.
Original signer remains inside the original backend. No secret/role fallback.
"""
from . import main as _baseline
from .music_production import mount_music_application

app,music_runtime,_music_storage=mount_music_application(_baseline)
