"""Read-only SSO audit. Output contains no credentials, cookies or URL queries.

Run on 131 for public status and optionally Core config. Run on HK with the
existing authorized shell for runtime container flags. This tool does not copy
keys, enable features, restart services, or read launcher account storage.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def public_probe(name, url, method='GET'):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(urllib.request.Request(url, method=method), timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    except Exception as error:
        return {'probe': name, 'error_type': type(error).__name__}
    with response:
        result = {'probe': name, 'status': response.code}
        location = response.headers.get('Location', '')
        # Only the exact known redirect destinations can appear in output.
        destination = location.split('?')[0].split('#')[0]
        if destination in ('/account.html', 'https://account.muxigame.com/oauth/authorize'):
            result['redirect'] = destination
        elif location:
            result['redirect'] = 'other_redacted_destination'
        if method == 'POST':
            try:
                detail = json.loads(response.read(4096)).get('detail')
                if detail in ('Terminal login is unavailable', 'Not Found', 'Launcher authentication required'):
                    result['recognized_detail'] = detail
            except (ValueError, AttributeError):
                pass
        return result


def container_audit(name, kind):
    # Capture all raw inspect data privately; never forward stdout/stderr/errors.
    try:
        result = subprocess.run(['docker', 'inspect', name], capture_output=True,
                                text=True, check=False, timeout=15)
        if result.returncode:
            return {'kind': kind, 'available': False}, None
        data = json.loads(result.stdout)[0]
        config = data['Config']
        environ = dict(item.split('=', 1) for item in config.get('Env', []) if '=' in item)
        labels = config.get('Labels') or {}
        revision = str(labels.get('org.opencontainers.image.revision', ''))
        import re
        output = {'kind': kind, 'available': True,
                  'running': bool(data.get('State', {}).get('Running')),
                  'image_id': data.get('Image') if re.fullmatch(r'sha256:[0-9a-f]{64}', str(data.get('Image', ''))) else None,
                  'source_revision': revision if re.fullmatch(r'[0-9a-f]{40}', revision) else None}
        if kind == 'auth':
            key = environ.get('MUXI_TERMINAL_SSO_SERVER_KEY', '')
            output.update(enabled=environ.get('MUXI_TERMINAL_SSO_ENABLED') == '1',
                          dedicated_key_present=bool(key), dedicated_key_valid_length=32 <= len(key) <= 512,
                          dedicated_key_distinct=bool(key) and all(key != environ.get(other, '') for other in
                              ('MUXI_MC_PROFILE_KEY', 'BMC_GAME_SERVICE_KEY', 'BMC_GAME_SOCIAL_KEY')))
        else:
            output.update(enabled=environ.get('BMC_TERMINAL_SSO_ENABLED') == '1',
                          public_origin_correct=environ.get('BMC_PUBLIC_URL', '').rstrip('/') == 'https://mc.muxigame.com',
                          issuer_correct=environ.get('BMC_AUTH_ISSUER', '').rstrip('/') == 'https://account.muxigame.com',
                          client_id_correct=environ.get('BMC_AUTH_CLIENT_ID') == 'better-mc-web',
                          client_secret_present=bool(environ.get('BMC_AUTH_CLIENT_SECRET')))
        return output, environ
    except Exception as error:
        return {'kind': kind, 'available': False, 'error_type': type(error).__name__}, None


def core_audit(path, auth):
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        root = json.loads(path.read_text(encoding='utf-8-sig'))
        features = root.get('features', {})
        terminal = features.get('terminalSso', {})
        key = terminal.get('serverKey', '')
        output = {'available': True, 'enabled': terminal.get('enabled') is True,
                  'endpoint_correct': terminal.get('endpoint', 'https://account.muxigame.com/api/internal/minecraft/') == 'https://account.muxigame.com/api/internal/minecraft/',
                  'dedicated_key_present': isinstance(key, str) and bool(key),
                  'dedicated_key_valid_length': isinstance(key, str) and 32 <= len(key) <= 512,
                  'login_enabled': features.get('login', {}).get('enabled') is True,
                  'op_sync_enabled': features.get('opSync', {}).get('enabled') is True,
                  'dedicated_key_distinct': bool(key) and all(key != features.get(other, {}).get('serverKey', '') for other in ('identity', 'login', 'opSync'))}
        if auth is not None:
            output['issuer_key_matches'] = bool(key) and key == auth.get('MUXI_TERMINAL_SSO_SERVER_KEY')
        return output
    except Exception as error:
        return {'available': False, 'error_type': type(error).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--auth-container')
    parser.add_argument('--web-api-container')
    parser.add_argument('--core-config', type=Path)
    args = parser.parse_args()
    output = {'checked_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'read_only': True, 'public': [
                  public_probe('account', 'https://mc.muxigame.com/account.html'),
                  public_probe('terminal_entry', 'https://mc.muxigame.com/api/v1/auth/terminal'),
                  public_probe('bootstrap_without_auth', 'https://account.muxigame.com/api/launcher/minecraft/terminal-bootstrap', 'POST')]}
    auth = None
    if args.auth_container:
        output['auth_runtime'], auth = container_audit(args.auth_container, 'auth')
    if args.web_api_container:
        output['web_api_runtime'], web = container_audit(args.web_api_container, 'web')
        if auth is not None and web is not None:
            output['web_auth_secret_matches'] = bool(web.get('BMC_AUTH_CLIENT_SECRET')) and web.get('BMC_AUTH_CLIENT_SECRET') == auth.get('MUXI_BMC_WEB_CLIENT_SECRET')
    if args.core_config:
        output['core_runtime'] = core_audit(args.core_config, auth)
    print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
