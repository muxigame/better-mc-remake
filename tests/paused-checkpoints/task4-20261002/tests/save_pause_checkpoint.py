"""Save existing commits and non-repository source/evidence only. Never test or push."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'dedicated-sso'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def git(repo, *args):
    return subprocess.check_output(['git', '-c', 'core.excludesFile='+str(ROOT/'evidence/empty-git-ignore'),
        '-C', str(repo), *args], stderr=subprocess.PIPE).decode('utf-8', errors='replace').strip()

records = json.loads((OUT/'ALL-LOCAL-REPOSITORIES.json').read_text(encoding='utf-8'))
current = {x['repository']: x for x in json.loads((OUT/'PAUSED-REPOSITORIES.json').read_text(encoding='utf-8'))}
bundles = []
for item in records:
    repo = ROOT/'repos'/item['repository']
    assert not git(repo, 'status', '--porcelain')
    assert git(repo, 'rev-parse', 'HEAD') == item['commit']
    item['visibility'] = 'public'
    item['visibility_source'] = 'GitHub get_repo metadata, 2026-10-02'
    canonical = item['repository'].replace('-dedicated-sso-fixture','').replace('-dedicated-sso','').replace('-container','')
    item['canonical_release_remote'] = 'git@github-muxi:muxigame/'+canonical+'.git'
    if item['repository'] in current:
        current[item['repository']]['visibility'] = 'public'
    if item['repository'].endswith('-fixture'):
        item['new_owner_changes'] = False
        continue
    item['new_owner_changes'] = True
    item['current_dedicated_sso_fix'] = item['repository'].endswith('-dedicated-sso')
    base = item['head_parents']
    assert len(base.split()) == 1
    filename = item['repository']+'-checkpoint.bundle'
    destination = OUT/filename
    git(repo, 'bundle', 'create', str(destination), item['branch'], '^'+base)
    git(repo, 'bundle', 'verify', str(destination))
    assert item['commit'] in git(repo, 'bundle', 'list-heads', str(destination))
    bundles.append({'file': filename, 'sha256': sha(destination), 'bytes': destination.stat().st_size,
        'repository': item['repository'], 'branch': item['branch'], 'commit': item['commit'],
        'prerequisite': base, 'current_dedicated_sso_fix': item['current_dedicated_sso_fix'], 'verified': True})
(OUT/'ALL-LOCAL-REPOSITORIES.json').write_text(json.dumps(records, indent=2)+'\n', encoding='utf-8')
(OUT/'PAUSED-REPOSITORIES.json').write_text(json.dumps(list(current.values()), indent=2)+'\n', encoding='utf-8')

paths = set()
for folder in (OUT, ROOT/'artifacts', ROOT/'evidence'):
    for path in folder.glob('*'):
        if path.is_file() and path.suffix.lower() in ('.md', '.json', '.log', '.txt', '.patch', '.bundle'):
            if path.name not in ('PAUSED-SNAPSHOT.json',):
                paths.add(path)
for path in (ROOT/'tests').glob('*'):
    if path.is_file() and path.suffix.lower() in ('.py', '.ps1', '.rs', '.cjs'):
        paths.add(path)
for path in (ROOT/'dedicated-sso/inputs').glob('*.java'):
    paths.add(path)
for name in ('BUILD-AND-RELEASE.md', 'DEPLOYMENT.md', 'SSO_CONTAINER_ADAPTER.md', 'VERIFICATION.json'):
    paths.add(ROOT/name)
file_records = []
for path in sorted(paths):
    assert path.stat().st_size < 4*1024*1024, path
    assert path.name != '.env' and not any(x in path.parts for x in ('.git','.venv','__pycache__'))
    file_records.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path), 'bytes': path.stat().st_size})
archive = OUT/'pause-source-checkpoint.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target:
    for path in sorted(paths): target.write(path, path.relative_to(ROOT).as_posix())
with zipfile.ZipFile(archive) as source:
    assert source.testzip() is None
    for item in file_records:
        assert hashlib.sha256(source.read(item['path'])).hexdigest() == item['sha256']
record = {'saved_utc': datetime.now(timezone.utc).isoformat(), 'repositories': records, 'git_bundles': bundles,
    'auxiliary_files_committed': False, 'auxiliary_files_saved_in_archive': True,
    'files': file_records, 'archive': {'path': archive.name, 'sha256': sha(archive), 'bytes': archive.stat().st_size},
    'new_commits_created': False, 'remote_push_performed': False, 'production_mutation': False,
    'new_test_build_or_game_started': False, 'active_owned_processes': [],
    'breakpoint': 'MCEF API/Session2 preparation research; no new native QA driver written or started',
    'unsaved_items': [], 'push_owner': 'One release owner designated by parent; archive older review branches without reapplying old patches'}
(OUT/'PAUSED-SNAPSHOT.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'archive': record['archive'], 'bundles': bundles, 'auxiliary_file_count': len(file_records),
    'new_commits_created': False, 'remote_push_performed': False, 'unsaved_items': []}))
