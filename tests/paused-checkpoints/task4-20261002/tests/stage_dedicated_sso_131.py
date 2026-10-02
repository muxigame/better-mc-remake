"""Stage only synthetic QA source to the explicitly requested 131 lab.

Does not install software, modify game files/config/permissions, read credentials,
or create/start/stop a game server/client. Strict host checking stays enabled.
"""
import base64
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DEST='C:/Users/ranzh/Documents/Codex/terminal-sso-dedicated-task4-20261002'
HOST='JBC-1@192.168.110.131'
FLAGS=['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=8']
parser=argparse.ArgumentParser();parser.add_argument('--source-only',action='store_true',help='Compatibility option: staging is always source-only');args=parser.parse_args()
def remote(script,timeout=60):
    encoded=base64.b64encode(script.encode('utf-16le')).decode()
    p=subprocess.run(['ssh',*FLAGS,HOST,'powershell','-NoProfile','-NonInteractive','-EncodedCommand',encoded],capture_output=True,timeout=timeout)
    if p.returncode:
        (ROOT/'dedicated-sso/131-stage-error.log').write_bytes(p.stdout+p.stderr)
        raise RuntimeError('131 task-owned stage failed; diagnostic saved locally')
    return p.stdout.decode('utf-8-sig')

target=ROOT/'dedicated-sso/synthetic-qa-source.zip'
with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for name in ('muxi-auth-dedicated-sso','muxi-game-core-dedicated-sso','better-mc-remake-dedicated-sso-fixture'):
        repo=ROOT/'repos'/name
        paths=subprocess.check_output(['git','-c',f'core.excludesFile={os.devnull}','-C',str(repo),
            'ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0')
        for relative in sorted(set(paths)-{''}):
            if name.startswith('better-mc') and not relative.startswith('server/'):continue
            p=repo/relative
            assert '.git' not in Path(relative).parts and Path(relative).name!='.env'
            assert p.suffix.lower() not in ('.db','.sqlite','.pem','.key','.pfx','.exe','.jar')
            if p.is_file():z.write(p,'repos/'+name+'/'+relative)
    for name in ('run_dedicated_sso_chain.py','run_core_trust_131.py'):
        z.write(ROOT/'tests'/name,'tests/'+name)
    z.write(ROOT/'dedicated-sso/inputs/TrustedAccounts.java','dedicated-sso/inputs/TrustedAccounts.java')
with zipfile.ZipFile(target) as z:assert z.testzip() is None
source_hash=hashlib.sha256(target.read_bytes()).hexdigest()
created=remote(r'''
$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8
$parent=Join-Path $env:USERPROFILE 'Documents\Codex'
$root=Join-Path $parent 'terminal-sso-dedicated-task4-20261002'
if([IO.Path]::GetFullPath($root) -ne 'C:\Users\ranzh\Documents\Codex\terminal-sso-dedicated-task4-20261002'){throw 'Unexpected task-owned root'}
if(Test-Path -LiteralPath $root){if(!(Test-Path -LiteralPath "$root\TASK4-OWNED.txt") -or [IO.File]::ReadAllText("$root\TASK4-OWNED.txt") -ne 'task4 dedicated SSO synthetic QA only'){throw 'Existing unowned directory; refusing overwrite'}}else{New-Item -ItemType Directory -Path $root|Out-Null}
[IO.File]::WriteAllText("$root\TASK4-OWNED.txt",'task4 dedicated SSO synthetic QA only')
@{root=$root;productionMutation=$false}|ConvertTo-Json -Compress
''')
p=subprocess.run(['scp',*FLAGS,str(target),HOST+':'+DEST+'/synthetic-qa-source.zip'],capture_output=True,timeout=60)
if p.returncode:raise RuntimeError('131 owned QA archive copy failed; no test was started')
prepared=remote(r'''
$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8
$root='C:\Users\ranzh\Documents\Codex\terminal-sso-dedicated-task4-20261002'
if(!(Test-Path -LiteralPath "$root\TASK4-OWNED.txt") -or [IO.File]::ReadAllText("$root\TASK4-OWNED.txt") -ne 'task4 dedicated SSO synthetic QA only'){throw 'Ownership marker absent or mismatched'}
if((Get-FileHash -LiteralPath "$root\synthetic-qa-source.zip" -Algorithm SHA256).Hash.ToLowerInvariant() -ne '__SOURCE__'){throw 'Source transfer mismatch'}
Expand-Archive -LiteralPath "$root\synthetic-qa-source.zip" -DestinationPath $root -Force
New-Item -ItemType Directory -Force -Path "$root\dedicated-sso","$root\scratch"|Out-Null
@{sourceReady=$true;portableApplicationRuntime='not required or staged';javaTestDriver='existing C:\Python38\python.exe';productionMutation=$false}|ConvertTo-Json -Compress
'''.replace('__SOURCE__',source_hash),timeout=60)
record={'host':HOST,'owned_root':DEST,'source_zip_sha256':source_hash,
        'read_or_transferred_production_credentials':False,'production_mutation':False,'system_installation':False,
        'runtime_probe':prepared.strip()}
(ROOT/'dedicated-sso/131-stage.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))
