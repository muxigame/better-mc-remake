<#
.SYNOPSIS
    构建 Tauri 客户端、.NET sidecar 和 Python 官网后端。

.DESCRIPTION
    Windows 代码签名（Authenticode）在 .env 里配了才做，没配照常构建但会警告：

      WINDOWS_SIGN_CERT_THUMBPRINT  证书指纹。证书要在本机「当前用户\个人」证书库里——
                                    USB 令牌插上、装好厂商驱动就会出现在那儿
      WINDOWS_SIGN_COMMAND          或者：自定义签名命令，%1 代表文件路径（云签名服务的命令行）
      WINDOWS_SIGN_TIMESTAMP_URL    时间戳服务，默认 DigiCert。证书过期后签名仍然有效靠它

    没签名的安装包，玩家下载运行时 Windows 显示「未知发布者」。

.EXAMPLE
    .\build.ps1                      # 构建客户端和更新服务器
    .\build.ps1 -Target client       # 只构建 Tauri 客户端
    .\build.ps1 -Target server       # 只构建更新服务器
    .\build.ps1 -SelfTest            # 构建后跑核心逻辑自检
    .\build.ps1 -AllowUntrustedSigning  # 用自签测试证书验证签名流程（证书不受信任也放行）
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'client', 'server')]
    [string]$Target = 'all',

    [string]$OutDir,

    [switch]$SelfTest,

    [switch]$AllowUntrustedSigning
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

# ── Windows 代码签名 ──
#
# 签了以后「未知发布者」变成证书上的名字。SmartScreen「已保护你的电脑」看的是信誉：
# 签了名，信誉按证书累积、跨版本延续；不签，信誉按文件哈希算，每发一版从零开始。
# 新证书头一段时间仍可能被拦，这点 EV 也一样（2024 年起 EV 不再自带信誉）。
#
# 必须交给 Tauri 在打包过程中签，不能等安装包出来再签：updater 的 .sig 是对最终
# 安装包算的，事后再签名会改动文件，玩家自动更新时校验对不上。Tauri 会签主程序、
# sidecar、NSIS 插件、安装包，卸载程序在安装时由 !uninstfinalize 签。
$signThumbprint = ($env:WINDOWS_SIGN_CERT_THUMBPRINT -replace '\s', '').ToUpperInvariant()
$signCommand = $env:WINDOWS_SIGN_COMMAND
$signTimestamp = if ($env:WINDOWS_SIGN_TIMESTAMP_URL) { $env:WINDOWS_SIGN_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }
$signing = [bool]($signThumbprint -or $signCommand)

function Find-SignTool {
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $tool = Get-ChildItem -LiteralPath $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $tool) { throw '找不到 signtool.exe；安装 Windows SDK（Visual Studio 安装器里勾「Windows 11 SDK」）' }
    $tool.FullName
}

function Invoke-CodeSign([string]$Path) {
    if ($signCommand) {
        $command = $signCommand.Replace('%1', "`"$Path`"")
        cmd /c $command
    } else {
        & (Find-SignTool) sign /fd sha256 /sha1 $signThumbprint /tr $signTimestamp /td sha256 /d 'BMC [Remake]' $Path
    }
    if ($LASTEXITCODE -ne 0) { throw "签名失败：$Path" }
}

function Assert-CodeSigned([string]$Path) {
    $sig = Get-AuthenticodeSignature -LiteralPath $Path
    $ok = $sig.Status -eq 'Valid' -or ($AllowUntrustedSigning -and $sig.SignerCertificate)
    if (-not $ok) { throw "签名核对没过：$Path（$($sig.Status) $($sig.StatusMessage)）" }
    Write-Host ("  已签名 {0}  {1}" -f (Split-Path $Path -Leaf), $sig.SignerCertificate.Subject) -ForegroundColor DarkGray
}

if ($Target -in 'all', 'client') {
    if ($signing) {
        if ($signThumbprint -and -not $signCommand) {
            # signtool /sha1 只找「当前用户\个人」，这里也只认那儿
            $cert = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
                Where-Object { $_.Thumbprint -eq $signThumbprint } | Select-Object -First 1
            if (-not $cert) { throw "「当前用户\个人」证书库里没有指纹为 $signThumbprint 的证书（USB 令牌插上了吗？）" }
            if (-not $cert.HasPrivateKey) { throw "证书 $($cert.Subject) 没有私钥，签不了名" }
            if ($cert.NotAfter -lt (Get-Date).AddDays(30)) {
                Write-Warning "代码签名证书 $($cert.NotAfter.ToString('yyyy-MM-dd')) 到期，记得续"
            }
            Write-Host "  代码签名：$($cert.Subject)" -ForegroundColor DarkGray
        } else {
            Write-Host '  代码签名：WINDOWS_SIGN_COMMAND' -ForegroundColor DarkGray
        }
    } else {
        Write-Warning '没有配置代码签名（WINDOWS_SIGN_CERT_THUMBPRINT / WINDOWS_SIGN_COMMAND）：玩家下载运行安装包时会看到「未知发布者」'
    }
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
    $tauriArgs = @('run', 'tauri', '--', 'build')
    if ($signing) {
        $windows = if ($signCommand) { @{ signCommand = $signCommand } } else {
            @{ certificateThumbprint = $signThumbprint; digestAlgorithm = 'sha256'; timestampUrl = $signTimestamp; tsp = $true }
        }
        $signConfig = Join-Path $OutDir 'intermediate\tauri-signing.json'
        New-Item -ItemType Directory -Path (Split-Path $signConfig) -Force | Out-Null
        @{ bundle = @{ windows = $windows } } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $signConfig -Encoding utf8
        $tauriArgs += @('--config', $signConfig)
    }
    Push-Location client\tauri
    try { npm @tauriArgs }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Tauri 构建失败' }

    if ($SelfTest) {
        Step '卸载钩子测试'
        & (Join-Path $workspaceRoot 'client\tests\installer-hooks-test.ps1')
        if ($LASTEXITCODE -ne 0) { throw '卸载钩子测试失败' }
    }

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

    if ($signing) {
        # 安装包和装进去的文件由 Tauri 签过了；target\release 里留下的那份主程序没签，
        # 上面拷出来的便携副本在这里补签。安装包本身绝不能再动（updater .sig 已经算好了）
        Assert-CodeSigned $installer
        foreach ($portable in @($launcherExe, $portableBackend)) {
            if ((Get-AuthenticodeSignature -LiteralPath $portable).Status -eq 'NotSigned') { Invoke-CodeSign $portable }
            Assert-CodeSigned $portable
        }
    }

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
    # 默认皮肤 steve.png 这类资源文件。skins.py 启动时就要读，缺了控制面起不来
    Copy-Item -LiteralPath server\app\assets -Destination $serverAppOut -Recurse -Force
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
