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
# 版本号的唯一真相源是 client/tauri/package.json。Directory.Build.props 里的
# Version 是一条指向它的 MSBuild 表达式，按字面 XML 读回来的是表达式本身——
# 实测因此把 "$([System.Text...)" 当成版本号写进了 launcher-release.json，
# 一路带到发布脚本才被版本号格式校验拦下。
$clientVersion = [string](Get-Content -LiteralPath (Join-Path $workspaceRoot 'client\tauri\package.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
if ($clientVersion -notmatch '^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$') {
    throw "client/tauri/package.json 里的版本号不合法：$clientVersion"
}

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

    # 自绘标题栏左上角那个图标直接用应用图标本尊。前端只能读 sidecar\web\ 下的文件，
    # 所以从唯一真相 sidecar\icon.ico 拷一份过去，换图标永远只改那一个文件。
    Copy-Item -LiteralPath client\sidecar\icon.ico `
        -Destination client\sidecar\web\icon.ico -Force

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

    # sidecar 的版本必须和声明的版本一致，否则装出来的是"1.1.30 的安装包 + 1.1.29 的
    # sidecar"。这种事真出过一次：改了代码只跑 dotnet build、没跑这里的 publish，安装包里
    # 带的还是上一版 sidecar，而界面显示的是新版本号——玩家反馈"更新了但问题还在"，
    # 查了很久才发现是发版流程漏了一步。版本号靠 client/Directory.Build.props 里一条
    # MSBuild 表达式从 package.json 读出来，这里只负责确认那条链真的生效了。
    $sidecarVersion = (Get-Item -LiteralPath $backend).VersionInfo.ProductVersion
    if (-not $sidecarVersion) { throw 'sidecar 没有版本信息，无法核对' }
    $sidecarVersion = ($sidecarVersion -split '\+')[0].Trim()
    if ($sidecarVersion -ne $clientVersion) {
        throw "sidecar 版本 $sidecarVersion 和声明版本 $clientVersion 不一致；版本号唯一真相源是 client/tauri/package.json，检查 client/Directory.Build.props 的表达式"
    }
    Write-Host "  sidecar 版本核对通过：$sidecarVersion" -ForegroundColor DarkGray

    $tauriBinaries = Join-Path $workspaceRoot 'client\tauri\src-tauri\binaries'
    New-Item -ItemType Directory -Path $tauriBinaries -Force | Out-Null
    Copy-Item -LiteralPath $backend `
        -Destination (Join-Path $tauriBinaries 'battermc-backend-x86_64-pc-windows-msvc.exe') -Force

    if ($SelfTest) {
        Step '核心逻辑自检'
        & $backend --selftest
        if ($LASTEXITCODE -ne 0) { throw '自检失败' }
        node --test client/tests/client-update.test.cjs client/tests/pack-install.test.cjs
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

    $launcherExe = Join-Path $clientOut 'BMC [Remake].exe'
    $portableBackend = Join-Path $clientOut 'battermc-backend.exe'
    Copy-Item -LiteralPath (Join-Path $tauriTarget 'battermc5remake.exe') -Destination $launcherExe -Force
    Copy-Item -LiteralPath (Join-Path $tauriTarget 'battermc-backend.exe') -Destination $portableBackend -Force

    # msquic.dll 也要跟着走。安装包里它是 tauri 的 resource，装完就在主程序旁边；
    # 但 artifacts 下这两个便携副本没人管，缺了它 QUIC 加载不起来，诊断命令会
    # 报"QUIC 不可用"，看着像玩家环境的问题，其实是打包漏了。
    $msquic = Join-Path $tauriTarget 'msquic.dll'
    if (Test-Path -LiteralPath $msquic -PathType Leaf) {
        Copy-Item -LiteralPath $msquic -Destination (Join-Path $clientOut 'msquic.dll') -Force
    } else {
        throw 'Tauri 输出里没有 msquic.dll；QUIC 会不可用'
    }

    $builtInstaller = Get-ChildItem (Join-Path $tauriTarget 'bundle\nsis') -Filter '*-setup.exe' |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $installer = Join-Path $clientOut 'BMC [Remake] setup.exe'
    if ($builtInstaller) { Copy-Item -LiteralPath $builtInstaller.FullName -Destination $installer -Force }
    if (-not $builtInstaller) { throw '没有找到 Tauri NSIS 安装包' }

    $builtSignaturePath = $builtInstaller.FullName + '.sig'
    if (-not (Test-Path -LiteralPath $builtSignaturePath -PathType Leaf)) {
        throw '没有生成 Tauri updater 签名；检查 TAURI_SIGNING_PRIVATE_KEY 配置'
    }
    $signatureFile = Join-Path $clientOut 'BMC [Remake] setup.exe.sig'
    Copy-Item -LiteralPath $builtSignaturePath -Destination $signatureFile -Force
    $signature = (Get-Content -LiteralPath $builtSignaturePath -Raw -Encoding UTF8).Trim()

    # 产物名里有方括号（"BMC [Remake] setup.exe"），而方括号是 PowerShell 的通配符。
    # 不加 -LiteralPath 的话路径会被当成字符类去匹配，匹配不到就静默返回 null，
    # 然后在 .Hash 上炸成"不能对 Null 值表达式调用方法"。
    $sha = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = (Get-Item -LiteralPath $installer).Length
    $release = [ordered]@{
        version      = $clientVersion
        architecture = 'tauri-2-with-dotnet-sidecar'
        installer    = 'BMC [Remake] setup.exe'
        signatureFile = 'BMC [Remake] setup.exe.sig'
        signature    = $signature
        sha256       = $sha
        size         = $size
        pubDate      = [DateTimeOffset]::UtcNow.ToString('o')
    }
    $release | ConvertTo-Json | Set-Content (Join-Path $clientOut 'launcher-release.json') -Encoding utf8

    Write-Host ("  Tauri EXE  {0:N1} MB" -f ((Get-Item -LiteralPath $launcherExe).Length / 1MB)) -ForegroundColor Green
    Write-Host ("  .NET sidecar {0:N1} MB" -f ((Get-Item -LiteralPath $portableBackend).Length / 1MB)) -ForegroundColor Green
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
