"""Stop only the two stalled task-owned public runtime import probes."""
import base64
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
script=r'''
$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8
$root='C:\Users\ranzh\Documents\Codex\terminal-sso-dedicated-task4-20261002'
if((Get-Content -LiteralPath "$root\TASK4-OWNED.txt" -Raw) -ne 'task4 dedicated SSO synthetic QA only'){throw 'Ownership marker mismatch'}
$stopped=@()
foreach($id in @(3420,22428)){$p=Get-Process -Id $id -ErrorAction SilentlyContinue;if($p -and $p.Path -eq "$root\python\python.exe"){Stop-Process -Id $p.Id -Force;$stopped+=$p.Id}}
@{stoppedTaskOwnedProbeIds=$stopped;otherProcessMutation=$false;productionMutation=$false}|ConvertTo-Json -Compress
'''
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=8',
    'JBC-1@192.168.110.131','powershell','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],capture_output=True,timeout=25)
if p.returncode:raise SystemExit('Task-owned probe cleanup failed; no other process was targeted')
record=json.loads(p.stdout.decode('utf-8-sig'))
(ROOT/'dedicated-sso/131-probe-cleanup.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))
