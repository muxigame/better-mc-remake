"""Read-only check. Never installs files or starts Minecraft."""
import argparse, hashlib, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--server', type=Path, required=True)
parser.add_argument('--client', type=Path, required=True)
args = parser.parse_args()
lock = json.loads(Path(__file__).with_name('input-lock.json').read_text(encoding='utf-8'))
errors = []
for side, root, rows, algorithm in [
    ('server', args.server, [{'path': 'mods/'+x['name'], 'digest': x['sha256']} for x in lock['serverMods']], 'sha256'),
    ('client', args.client, [{'path': x['path'], 'digest': x['sha1']} for x in lock['clientFiles'] if x['path'].startswith('mods/')], 'sha1')]:
    expected = {x['path'] for x in rows}
    actual = {'mods/'+p.name for p in (root/'mods').glob('*.jar')}
    errors.extend(side+': unexpected '+p for p in sorted(actual-expected))
    for row in rows:
        path = root/row['path']
        if not path.is_file() or hashlib.new(algorithm, path.read_bytes()).hexdigest() != row['digest']:
            errors.append(side+': missing or mismatched '+row['path'])
print(json.dumps({'passed': not errors, 'errors': errors, 'scope': 'product mod inputs only; no MC/QA/cloud started'}, indent=2))
raise SystemExit(1 if errors else 0)
