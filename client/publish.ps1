[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Bucket,
    [string]$Region,
    [string]$OutDir,
    [string]$ClientPrefix,
    [string]$PublicBaseUrl,
    [string]$PolicyPath,
    [string]$NotesPath
)

$ErrorActionPreference = 'Stop'

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        $p = $t.Split('=', 2)
        $name = $p[0].Trim(); $value = $p[1].Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

function ConvertTo-OssUrlPath([string]$Object) {
    # 对象名里现在有空格和方括号（"BMC [Remake] setup.exe"）。直接拼进 URL，
    # 空格会让多数 HTTP 客户端直接拒掉请求，方括号在路径里也是保留字符。
    # 逐段转义：斜杠是路径分隔符要留着，段内其余字符一律百分号编码。
    ($Object -split '/' | ForEach-Object { [System.Uri]::EscapeDataString($_) }) -join '/'
}

function Test-OssObjectExists([string]$OssUtil, [string]$Object, [object[]]$Common) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & $OssUtil stat $Object @Common *> $null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Upload-OssObject([string]$OssUtil, [string]$Source, [string]$Target, [string]$ContentType, [string]$CacheControl, [object[]]$Common) {
    Write-Host "上传 $Target" -ForegroundColor DarkCyan
    & $OssUtil cp $Source $Target -f --content-type $ContentType --cache-control $CacheControl @Common
    if ($LASTEXITCODE -ne 0) { throw "上传失败：$Target" }
}

$workspaceRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'release-policy.ps1')
Import-DotEnv (Join-Path $workspaceRoot '.env')
if (-not $OutDir) { $OutDir = Join-Path $workspaceRoot 'artifacts' }
if (-not $Bucket) { $Bucket = $env:OSS_BUCKET }
if (-not $Region) { $Region = $env:OSS_REGION }
if (-not $Bucket) { $Bucket = 'muxigame-prod-static-cn' }
if (-not $Region) { $Region = 'cn-hangzhou' }
if (-not $ClientPrefix) { $ClientPrefix = 'bmc/client/' }
$ClientPrefix = $ClientPrefix.Replace('\\','/').TrimStart('/')
if (-not $ClientPrefix.EndsWith('/')) { $ClientPrefix += '/' }
if (-not $PublicBaseUrl) { $PublicBaseUrl = "https://$Bucket.oss-$Region.aliyuncs.com" }
$PublicBaseUrl = $PublicBaseUrl.TrimEnd('/')

$installer = Join-Path $OutDir 'client\BMC [Remake] setup.exe'
$signatureFile = Join-Path $OutDir 'client\BMC [Remake] setup.exe.sig'
$metadata = Join-Path $OutDir 'client\launcher-release.json'
foreach ($path in @($installer,$signatureFile,$metadata)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "发布文件不存在：$path" }
}

$meta = Get-Content -LiteralPath $metadata -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$meta.version
if ($version -notmatch '^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$') { throw "客户端版本号不合法：$version" }

# 发布时再做一遍“源码版本 → sidecar → Tauri 主程序 → release metadata”的闭环校验。
# build.ps1 已经会检查一次，但发布脚本必须能独立挡住“手工 npm build / 复用旧 artifacts”
# 这类错误。历史上出现过安装包提示更新成功，重启后界面仍显示旧版本，根因就是
# NSIS 包里的 sidecar 还是上一版。
$packageJson = Join-Path $workspaceRoot 'client\tauri\package.json'
$sourceVersion = [string](Get-Content -LiteralPath $packageJson -Raw -Encoding UTF8 | ConvertFrom-Json).version
if ($sourceVersion -ne $version) {
    throw "发布产物版本 $version 与源码版本 $sourceVersion 不一致；请重新执行 scripts\build.ps1 -Target client -SelfTest"
}
function Get-ArtifactVersion([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "缺少版本核对产物：$Path" }
    $value = (Get-Item -LiteralPath $Path).VersionInfo.ProductVersion
    if (-not $value) { throw "产物没有 ProductVersion：$Path" }
    return (($value -split '\+')[0]).Trim()
}
$sidecarVersion = Get-ArtifactVersion (Join-Path $OutDir 'client\battermc-backend.exe')
$tauriVersion = Get-ArtifactVersion (Join-Path $OutDir 'client\BMC [Remake].exe')
if ($sidecarVersion -ne $version) {
    throw "sidecar 版本 $sidecarVersion 与发布版本 $version 不一致；拒绝发布旧 sidecar"
}
if ($tauriVersion -ne $version) {
    throw "Tauri 主程序版本 $tauriVersion 与发布版本 $version 不一致；拒绝发布"
}

# 方括号是 PowerShell 通配符，产物名里有，必须走 -LiteralPath
$sha = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sha -ne [string]$meta.sha256) { throw "安装包 SHA-256 与 launcher-release.json 不一致" }
$sig = (Get-Content -LiteralPath $signatureFile -Raw -Encoding UTF8).Trim()
if (-not $sig -or $sig -ne [string]$meta.signature) { throw "Updater 签名与 launcher-release.json 不一致" }

$ossutil = Get-Command ossutil -ErrorAction SilentlyContinue
if ($ossutil) { $oss = $ossutil.Path } else {
    $oss = Join-Path $env:LOCALAPPDATA 'Programs\ossutil\ossutil-2.4.0-windows-amd64\ossutil.exe'
    if (-not (Test-Path -LiteralPath $oss)) { throw "找不到 ossutil 2.x" }
}
$common = @(); if ($Region) { $common += @('--region', $Region) }

$releasePrefix = "${ClientPrefix}releases/$version/"
$installerObject = $releasePrefix + 'BMC [Remake] setup.exe'
$signatureObject = $releasePrefix + 'BMC [Remake] setup.exe.sig'
$releaseObject = $releasePrefix + 'release.json'
$latestObject = $ClientPrefix + 'latest/metadata.json'
$installerTarget = "oss://$Bucket/$installerObject"
$signatureTarget = "oss://$Bucket/$signatureObject"
$releaseTarget = "oss://$Bucket/$releaseObject"
$latestTarget = "oss://$Bucket/$latestObject"

$historyDir = Join-Path $PSScriptRoot 'releases'
$historyFile = Join-Path $historyDir "$version.json"
if (Test-Path -LiteralPath $historyFile) {
    throw "Git 中已经存在客户端版本 $version 的发布记录，禁止覆盖同版本：$historyFile"
}
foreach ($target in @($installerTarget,$signatureTarget,$releaseTarget)) {
    if (Test-OssObjectExists $oss $target $common) {
        throw "OSS 已存在客户端版本 $version 的发布对象，禁止覆盖同版本：$target"
    }
}

$published = [ordered]@{
    version = $version
    architecture = [string]$meta.architecture
    installer = 'BMC [Remake] setup.exe'
    signatureFile = 'BMC [Remake] setup.exe.sig'
    sha256 = $sha
    signature = $sig
    size = [int64]$meta.size
    pubDate = [string]$meta.pubDate
    updaterCompatible = $true
    ossObject = $installerObject
    signatureObject = $signatureObject
    releaseObject = $releaseObject
    url = "$PublicBaseUrl/$(ConvertTo-OssUrlPath $installerObject)"
    signatureUrl = "$PublicBaseUrl/$(ConvertTo-OssUrlPath $signatureObject)"
}
if (-not $NotesPath) { $NotesPath = Join-Path $PSScriptRoot 'release-notes.md' }
if (Test-Path -LiteralPath $NotesPath -PathType Leaf) {
    $published.notes = (Get-Content -LiteralPath $NotesPath -Raw -Encoding UTF8).Trim()
}
$publishedPath = Join-Path $OutDir "client\release-$version.json"
[IO.File]::WriteAllText($publishedPath, ($published | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
Merge-ClientUpdatePolicy $workspaceRoot $publishedPath $PolicyPath $publishedPath

Write-Host "客户端 $version -> $releasePrefix" -ForegroundColor Cyan
Write-Host "  历史版本目录不可覆盖；latest 只更新 metadata 指针" -ForegroundColor DarkGray
if (-not $PSCmdlet.ShouldProcess($releasePrefix, "发布签名客户端 $version")) { return }

Upload-OssObject $oss $installer $installerTarget 'application/vnd.microsoft.portable-executable' 'public, max-age=31536000, immutable' $common
Upload-OssObject $oss $signatureFile $signatureTarget 'text/plain; charset=utf-8' 'public, max-age=31536000, immutable' $common
Upload-OssObject $oss $publishedPath $releaseTarget 'application/json; charset=utf-8' 'public, max-age=31536000, immutable' $common
# latest 只是一份可回滚的小元数据指针，不复制二进制。
Upload-OssObject $oss $publishedPath $latestTarget 'application/json; charset=utf-8' 'no-cache, no-store, must-revalidate' $common

New-Item -ItemType Directory -Force -Path $historyDir | Out-Null
Copy-Item -LiteralPath $publishedPath -Destination $historyFile -Force
Copy-Item -LiteralPath $publishedPath -Destination (Join-Path $workspaceRoot 'server\launcher-release.json') -Force
Write-Host "发布完成：$version" -ForegroundColor Green
Write-Host "  Git 历史：$historyFile" -ForegroundColor DarkGray
Write-Host "  Server 当前版本快照：server\launcher-release.json" -ForegroundColor DarkGray

