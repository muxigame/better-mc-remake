"""Package only the SSO adaptation after the delivered container patch."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT/'repos/muxi-terminal-container'
BASE='a2db7539aca95ca82f40e24999c810d0a993ae4b'
FILE='src/main/java/net/muxigame/terminal/client/TerminalPassportNavigation.java'
PATCH=ROOT/'artifacts/terminal-sso-container-adapter.patch'
def git(*args,**kw):
    return subprocess.check_output(['git','-c',f'core.excludesFile={ROOT/"evidence/empty-git-ignore"}','-C',str(REPO),*args],**kw)

changed=git('diff',BASE,'--name-only').decode().splitlines()
assert changed==[FILE],changed
PATCH.write_bytes(git('diff','--binary',BASE,'--',FILE))
with tempfile.TemporaryDirectory(prefix='sso-adapter-index-') as temp:
    env=os.environ.copy()
    env['GIT_INDEX_FILE']=str(Path(temp)/'index')
    git('read-tree',BASE,env=env)
    git('apply','--cached','--check',str(PATCH),env=env)
if git('status','--porcelain').strip():
    git('add','--',FILE)
    git('-c','user.name=SSO Review','-c','user.email=sso-review@localhost',
        'commit','-m','fix: bind terminal SSO handoff to independent account view')
assert not git('status','--porcelain').strip()
build=json.loads((ROOT/'evidence/terminal-container-adapter-build.log').read_text())
jar=ROOT/'artifacts/muxi-terminal-0.2.1-container-sso-review.jar'
assert hashlib.sha256(jar.read_bytes()).hexdigest()==build['sha256']
assert '84 passed' in (ROOT/'evidence/navigation-adapter-tests.log').read_text()
record={
    'published_terminal_baseline':'c674e73656b7e7b51f507d21a2afed5fc276ab4b',
    'container_input_patch_sha256':'1b3ffcf20cf20a054fe08ece705bd380073a2ac6e7c9cd3bf9d45344b7a03556',
    'container_review_base':BASE,
    'adapter_review_commit':git('rev-parse','HEAD').decode().strip(),
    'owned_files':[FILE],
    'patch':PATCH.relative_to(ROOT).as_posix(),
    'patch_sha256':hashlib.sha256(PATCH.read_bytes()).hexdigest(),
    'fresh_container_base_apply_check':'passed',
    'deterministic_java_assertions':84,
    'actual_minecraft_neoforge_mcef_compilation':'passed',
    'combined_native_mcef_child_sso_runtime':'not run; parent merged integration QA required',
    'artifact':jar.relative_to(ROOT).as_posix(),
    'artifact_sha256':build['sha256'],
    'artifact_size':jar.stat().st_size,
    'artifact_version':'0.2.1 review only; unique release version required after integration',
    'container_animation_and_four_regression_files_unchanged':True,
    'working_tree_clean':True,
    'original_terminal_checkout_changed':False,
    'published_or_deployed':False}
(ROOT/'evidence/terminal-container-adapter.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))
