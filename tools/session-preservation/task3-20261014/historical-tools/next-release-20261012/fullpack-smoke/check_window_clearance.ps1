$ErrorActionPreference = 'Stop'
$qaWorkspace = 'C:\Users\ranzh\Documents\Codex\2026-10-11\task-3'
$qaProcesses = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('java.exe','javaw.exe','jcef_helper.exe') } | Select-Object ProcessId,ParentProcessId,Name)
$qaTcp = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -eq 25847 | Select-Object LocalAddress,LocalPort,OwningProcess)
$qaUdp = @(Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object LocalPort -eq 25847 | Select-Object LocalAddress,LocalPort,OwningProcess)
$qaLocks = @()
foreach ($qaRun in @('r3','r5','r7','r8','r9-default12g')) {
 $qaLockPath = Join-Path $qaWorkspace "q\build\local-mc-debug\$qaRun\server\qa-world\session.lock"
 if (Test-Path -LiteralPath $qaLockPath) {
  $qaExclusive = $false
  try { $qaStream = [System.IO.File]::Open($qaLockPath,[System.IO.FileMode]::Open,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::None); $qaStream.Dispose(); $qaExclusive = $true } catch {}
  $qaLocks += [pscustomobject]@{run=$qaRun;path=$qaLockPath;exclusiveOpen=$qaExclusive}
 }
}
$qaEvidence = [ordered]@{utc=[DateTime]::UtcNow.ToString('o');javaOrJcef=$qaProcesses;port25847Listeners=@($qaTcp)+@($qaUdp);worldLocks=$qaLocks;productionMutation=$false}
$qaEvidence | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $qaWorkspace 'next-release-20261012\fullpack-smoke\window-clearance.json') -Encoding UTF8
$qaEvidence | ConvertTo-Json -Depth 6
