"""Explicit, credential-free review archive; no repository or deploy mutations."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'dedicated-sso'
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

record = json.loads((OUT / 'VERIFICATION.json').read_text(encoding='utf-8'))
assert record['validation']['actual_https_functional_checks'] == 52
assert record['validation']['core_connection_assertions_on_131'] == 61
assert sha(OUT / 'muxi-game-core-dedicated-sso-review.jar') == record['core_review_artifact']['sha256']
assert sha(OUT / 'inputs/TrustedAccounts.java') == record['compile_framework']['task2_frozen_trust_source_sha256']
for item in record['repositories'].values():
    assert sha(OUT / item['patch']) == item['patch_sha256']
for relative in record['files']:
    path = OUT / relative
    record['files'][relative] = {'sha256': sha(path), 'bytes': path.stat().st_size}
record['files']['HANDOFF.md'] = {'sha256': sha(OUT / 'HANDOFF.md'), 'bytes': (OUT / 'HANDOFF.md').stat().st_size}
record['files']['inputs/TrustedAccounts.java'] = {'sha256': sha(OUT / 'inputs/TrustedAccounts.java'),
    'bytes': (OUT / 'inputs/TrustedAccounts.java').stat().st_size, 'test_input_only': True}

names = [
    'HANDOFF.md', 'COORDINATION.md', 'VERIFICATION.json',
    'muxi-auth-dedicated-terminal-sso.patch', 'muxi-game-core-dedicated-terminal-sso.patch',
    'muxi-game-core-dedicated-sso-review.jar', 'auth-tests.log', 'core-build.log',
    'core-trust-131.json', 'core-trust-131.log', 'https-chain-131.json', 'https-chain-131.log',
    '131-probe-cleanup.json', 'inputs/TrustedAccounts.java',
]
controls = ['run_core_trust_131.py', 'run_dedicated_sso_chain.py', 'stage_dedicated_sso_131.py',
            'finalize_dedicated_sso.py']
record['review_archive_contents'] = ['dedicated-sso/' + x for x in names] + ['tests/' + x for x in controls]
record['reproduction_scripts'] = {x: {'sha256': sha(ROOT/'tests'/x), 'bytes': (ROOT/'tests'/x).stat().st_size}
    for x in controls}
(OUT / 'VERIFICATION.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')

archive = OUT / 'sso-dedicated-trust-review.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target:
    for name in names:
        target.write(OUT / name, 'dedicated-sso/' + name)
    for name in controls:
        target.write(ROOT / 'tests' / name, 'tests/' + name)
with zipfile.ZipFile(archive) as source:
    assert source.testzip() is None
    assert sorted(source.namelist()) == sorted(record['review_archive_contents'])
    assert not any(Path(x).suffix.lower() in ('.pem', '.pfx', '.p12', '.db', '.exe') for x in source.namelist())
    for name in names:
        assert hashlib.sha256(source.read('dedicated-sso/' + name)).hexdigest() == sha(OUT / name)
receipt = {'archive': archive.name, 'sha256': sha(archive), 'bytes': archive.stat().st_size,
    'file_count': len(record['review_archive_contents']), 'crc_and_content_hashes': 'passed',
    'production_mutation': False, 'review_only': True,
    'verification_sha256': sha(OUT / 'VERIFICATION.json')}
(OUT / 'DELIVERY.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
print(json.dumps(receipt))
