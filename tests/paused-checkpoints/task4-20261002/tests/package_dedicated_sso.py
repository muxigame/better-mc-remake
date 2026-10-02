"""Make reviewed owner-only source patches and current Core artifact; never publish."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'dedicated-sso'
REPOS={
 'muxi-auth-dedicated-sso':('ff85de6cb62930ea6acfc798434b23ef45dd9336',[
     '.env.example','README.md','app/config.py','app/main.py','app/terminal_server_auth.py',
     'tests/test_terminal_sso.py','tests/test_terminal_dedicated_auth.py']),
 'muxi-game-core-dedicated-sso':('37a5156f7e71d46316cb91d7b592f436d5e1c69e',[
     'README.md','src/main/java/net/muxigame/core/config/CoreConfig.java',
     'src/main/java/net/muxigame/core/feature/login/TerminalPassportServer.java',
     'tests/run_terminal_sso_trust.py','tests/terminal-sso-trust/TerminalTrustHarness.java'])}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(repo,*args,**kw):return subprocess.check_output(['git','-c',f'core.excludesFile={ROOT/"evidence/empty-git-ignore"}',
    '-C',str(repo),*args],**kw)
assert 'Ran 99 tests' in (OUT/'auth-tests.log').read_text(encoding='utf-8')
assert (OUT/'auth-tests.log').read_text(encoding='utf-8').rstrip().endswith('OK')
trust=json.loads((OUT/'core-trust-131.json').read_text());chain=json.loads((OUT/'https-chain-131.json').read_text())
assert trust['status']==chain['status']=='passed' and trust['assertions']==61 and chain['assertions']==52
source=ROOT/'repos/muxi-game-core-dedicated-sso/src/main/java/net/muxigame/core/feature/login/TerminalPassportServer.java'
assert sha(source)==trust['source_sha256']['core']==chain['source_sha256']['TerminalPassportServer.java']
report={'created_utc':datetime.now(timezone.utc).isoformat(),'repositories':{},'validation':{
    'auth_tests':99,'core_existing_assertions':6935,'core_connection_assertions_on_131':61,
    'actual_https_functional_checks':52,'core_computer':chain['core_computer'],
    'auth_website_location':'008 temporary loopback services; loopback-only SSH forward to 131 Core',
    'two_synthetic_accounts_independently_authenticated':True,'minecraft_listeners':'explicit fixtures',
    'real_minecraft_mcef_or_production_player_acceptance':False},'production_mutation':False,
    'production_keys_read_or_transferred':False,'pushed_or_deployed':False,'files':{}}
for name,(base,paths) in REPOS.items():
    repo=ROOT/'repos'/name
    assert git(repo,'rev-parse','HEAD').decode().strip()==base or git(repo,'merge-base',base,'HEAD').decode().strip()==base
    git(repo,'add','--',*paths)
    changed=git(repo,'diff',base,'--name-only').decode().splitlines()
    assert set(changed)==set(paths),(name,changed)
    patch=OUT/(name.replace('-dedicated-sso','')+'-dedicated-terminal-sso.patch')
    patch.write_bytes(git(repo,'diff','--binary','--full-index',base,'--',*paths))
    with tempfile.TemporaryDirectory(prefix='dedicated-sso-index-') as directory:
        env=os.environ.copy();env['GIT_INDEX_FILE']=str(Path(directory)/'index')
        git(repo,'read-tree',base,env=env);git(repo,'apply','--cached','--check',str(patch),env=env)
    preserved=0
    for relative in git(repo,'ls-tree','-r','--name-only',base).decode().splitlines():
        if relative in paths:continue
        before=git(repo,'show',base+':'+relative)
        after=(repo/relative).read_bytes()
        assert before.replace(b'\r\n',b'\n')==after.replace(b'\r\n',b'\n'),relative
        preserved+=1
    if git(repo,'diff','--cached','--name-only').strip():
        git(repo,'-c','user.name=SSO Review','-c','user.email=sso-review@localhost','commit','-m',
            'fix: isolate terminal SSO authority and bind social trust to verified connection')
    assert not git(repo,'status','--porcelain').strip()
    report['repositories'][name]={'baseline':base,'review_commit':git(repo,'rev-parse','HEAD').decode().strip(),
        'owned_files':paths,'unchanged_tracked_files_verified':preserved,'patch':patch.name,
        'patch_sha256':sha(patch),'fresh_baseline_apply_check':'passed','working_tree_clean':True}
buildlog=(OUT/'core-build.log').read_text(encoding='utf-8')
assert 'Game Core Java self-tests: 6935 passed' in buildlog
build=json.loads(next(x for x in reversed(buildlog.splitlines()) if x.startswith('{')))
jar=ROOT/'repos/muxi-game-core-dedicated-sso/build/libs'/build['artifact']
assert sha(jar)==build['sha256']
destination=OUT/'muxi-game-core-dedicated-sso-review.jar';shutil.copyfile(jar,destination)
report['core_review_artifact']={**build,'delivery':destination.name,'review_only':True,'unique_release_version_required':True}
framework=ROOT/'repos/muxi-minigames/build/libs/muxi-minigames-0.1.2.jar'
report['compile_framework']={'sha256':sha(framework),'purpose_apis':['admitTerminal','socialUid','revoke'],
    'isolated_compile_alias':'muxi-minigames-0.1.2.jar','actual_internal_version':'0.1.3-task14-social-qa.1',
    'alias_is_not_a_release_dependency':True,'task2_frozen_trust_source_sha256':sha(OUT/'inputs/TrustedAccounts.java')}
for path in sorted(OUT.iterdir()):
    if path.is_file() and path.suffix in ('.patch','.jar','.json','.log','.md') and path.name not in ('VERIFICATION.json','python-test-runtime.json'):
        report['files'][path.name]={'sha256':sha(path),'bytes':path.stat().st_size}
(OUT/'VERIFICATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'core_review_jar_sha256':build['sha256'],'repositories':report['repositories'],
    'validation':report['validation'],'pushed_or_deployed':False}))
