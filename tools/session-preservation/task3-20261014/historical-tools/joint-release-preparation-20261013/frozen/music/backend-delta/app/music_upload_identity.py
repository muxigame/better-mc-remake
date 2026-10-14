"""Reuse muxi-auth authority without changing its configuration or permissions."""
from .music_upload import MusicError


def music_identities(settings_getter, store_getter, bindings_getter):
    from .terminal_server_auth import terminal_player

    def active(account, session, settings):
        if not settings.terminal_sso_enabled:
            raise MusicError(503, 'music_identity_unconfigured')
        if not bindings_getter().active(account.id, session, settings.terminal_sso_server_key):
            raise MusicError(401, 'live_account_connection_required')
        return account.uid, session

    def launcher(request, session):
        settings=settings_getter()
        scheme, _, access=request.headers.get('authorization', '').partition(' ')
        result=store_getter().access_token(access if scheme.lower()=='bearer' else '')
        if result is None or result[1]!=settings.bmc_launcher_client_id:
            raise MusicError(401, 'launcher_authorization_required')
        return active(result[0], session, settings)

    def server(request, session):
        settings=settings_getter()
        # This header is accepted only from the existing authenticated MC server.
        try:uid=int(request.headers.get('x-muxi-player-uid', ''))
        except ValueError:raise MusicError(401, 'game_identity_required') from None
        account=terminal_player(settings,store_getter(),uid,request.headers.get('x-muxi-server-key',''))
        return active(account,session,settings)

    return launcher, server


def music_live_identity(settings_getter, store_getter, bindings_getter):
    """Proof redemption uses only the already bound account; no new credential."""
    def live(uid, session):
        settings = settings_getter()
        account = store_getter().account_by_uid(uid)
        if account is None or not settings.terminal_sso_enabled or not bindings_getter().active(
                account.id, session, settings.terminal_sso_server_key):
            raise MusicError(401, 'live_account_connection_required')
        return uid, session
    return live
