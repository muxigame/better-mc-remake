"""Compile the server-only bridge against the installed, pinned NeoForge jars.

No Minecraft/third-party classes are bundled into the output jar.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', type=Path, default=ROOT / 'BMC5Server/BMC5Server')
    parser.add_argument('--java-home', type=Path, required=True)
    parser.add_argument('--nickname-jar', type=Path, required=True)
    args = parser.parse_args()
    java = args.java_home / 'bin' / ('javac.exe' if os.name == 'nt' else 'javac')
    libraries = sorted((args.server / 'libraries').rglob('*.jar'))
    # Installed server directories also contain Mojang's original obfuscated
    # jars. Compile against the runtime mapped jar, never the originals.
    mapped = [p for p in libraries if p.name.endswith('-srg.jar')]
    libraries = mapped + [p for p in libraries if '/net/minecraft/' not in p.as_posix()]
    if not java.is_file() or len(mapped) != 1 or not args.nickname_jar.is_file():
        raise SystemExit('JDK, installed NeoForge libraries or Simple Nicknames jar missing')
    out = ROOT / 'artifacts/game-server'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as temp:
        build = Path(temp)
        classes = build / 'classes'
        classes.mkdir()
        cp = os.pathsep.join(str(p.resolve()) for p in [*libraries, args.nickname_jar])
        sources = list((SOURCE / 'src').rglob('*.java'))
        arguments = ['--release', '21', '-encoding', 'UTF-8', '-proc:none', '-classpath', cp, '-d', str(classes), *map(str, sources)]
        argfile = build / 'javac.args'
        argfile.write_text('\n'.join('"' + s.replace('\\', '/').replace('"', '\\"') + '"' for s in arguments), encoding='utf-8')
        subprocess.run([str(java), '@' + str(argfile)], check=True)
        target = out / 'muxi-identity-1.0.0.jar'
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            license_info = zipfile.ZipInfo('META-INF/LICENSE', (2026, 1, 1, 0, 0, 0))
            license_info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(license_info, (SOURCE / 'LICENSE').read_bytes())
            for base in (classes, SOURCE / 'resources'):
                for f in sorted(base.rglob('*')):
                    if f.is_file():
                        info = zipfile.ZipInfo(f.relative_to(base).as_posix(), (2026, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(info, f.read_bytes())
        print(json.dumps({'jar': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'size': target.stat().st_size}))


if __name__ == '__main__': main()
