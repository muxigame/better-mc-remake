"""Storage wrapper for the existing canonical local_mc_debug runner; no source fork."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys

MAX_NEW_BYTES = 2 * 1024**3
MIN_FREE_BYTES = 10 * 1024**3
NEW_BYTES = 0


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree_bytes(folder):
    paths = [folder, *folder.rglob('*')]
    if any(p.is_symlink() or p.is_junction() for p in paths):
        raise ValueError('Writable input tree must not contain symlinks/junctions')
    return sum(p.stat().st_size for p in paths if p.is_file())


def cache_jar(source, cache):
    key = digest(source)
    target = cache / (key + '.jar')
    cache.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if digest(target) != key:
            raise ValueError('Immutable cache corruption: ' + key)
        return target, False
    # Never link a mutable build output into the cache.
    if source.stat().st_size > MAX_NEW_BYTES or shutil.disk_usage(cache).free - source.stat().st_size < MIN_FREE_BYTES:
        raise ValueError('Insufficient disk budget for new immutable jar')
    temporary = cache / (key + '.' + str(os.getpid()) + '.tmp')
    try:
        shutil.copyfile(source, temporary)
        if digest(temporary) != key:
            raise ValueError('Source changed while freezing jar')
        try:
            os.link(temporary, target)
        except FileExistsError:
            if digest(target) != key:
                raise ValueError('Cache publication race')
    finally:
        temporary.unlink(missing_ok=True)
    return target, True


def copy_inputs(lab, mods, data, cache):
    global NEW_BYTES
    names = [p.name.casefold() for p in mods]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate mod jar filename')
    total = sum(tree_bytes(folder) for folder in data)
    total += sum(p.stat().st_size for p in mods if not (cache / (digest(p) + '.jar')).exists())
    if NEW_BYTES + total > MAX_NEW_BYTES or shutil.disk_usage(lab).free - total < MIN_FREE_BYTES:
        raise ValueError('New instance inputs exceed 2 GiB or 10 GiB disk reserve; reuse an owned instance')
    NEW_BYTES += total
    forbidden = {'mods', 'config', 'saves', 'world', 'libraries', 'versions', 'logs', 'assets', 'natives'}
    if any(folder.name in forbidden for folder in data):
        raise ValueError('Use original runner explicit world/natives parameters')
    (lab / 'mods').mkdir()
    (lab / 'config').mkdir()
    receipt = []
    for jar in mods:
        frozen, created = cache_jar(jar, cache)
        destination = lab / 'mods' / jar.name
        os.link(frozen, destination)  # No silent full-copy fallback across volumes.
        receipt.append({'path':jar.name,'sha256':frozen.stem,'cache':str(frozen),'newCacheObject':created})
    for folder in data:
        shutil.copytree(folder, lab / folder.name)  # All writable data is independent.
    (lab / 'immutable-mod-links.json').write_text(json.dumps(receipt, indent=2), 'utf8')


def main():
    global NEW_BYTES
    scripts = Path(__file__).resolve().parent
    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        def value(flag, default=None):
            return sys.argv[sys.argv.index(flag)+1] if flag in sys.argv else default
        world = value('--world')
        if world:
            NEW_BYTES += tree_bytes(Path(world))
        # Original runner independently copies extracted natives for each client.
        NEW_BYTES += int(value('--clients', '2')) * 256 * 1024**2
        if NEW_BYTES > MAX_NEW_BYTES:
            raise ValueError('World/natives reserve already exceeds 2 GiB; reuse owned test world')
    sys.path.insert(0, str(scripts))
    import local_mc_debug as runner
    import local_mc_runtime as runtime
    cache = scripts.parent / 'build' / 'local-mc-cache' / 'sha256'
    runtime.copy_inputs = lambda lab, mods, data: copy_inputs(lab, mods, data, cache)
    return runner.main()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf8', errors='replace')
    raise SystemExit(main())
