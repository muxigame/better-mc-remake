"""Read-only probe using the existing approved 131 SSH route."""
import base64
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
script=r'''
$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8
$out=@{computer=$env:COMPUTERNAME;user=$env:USERNAME;readOnly=$true;commands=@();jdks=@();pythonModules=@()}
foreach($name in @('python','java','javac')){$c=Get-Command $name -ErrorAction SilentlyContinue;if($c){$out.commands+=@{name=$name;path=$c.Source}}}
foreach($parent in @('C:\Program Files\Java',"$env:USERPROFILE\.jdks","$env:USERPROFILE\Documents\Codex\terminal-integration-task6-20261001\tools\jdk")){if(Test-Path -LiteralPath $parent){foreach($d in @(Get-ChildItem -LiteralPath $parent -Directory)){$p=Join-Path $d.FullName 'bin\javac.exe';if(Test-Path -LiteralPath $p){$out.jdks+=$p}}}}
$pythonProbe='import importlib.util,json,sys;print(json.dumps({"version":sys.version,"modules":{n:importlib.util.find_spec(n) is not None for n in ["fastapi","uvicorn","httpx","cryptography","pydantic","multipart","email_validator","argon2","jwt"]}}))'
foreach($p in @('C:\Python312\python.exe','C:\Python38\python.exe')){if(Test-Path -LiteralPath $p){$modules=$pythonProbe|& $p -;if($LASTEXITCODE -ne 0){throw 'Python read-only module probe failed'};$out.pythonModules+=@{path=$p;info=($modules|ConvertFrom-Json)}}}
$out|ConvertTo-Json -Depth 8 -Compress
'''
encoded=base64.b64encode(script.encode('utf-16le')).decode()
flags=['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=8']
p=subprocess.run(['ssh',*flags,'JBC-1@192.168.110.131','powershell','-NoProfile','-NonInteractive','-EncodedCommand',encoded],capture_output=True,timeout=45)
if p.returncode:
    (ROOT/'dedicated-sso/131-probe-error.log').write_bytes(p.stdout+p.stderr)
    raise SystemExit('131 read-only probe failed; local error record saved')
data=json.loads(p.stdout.decode('utf-8-sig'))
(ROOT/'dedicated-sso/131-environment.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
print(json.dumps(data))
