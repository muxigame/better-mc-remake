<#
.SYNOPSIS
    构建 Tauri 客户端、.NET sidecar 和 Python 官网后端。

.EXAMPLE
    .\build.ps1                      # 构建客户端和更新服务器
    .\build.ps1 -Target client       # 只构建 Tauri 客户端
    .\build.ps1 -Target server       # 只构建更新服务器
    .\build.ps1 -SelfTest            # 构建后跑核心逻辑自检
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'client', 'server')]
    [string]$Target = 'all',

    [string]$OutDir,

    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutDir) { $OutDir = Join-Path $workspaceRoot 'artifacts' }
Set-Location $workspaceRoot
[xml]$clientBuildProps = Get-Content (Join-Path $workspaceRoot 'client\Directory.Build.props')
$clientVersion = [string]$clientBuildProps.Project.PropertyGroup.Version
if (-not $clientVersion) { throw 'client/Directory.Build.props 缺少 Version' }

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $pair = $trimmed.Split('=', 2)
        $name = $pair[0].Trim()
        $value = $pair[1].Trim()
        if ($value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

Import-DotEnv (Join-Path $workspaceRoot '.env')

if ($Target -in 'all', 'client') {
    if (-not $env:TAURI_SIGNING_PRIVATE_KEY -and $env:TAURI_SIGNING_PRIVATE_KEY_PATH) {
        $env:TAURI_SIGNING_PRIVATE_KEY = $env:TAURI_SIGNING_PRIVATE_KEY_PATH
    }
    if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
        throw '缺少 TAURI_SIGNING_PRIVATE_KEY / TAURI_SIGNING_PRIVATE_KEY_PATH，正式客户端必须生成签名 updater artifact'
    }

    Step '还原客户端 .NET 依赖'
    dotnet restore client\BatterMC.Client.sln
    if ($LASTEXITCODE -ne 0) { throw '客户端 .NET 依赖还原失败' }

    $cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
    if (Test-Path $cargoBin) { $env:Path = "$cargoBin;$env:Path" }
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        throw '找不到 Cargo。请先安装 Rust MSVC 工具链：https://tauri.app/start/prerequisites/'
    }

    Step '安装 Tauri 前端依赖'
    Push-Location client\tauri
    try { npm ci }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'npm ci 失败' }

    Step '构建 .NET 游戏引擎 sidecar（win-x64）'
    $backendOut = Join-Path $OutDir 'intermediate\sidecar'
    dotnet publish client\sidecar\BatterMC.Launcher.csproj `
        -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true --nologo -o $backendOut
    if ($LASTEXITCODE -ne 0) { throw 'sidecar 构建失败' }

    $backend = Join-Path $backendOut 'battermc-backend.exe'
    $tauriBinaries = Join-Path $workspaceRoot 'client\tauri\src-tauri\binaries'
    New-Item -ItemType Directory -Path $tauriBinaries -Force | Out-Null
    Copy-Item -LiteralPath $backend `
        -Destination (Join-Path $tauriBinaries 'battermc-backend-x86_64-pc-windows-msvc.exe') -Force

    if ($SelfTest) {
        Step '核心逻辑自检'
        & $backend --selftest
        if ($LASTEXITCODE -ne 0) { throw '自检失败' }
        node --test client/tests/client-update.test.cjs
        if ($LASTEXITCODE -ne 0) { throw 'Client update UI tests failed' }
    }

    Step '构建 Tauri 2 客户端和 NSIS 安装包'
    Push-Location client\tauri
    try { npm run tauri -- build }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Tauri 构建失败' }

    $tauriTarget = Join-Path $workspaceRoot 'client\tauri\src-tauri\target\release'
    $clientOut = Join-Path $OutDir 'client'
    New-Item -ItemType Directory -Path $clientOut -Force | Out-Null

    $launcherExe = Join-Path $clientOut 'BatterMC5Remake.exe'
    $portableBackend = Join-Path $clientOut 'battermc-backend.exe'
    Copy-Item -LiteralPath (Join-Path $tauriTarget 'battermc5remake.exe') -Destination $launcherExe -Force
    Copy-Item -LiteralPath (Join-Path $tauriTarget 'battermc-backend.exe') -Destination $portableBackend -Force

    $builtInstaller = Get-ChildItem (Join-Path $tauriTarget 'bundle\nsis') -Filter '*-setup.exe' |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $installer = Join-Path $clientOut 'BatterMC5Remake-setup.exe'
    if ($builtInstaller) { Copy-Item -LiteralPath $builtInstaller.FullName -Destination $installer -Force }
    if (-not $builtInstaller) { throw '没有找到 Tauri NSIS 安装包' }

    $builtSignaturePath = $builtInstaller.FullName + '.sig'
    if (-not (Test-Path -LiteralPath $builtSignaturePath -PathType Leaf)) {
        throw '没有生成 Tauri updater 签名；检查 TAURI_SIGNING_PRIVATE_KEY 配置'
    }
    $signatureFile = Join-Path $clientOut 'BatterMC5Remake-setup.exe.sig'
    Copy-Item -LiteralPath $builtSignaturePath -Destination $signatureFile -Force
    $signature = (Get-Content -LiteralPath $builtSignaturePath -Raw -Encoding UTF8).Trim()

    $sha = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = (Get-Item $installer).Length
    $release = [ordered]@{
        version      = $clientVersion
        architecture = 'tauri-2-with-dotnet-sidecar'
        installer    = 'BatterMC5Remake-setup.exe'
        signatureFile = 'BatterMC5Remake-setup.exe.sig'
        signature    = $signature
        sha256       = $sha
        size         = $size
        pubDate      = [DateTimeOffset]::UtcNow.ToString('o')
    }
    $release | ConvertTo-Json | Set-Content (Join-Path $clientOut 'launcher-release.json') -Encoding utf8

    Write-Host ("  Tauri EXE  {0:N1} MB" -f ((Get-Item $launcherExe).Length / 1MB)) -ForegroundColor Green
    Write-Host ("  .NET sidecar {0:N1} MB" -f ((Get-Item $portableBackend).Length / 1MB)) -ForegroundColor Green
    Write-Host ("  NSIS 安装包 {0:N1} MB" -f ($size / 1MB)) -ForegroundColor Green
    Write-Host "  Updater 签名已生成" -ForegroundColor Green
    Write-Host "  SHA-256 $sha" -ForegroundColor DarkGray

    Write-Host '  启动器构建完成；整合包内容由独立的 manifest/OSS 发布流程维护' -ForegroundColor Green
}

if ($Target -in 'all', 'server') {
    $python = Join-Path $workspaceRoot 'server\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        $command = Get-Command py -ErrorAction SilentlyContinue
        if (-not $command) { throw '找不到 Python 3' }
        $python = $command.Source
    }
    $env:PYTHONUTF8 = '1'

    Step '测试 Python 官网和发布工具'
    Push-Location server
    try { & $python -m unittest discover -s tests -v }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Python 服务端测试失败' }

    Step '组装 Python 官网后端'
    $serverOut = Join-Path $OutDir 'server'
    if (Test-Path -LiteralPath $serverOut) { Remove-Item -LiteralPath $serverOut -Recurse -Force }
    New-Item -ItemType Directory -Path $serverOut -Force | Out-Null
    $serverAppOut = Join-Path $serverOut 'app'
    New-Item -ItemType Directory -Path $serverAppOut -Force | Out-Null
    Get-ChildItem -LiteralPath server\app -Filter '*.py' -File |
        Copy-Item -Destination $serverAppOut -Force
    Copy-Item -LiteralPath server\web -Destination $serverOut -Recurse -Force
    Copy-Item -LiteralPath server\requirements.txt -Destination $serverOut -Force
    Copy-Item -LiteralPath server\site.json -Destination $serverOut -Force
    $publishedRelease = if (Test-Path -LiteralPath artifacts\client\launcher-release.json) {
        'artifacts\client\launcher-release.json'
    } else {
        'server\launcher-release.json'
    }
    Copy-Item -LiteralPath $publishedRelease -Destination $serverOut -Force
    Write-Host '  FastAPI 官网 + 控制面 API；manifest 与整合包内容由对象存储托管' -ForegroundColor Green
    Write-Host "  $serverOut\app\main.py" -ForegroundColor DarkGray
}

Step '完成'
Write-Host "输出目录 $OutDir"
