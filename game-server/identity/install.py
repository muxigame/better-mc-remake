"""Install only the declared UID/nickname additions; never run either game tree."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import urllib.request
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def atomic_bytes(path: Path, data: bytes, backups: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = path.read_bytes()
        if old == data:
            return
        backup = backups / hashlib.sha256(str(path).encode()).hexdigest()
        backup.write_bytes(old)
        backup.chmod(0o600)
    # Replace inode instead of overwriting a possible hardlink to pack/staging.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.muxi-install-', delete=False) as temp:
        temp.write(data)
        staged = Path(temp.name)
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', type=Path, default=ROOT / 'BMC5Server/BMC5Server')
    parser.add_argument('--client', type=Path, default=ROOT / 'pack/source/Better MC Remake [FORGE]')
    parser.add_argument('--auth-env', type=Path, default=ROOT.parent / 'muxi-auth/.env')
    args = parser.parse_args()
    for root in (args.server, args.client):
        if not (root / 'mods').is_dir():
            raise SystemExit('Expected mods directory missing: ' + str(root))
    built = ROOT / 'artifacts/game-server/muxi-identity-1.0.0.jar'
    if not built.is_file():
        raise SystemExit('Build the server bridge first.')
    dep = json.loads((HERE / 'dependencies.json').read_text(encoding='utf-8'))['simpleNicknames']
    cache = ROOT / 'artifacts/game-server' / dep['filename']
    if not cache.is_file():
        with urllib.request.urlopen(dep['url'], timeout=30) as response:
            data = response.read(4 * 1024 * 1024)
        if hashlib.sha512(data).hexdigest() != dep['sha512']:
            raise SystemExit('Simple Nicknames hash verification failed')
        cache.write_bytes(data)
    data = cache.read_bytes()
    if hashlib.sha512(data).hexdigest() != dep['sha512']:
        raise SystemExit('Cached Simple Nicknames hash mismatch')
    backups = ROOT / 'artifacts/uid-nickname-qa/install-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    backups.mkdir(parents=True, exist_ok=True)
    if not args.auth_env.is_file():
        raise SystemExit('Local identity service .env missing')
    env = args.auth_env.read_text(encoding='utf-8-sig')
    values = {}
    for raw in env.splitlines():
        if raw.strip() and not raw.strip().startswith('#') and '=' in raw:
            key, value = raw.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    key = values.get('MUXI_MC_PROFILE_KEY', '') or secrets.token_urlsafe(48)
    if len(key) < 32 or any(c in key for c in '\r\n'):
        raise SystemExit('Invalid dedicated game-server key')
    if not values.get('MUXI_MC_PROFILE_KEY'):
        lines = [line for line in env.splitlines() if not line.startswith('MUXI_MC_PROFILE_KEY=')]
        lines += ['MUXI_MC_PROFILE_KEY=' + key]
        atomic_bytes(args.auth_env, ('\n'.join(lines) + '\n').encode('utf-8'), backups)
    config = (HERE / 'simplenicknames.toml').read_bytes()
    for root in (args.server, args.client):
        atomic_bytes(root / 'mods' / dep['filename'], data, backups)
        atomic_bytes(root / 'config/simplenicknames/simplenicknames.toml', config, backups)
    atomic_bytes(args.server / 'mods/muxi-identity-1.0.0.jar', built.read_bytes(), backups)
    private_config = {
        'endpoint': 'https://account.muxigame.com/api/internal/minecraft/identity/',
        'serverKey': key,
        'refreshSeconds': 60,
    }
    private_path = args.server / 'config/muxi-identity-bridge.json'
    atomic_bytes(private_path, (json.dumps(private_config, indent=2) + '\n').encode(), backups)
    private_path.chmod(0o600)
    culling_file = args.client / 'config/entityculling.json'
    if culling_file.exists():
        culling = json.loads(culling_file.read_text(encoding='utf-8-sig'))
        whitelist = culling.setdefault('entityWhitelist', [])
        if 'minecraft:player' not in whitelist:
            whitelist.append('minecraft:player')
        atomic_bytes(culling_file, (json.dumps(culling, ensure_ascii=False, indent=2) + '\n').encode('utf-8'), backups)
    print('Installed pinned Simple Nicknames into both packages; server-only bridge and private configuration installed.')
    print('No game was started, no world/save was modified, no server credential entered the client package.')


if __name__ == '__main__': main()
