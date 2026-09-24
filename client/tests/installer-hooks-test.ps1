<#
.SYNOPSIS
    卸载钩子测试：勾了「同时删除游戏数据」才删、更新时绝不删、旧版本还登记着时不动兜底目录。

    只在临时目录和一个测试专用的 HKCU 键里动手，跑完清掉。不碰机器上真实的安装和玩家数据。
    需要 Tauri 构建时下载的 NSIS（%LOCALAPPDATA%\tauri\NSIS\makensis.exe）。
#>
$ErrorActionPreference = 'Stop'
$makensis = Join-Path $env:LOCALAPPDATA 'tauri\NSIS\makensis.exe'
if (-not (Test-Path -LiteralPath $makensis)) { throw "找不到 $makensis，先跑一次 Tauri 构建" }

$hooks = (Resolve-Path (Join-Path $PSScriptRoot '..\tauri\src-tauri\installer-hooks.nsh')).Path
$script = Join-Path $PSScriptRoot 'installer-hooks-test.nsi'
# 路径短一点：钩子里拼出来的游戏目录带方括号和空格，测试目录本身就别再添乱
$root = Join-Path $env:TEMP ('bmc-hooks-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
$inst = Join-Path $root 'inst'
$fallback = Join-Path $root 'fallback'
$legacyKey = 'Software\BmcInstallerHookTest\Legacy'
$failures = 0

function Check([string]$name, [bool]$ok) {
    if ($ok) { Write-Host "  [通过] $name" -ForegroundColor Green }
    else { Write-Host "  [失败] $name" -ForegroundColor Red; $script:failures++ }
}

function Build([string]$out, [string]$legacy) {
    & $makensis /V1 "/DHOOKS=$hooks" "/DOUTFILE=$out" "/DBMC_DATA_FALLBACK=$fallback" `
        "/DLEGACY_UNINSTKEY=$legacy" $script | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "makensis 失败：$out" }
}

function Arrange([switch]$Stray) {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
    foreach ($f in @('Better MC Remake [FORGE]\mods\a.jar', 'Better MC Remake [FORGE]\saves\w\level.dat',
                     'launcher\settings.json', 'runtime\java-21\bin\java.exe')) {
        $p = Join-Path $inst $f
        New-Item -ItemType Directory -Path (Split-Path $p) -Force | Out-Null
        Set-Content -LiteralPath $p -Value 'x'
    }
    New-Item -ItemType Directory -Path (Join-Path $fallback 'Better MC Remake [FORGE]') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $fallback 'Better MC Remake [FORGE]\x.txt') -Value 'x'
    if ($Stray) { Set-Content -LiteralPath (Join-Path $inst 'player-notes.txt') -Value 'mine' }
}

function Run([string]$exe, [string]$del, [string]$upd) {
    $p = Start-Process -FilePath $exe -ArgumentList "/DEL=$del", "/UPD=$upd", "/INST=$inst" -PassThru -Wait
    if ($p.ExitCode -ne 0) { throw "测试程序退出码 $($p.ExitCode)" }
}

$game = { Test-Path -LiteralPath (Join-Path $inst 'Better MC Remake [FORGE]') }
$data = { Test-Path -LiteralPath (Join-Path $inst 'launcher') }
$java = { Test-Path -LiteralPath (Join-Path $inst 'runtime') }
$fb = { Test-Path -LiteralPath $fallback }

try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $noLegacy = Join-Path $env:TEMP 'bmc-hooks-nolegacy.exe'
    $withLegacy = Join-Path $env:TEMP 'bmc-hooks-legacy.exe'
    Build $noLegacy 'Software\BmcInstallerHookTest\NoSuchKey'
    Build $withLegacy $legacyKey

    Write-Host '勾了删除：游戏、启动器数据、Java、兜底目录都删，安装目录空了也删'
    Arrange; Run $noLegacy 1 0
    Check '游戏目录删了' (-not (& $game))
    Check '启动器数据删了' (-not (& $data))
    Check 'Java 删了' (-not (& $java))
    Check '兜底目录删了' (-not (& $fb))
    Check '安装目录空了也删' (-not (Test-Path -LiteralPath $inst))

    Write-Host '没勾：一样都不动'
    Arrange; Run $noLegacy 0 0
    Check '全部保留' ((& $game) -and (& $data) -and (& $java) -and (& $fb))

    Write-Host '自动更新（/UPDATE）：就算变量是 1 也不删'
    Arrange; Run $noLegacy 1 1
    Check '全部保留' ((& $game) -and (& $data) -and (& $java) -and (& $fb))

    Write-Host '旧版本还登记在程序和功能里：兜底目录是它的，不动'
    New-Item -Path "HKCU:\$legacyKey" -Force | Out-Null
    New-ItemProperty -Path "HKCU:\$legacyKey" -Name 'UninstallString' -Value 'C:\legacy\uninstall.exe' -Force | Out-Null
    Arrange; Run $withLegacy 1 0
    Check '安装目录里的游戏照删' (-not (& $game))
    Check '兜底目录保留' (& $fb)

    Write-Host '安装目录里有玩家自己的文件：只删我们的，目录和那个文件留着'
    Arrange -Stray; Run $noLegacy 1 0
    Check '游戏目录删了' (-not (& $game))
    Check '玩家的文件还在' (Test-Path -LiteralPath (Join-Path $inst 'player-notes.txt'))

    Write-Host '游戏还开着（文件被占用）：删不掉的留着，程序不挂'
    Arrange
    $locked = [System.IO.File]::Open((Join-Path $inst 'Better MC Remake [FORGE]\mods\a.jar'), 'Open', 'Read', 'None')
    try { Run $noLegacy 1 0 } finally { $locked.Dispose() }
    Check '被占用的文件还在' (Test-Path -LiteralPath (Join-Path $inst 'Better MC Remake [FORGE]\mods\a.jar'))
    Check '其余照删' (-not (& $data))
}
finally {
    Remove-Item -Path 'HKCU:\Software\BmcInstallerHookTest' -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $env:TEMP 'bmc-hooks-nolegacy.exe'), (Join-Path $env:TEMP 'bmc-hooks-legacy.exe') -Force -ErrorAction SilentlyContinue
}

if ($failures -gt 0) { Write-Host "失败 $failures 项" -ForegroundColor Red; exit 1 }
Write-Host '全部通过' -ForegroundColor Green
