# Shared by publish/promote. Only metadata changes; signing keys are never read here.
function Merge-ClientUpdatePolicy([string]$WorkspaceRoot, [string]$ReleasePath, [string]$PolicyPath, [string]$OutputPath) {
    if (-not $PolicyPath) { $PolicyPath = Join-Path $WorkspaceRoot 'client\update-policy.json' }
    if (-not (Test-Path -LiteralPath $PolicyPath -PathType Leaf)) { throw "Update policy missing: $PolicyPath" }
    $python = $env:BMC_PYTHON
    if (-not $python) { $python = Join-Path $WorkspaceRoot '..\.ops-venv\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
    $script = Join-Path $WorkspaceRoot 'client\apply-update-policy.py'
    & $python $script --release $ReleasePath --policy $PolicyPath --out $OutputPath
    if ($LASTEXITCODE -ne 0) { throw 'Invalid client support policy. Release pointer was not changed.' }
}
