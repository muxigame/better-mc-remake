import base64
import json
from pathlib import Path
import subprocess
script=r'''
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$root='C:\Users\ranzh\Documents\Codex\terminal-sso-dedicated-task4-20261002'
$out=@{ownerMarker=(Test-Path -LiteralPath "$root\TASK4-OWNED.txt");pythonExe=(Test-Path -LiteralPath "$root\python\python.exe");runtimeFiles=0;latestWriteUtc=$null;sourceHarness=(Test-Path -LiteralPath "$root\tests\run_dedicated_sso_chain.py")}
if(Test-Path -LiteralPath "$root\python"){$files=@(Get-ChildItem -LiteralPath "$root\python" -Recurse -File);$out.runtimeFiles=$files.Count;$out.latestWriteUtc=($files|Sort-Object LastWriteTimeUtc -Descending|Select-Object -First 1).LastWriteTimeUtc.ToString('o')}
$out.ownedPython=@(Get-Process -Name python -ErrorAction SilentlyContinue|Where-Object {$_.Path -eq "$root\python\python.exe"}|Select-Object Id,Path)
$out.sourceGuardPresent=(Get-Content -LiteralPath "$root\repos\muxi-game-core-dedicated-sso\src\main\java\net\muxigame\core\feature\login\TerminalPassportServer.java" -Raw).Contains('System.nanoTime()-now>=30_000_000_000L')
$out|ConvertTo-Json -Compress
'''
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=8',
    'JBC-1@192.168.110.131','powershell','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],capture_output=True,timeout=30)
if p.returncode:raise SystemExit('131 task-stage metadata read failed')
data=json.loads(p.stdout.decode('utf-8-sig'));print(json.dumps(data))
(Path(__file__).resolve().parents[1]/'dedicated-sso/131-stage-progress.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
