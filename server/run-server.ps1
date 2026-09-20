<#
.SYNOPSIS
    启动 BatterMC5Remake 官网和 API。

.EXAMPLE
    .\run-server.ps1
    .\run-server.ps1 -Port 8099 -Reload
#>
[CmdletBinding()]
param(
    [int]$Port = 8099,
    [string]$HostAddress = '0.0.0.0',
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $command = Get-Command py -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $command) { throw '找不到 Python 3，请先安装 Python 并执行 pip install -r server\requirements.txt' }
    $python = $command.Source
}

$env:PYTHONUTF8 = '1'
$argv = @('-m', 'app.cli', 'serve', '--host', $HostAddress, '--port', $Port)
if ($Reload) { $argv += '--reload' }

Write-Host "官网和 API  http://${HostAddress}:$Port" -ForegroundColor Cyan
Write-Host "下载文件直连 OSS，Python 不转发大文件" -ForegroundColor DarkGray
Push-Location $PSScriptRoot
try { & $python @argv }
finally { Pop-Location }
