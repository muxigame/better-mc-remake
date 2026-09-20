<#
.SYNOPSIS
    将 BatterMC 客户端安装包、清单和整合包文件发布到阿里云 OSS。

.EXAMPLE
    .\publish-oss.ps1

.EXAMPLE
    .\publish-oss.ps1 -Region cn-hangzhou
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Bucket,
    [string]$Prefix,
    [string]$Region,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed.StartsWith('export ')) { $trimmed = $trimmed.Substring(7).Trim() }

        $pair = $trimmed.Split('=', 2)
        if ($pair.Count -ne 2) { throw "无效的 .env 行：$line" }
        $name = $pair[0].Trim()
        $value = $pair[1].Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "无效的 .env 变量名：$name" }
        if ($value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

$workspaceRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutDir) { $OutDir = Join-Path $workspaceRoot 'artifacts' }
Import-DotEnv (Join-Path $workspaceRoot '.env')

if (-not $Bucket) { $Bucket = $env:OSS_BUCKET }
if (-not $Prefix) { $Prefix = $env:OSS_PREFIX }
if (-not $Region) { $Region = $env:OSS_REGION }
if (-not $Bucket) { $Bucket = 'muxigame-prod-static-cn' }
if (-not $Prefix) { $Prefix = 'bmc/release/latest/' }
if (-not $Region) { $Region = 'cn-hangzhou' }

if ($Bucket -notmatch '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$') {
    throw "OSS Bucket 名称不合法：$Bucket"
}

$Prefix = $Prefix.Replace('\', '/').TrimStart('/')
if (-not $Prefix.EndsWith('/')) { $Prefix += '/' }
if ($Prefix.Contains('//') -or $Prefix.Split('/') -contains '..') {
    throw "OSS 对象前缀不合法：$Prefix"
}

$ossutil = Get-Command ossutil -ErrorAction SilentlyContinue
if (-not $ossutil) {
    $installed = Join-Path $env:LOCALAPPDATA 'Programs\ossutil\ossutil-2.4.0-windows-amd64\ossutil.exe'
    if (Test-Path -LiteralPath $installed) {
        $ossutil = Get-Item -LiteralPath $installed
    } else {
        throw '找不到 ossutil。请先安装阿里云官方 ossutil 2.x。'
    }
}

$installer = Join-Path $OutDir 'client\BatterMC5Remake-setup.exe'
$releaseMetadata = Join-Path $OutDir 'client\launcher-release.json'
$manifest = Join-Path $workspaceRoot 'server\publish\manifest.json'
$packFiles = Join-Path $workspaceRoot 'server\publish'
$files = @(
    [pscustomobject]@{
        Path = $installer
        Object = $Prefix + 'BatterMC5Remake-setup.exe'
        ContentType = 'application/vnd.microsoft.portable-executable'
    },
    [pscustomobject]@{
        Path = $manifest
        Object = $Prefix + 'manifest.json'
        ContentType = 'application/json; charset=utf-8'
    }
)

foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file.Path -PathType Leaf)) {
        throw "待发布文件不存在：$($file.Path)"
    }
}
if (-not (Test-Path -LiteralPath $releaseMetadata -PathType Leaf)) {
    throw "客户端发布元数据不存在：$releaseMetadata"
}
if (-not (Test-Path -LiteralPath $packFiles -PathType Container)) {
    throw "整合包发布目录不存在：$packFiles"
}

$common = @()
if ($Region) { $common += @('--region', $Region) }
$destination = "oss://$Bucket/$Prefix"

Write-Host "发布目标：$destination" -ForegroundColor Cyan
foreach ($file in $files) {
    $item = Get-Item -LiteralPath $file.Path
    $sha256 = (Get-FileHash -LiteralPath $file.Path -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host ("  {0}  {1:N1} MB  SHA-256 {2}" -f $item.Name, ($item.Length / 1MB), $sha256)
}
$packFileCount = (Get-ChildItem -LiteralPath $packFiles -File -Recurse | Measure-Object).Count
Write-Host "  整合包文件 $packFileCount 个 -> $($Prefix)files/" -ForegroundColor DarkGray

if (-not $PSCmdlet.ShouldProcess($destination, '上传客户端、清单和整合包文件')) { return }

# OSS 的“目录”是以 / 结尾的零字节对象；若目录已存在，mkdir 会返回非零。
& $ossutil.FullName mkdir $destination @common 2>$null
if ($LASTEXITCODE -ne 0) {
    & $ossutil.FullName stat $destination @common *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "无法创建或访问 OSS 前缀：$destination"
    }
}

$checkpoint = Join-Path $OutDir '.ossutil-checkpoint'
New-Item -ItemType Directory -Path $checkpoint -Force | Out-Null

foreach ($file in $files) {
    $target = "oss://$Bucket/$($file.Object)"
    Write-Host "上传 $($file.Path) -> $target" -ForegroundColor Cyan
    & $ossutil.FullName cp $file.Path $target -f `
        --checkpoint-dir $checkpoint `
        --content-type $file.ContentType `
        --cache-control 'no-cache, no-store, must-revalidate' `
        @common
    if ($LASTEXITCODE -ne 0) { throw "上传失败：$target" }

    & $ossutil.FullName stat $target --human-readable @common
    if ($LASTEXITCODE -ne 0) { throw "上传后校验失败：$target" }
}

$packTarget = "oss://$Bucket/$($Prefix)files/"
Write-Host "同步整合包 $packFiles -> $packTarget" -ForegroundColor Cyan
& $ossutil.FullName cp $packFiles $packTarget -r -f `
    --checkpoint-dir $checkpoint `
    --cache-control 'public, max-age=31536000, immutable' `
    @common
if ($LASTEXITCODE -ne 0) { throw "整合包文件上传失败：$packTarget" }

Write-Host "发布完成：$destination" -ForegroundColor Green
Copy-Item -LiteralPath $releaseMetadata -Destination (Join-Path $workspaceRoot 'server\launcher-release.json') -Force
Write-Host '已更新官网的已发布客户端版本记录' -ForegroundColor DarkGray
