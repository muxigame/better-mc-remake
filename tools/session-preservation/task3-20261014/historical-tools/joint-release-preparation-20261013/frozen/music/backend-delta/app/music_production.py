"""Opt-in production bootstrap, no main import, secrets or cloud calls."""
from pathlib import Path
import os,shutil
from .music_audio import AudioNormalizer
from .music_production_storage import ProductionStorage,ProductionBudget,ProductionLimits

def configure(runtime,signer,data_directory,*,limits,ffmpeg,allowed_uids=None,allowed_jobs=None,initial=(0,0,0,0),storage_factory=ProductionStorage):
    ffmpeg=Path(ffmpeg)
    if not ffmpeg.is_file():raise ValueError('reviewed_music_ffmpeg_required')
    budget=ProductionBudget(Path(data_directory)/'music-oss-usage.db',limits,allowed_uids,allowed_jobs,initial)
    storage=storage_factory(signer,budget)
    runtime.get().normalizer=AudioNormalizer(str(ffmpeg))
    runtime.get().cumulative_quotas=False
    runtime.configure_storage(storage)
    return storage

def configure_from_environment(runtime,signer,data_directory,environment=None):
    values=os.environ if environment is None else environment
    if values.get('MUSIC_OSS_ENABLE','0')!='1':return None
    if values.get('MUSIC_OSS_DEPLOYMENT_APPROVED')!='PRIVATE_SIGNED_V1':raise ValueError('music_production_enablement_not_reviewed')
    # Product release: all authenticated platform UIDs; old trial fields are
    # deliberately obsolete. QA explicitly supplies its own bounded provider.
    limits=ProductionLimits(None,None,None,None)
    ffmpeg=values.get('MUSIC_FFMPEG_EXECUTABLE') or shutil.which('ffmpeg')
    if not ffmpeg:raise ValueError('reviewed_music_ffmpeg_required')
    return configure(runtime,signer,data_directory,limits=limits,ffmpeg=ffmpeg)

def mount_music_application(baseline,environment=None):
    """Works with the actual pre-music main exports, not a music-patched main.
    Keeps all baseline routes/authority, adds missing music routes once only.
    """
    from .music_runtime import MusicRuntime
    from .music_upload import music_upload_router
    from .music_upload_identity import music_identities,music_live_identity
    from .music_intent_guard import MusicIntentGuard
    required=['app','store','signer','settings','terminal_access']
    if any(not hasattr(baseline,name) for name in required):raise RuntimeError('music_baseline_exports_missing')
    app,settings=baseline.app,baseline.settings
    if not getattr(app.state,'music_intent_guard',False):
        app.add_middleware(MusicIntentGuard);app.state.music_intent_guard=True
    runtime=getattr(app.state,'music_runtime',None) or getattr(baseline,'music_runtime',None)
    if runtime is None:runtime=MusicRuntime(settings.database_path.parent/'music-uploads.db',baseline.signer,settings.issuer)
    launcher,server=music_identities(lambda:settings,lambda:baseline.store,lambda:baseline.terminal_access)
    router=music_upload_router(runtime.get,launcher,server,
        music_live_identity(lambda:settings,lambda:baseline.store,lambda:baseline.terminal_access))
    existing={(getattr(route,'path',None),method) for route in app.routes for method in (getattr(route,'methods',None) or set())}
    for route in router.routes:
        methods=getattr(route,'methods',None) or set()
        if any((route.path,method) not in existing for method in methods):
            app.router.routes.append(route)
            existing.update((route.path,method) for method in methods)
    storage=runtime.get().storage
    if storage is None:storage=configure_from_environment(runtime,baseline.signer,settings.database_path.parent,environment)
    if storage is not None:app.add_event_handler('shutdown',storage.stop)
    app.state.music_runtime=runtime
    return app,runtime,storage
