"""Apply/check the reviewed Champions-only bonus-loot reduction.

Default: validate and preview. Pass --apply to write with backups.
Run from any directory. The script refuses unexpected edits to target files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SOURCE = ROOT / 'better-mc-remake/pack/source/Better MC Remake [FORGE]'
SERVER = ROOT / 'bmc5server'
BASE = [
    '1;minecraft:iron_ingot;1;false;10',
    '2;minecraft:gold_ingot;1;false;8',
    '3;minecraft:diamond;1;false;5',
    '3;minecraft:emerald;1;false;4',
    '4;minecraft:diamond;2;true;4',
    '5;minecraft:netherite_scrap;1;false;2',
    '5;minecraft:diamond;3;true;3',
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    assert SERVER.is_dir() and SOURCE.is_dir(), 'Unexpected workspace location'
    subprocess.run(['node', '--check', str(HERE / 'muxi_champions_bonus_loot.js')], check=True)
    subprocess.run(['node', str(HERE / 'test_champions_bonus_loot.cjs')], check=True)

    # Eligibility is minimum-tier based, not exact-tier. Match the incremental
    # real-item weight at each tier so cumulative empty and real weights match.
    tier_weights: dict[int, int] = defaultdict(int)
    for entry in BASE:
        fields = entry.split(';')
        tier_weights[int(fields[0])] += int(fields[4])
    additions = [f'{tier};minecraft:air;1;false;{weight}'
                 for tier, weight in sorted(tier_weights.items())]
    target = BASE + additions
    assertions = 0
    summaries = []
    for tier in [1, 2, 3, 4, 5, 10]:
        original = [e.split(';') for e in BASE if int(e.split(';')[0]) <= tier]
        eligible = [e.split(';') for e in target if int(e.split(';')[0]) <= tier]
        before_weight = sum(int(e[4]) for e in original)
        after_weight = sum(int(e[4]) for e in eligible)
        assert after_weight == before_weight * 2
        assertions += 1
        for entry in original:
            assert Fraction(int(entry[4]), after_weight) == Fraction(int(entry[4]), before_weight) / 2
            assertions += 1
        summaries.append({'tier': tier, 'real_weight': before_weight,
                          'empty_weight': after_weight - before_weight,
                          'expected_kept_fraction': 0.5})

    writes: dict[Path, bytes] = {}
    originals: dict[Path, bytes | None] = {}
    canonical_script = (HERE / 'muxi_champions_bonus_loot.js').read_bytes()
    for root in [SERVER, SOURCE]:
        config_path = root / 'config/champions-server.toml'
        before = config_path.read_bytes()
        text = before.decode('utf-8-sig')
        config = tomllib.loads(text)
        current = config['loot']['lootDrops']
        if current not in [BASE, target]:
            raise RuntimeError(f'Unreviewed reward list; refusing overwrite: {config_path}')
        if current == BASE:
            replacement = 'lootDrops = ' + json.dumps(target, separators=(', ', ': '))
            text, count = re.subn(r'(?m)^(\s*)lootDrops\s*=\s*\[[^\r\n]*\]',
                                 lambda m: m.group(1) + replacement, text)
            assert count == 1
            after_config = tomllib.loads(text)
            expected = dict(config)
            expected['loot'] = dict(config['loot'], lootDrops=target)
            assert after_config == expected, 'Unexpected setting changed'
            payload = text.encode('utf-8')
            if before.startswith(b'\xef\xbb\xbf'):
                payload = b'\xef\xbb\xbf' + payload
            originals[config_path] = before
            writes[config_path] = payload
        else:
            print('ALREADY BALANCED', config_path.relative_to(ROOT))
        script_path = root / 'kubejs/server_scripts/muxi_champions_bonus_loot.js'
        existing = script_path.read_bytes() if script_path.exists() else None
        if existing is not None and existing != canonical_script:
            raise RuntimeError(f'Unreviewed script; refusing overwrite: {script_path}')
        if existing != canonical_script:
            originals[script_path] = existing
            writes[script_path] = canonical_script

    print('PASS:', assertions, 'exact-rational reward probability assertions')
    print('EMPTY ENTRIES:', additions)
    print('CHANGES:', [str(p.relative_to(ROOT)) for p in writes])
    if not args.apply:
        print('DRY RUN ONLY; no live configuration changed')
        return
    if not writes:
        print('NO-OP: all target files already match; no further reduction applied')
        return

    backup = ROOT / 'better-mc-remake/pack/archive' / ('champions-balance-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'Champions extra item rewards only', 'buffs_changed': False,
              'experience_changed': False, 'server_restarted': False,
              'published_to_oss': False, 'tiers': summaries, 'files': []}
    for path, data in originals.items():
        relative = path.relative_to(ROOT)
        if data is not None:
            destination = backup / 'before' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        report['files'].append({'path': relative.as_posix(), 'existed': data is not None,
                                'before_sha256': hashlib.sha256(data).hexdigest() if data is not None else None,
                                'after_sha256': hashlib.sha256(writes[path]).hexdigest()})
    (backup / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    # Optimistic concurrency check before any live-file mutation.
    for path, old in originals.items():
        actual = path.read_bytes() if path.exists() else None
        if actual != old:
            raise RuntimeError(f'Concurrent file change, stopping: {path}')
    done: list[Path] = []
    try:
        for path, data in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.champions-balance.tmp')
            with temporary.open('xb') as stream:
                stream.write(data)
            os.replace(temporary, path)
            done.append(path)
            assert path.read_bytes() == data
    except BaseException:
        for path in done:
            if originals[path] is None:
                path.unlink()
            else:
                path.write_bytes(originals[path])
        raise
    print('VERIFIED', len(done), 'live/source files')
    print('BACKUP:', backup)
    print('No buff/XP/spawn/ordinary loot/JAR/core/packspec edits; no restart or OSS publication')


if __name__ == '__main__':
    main()
