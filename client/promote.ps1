[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory=$true)]
    [string]$Version,
    [string]$Bucket,
    [string]$Region,
    [string]$ClientPrefix,
    [string]$PolicyPath
)

$ErrorActionPreference = 'Stop'

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        $p = $t.Split('=', 2)
        $value = $p[1].Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($p[0].Trim(), $value, 'Process')
    }
}

$workspaceRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'release-policy.ps1')
Import-DotEnv (Join-Path $workspaceRoot '.env')
if (-not $Bucket) { $Bucket = $env:OSS_BUCKET }
if (-not $Region) { $Region = $env:OSS_REGION }
if (-not $Bucket) { $Bucket = 'muxigame-prod-static-cn' }
if (-not $Region) { $Region = 'cn-hangzhou' }
if (-not $ClientPrefix) { $ClientPrefix = 'bmc/client/' }
$ClientPrefix = $ClientPrefix.Replace('\\','/').TrimStart('/')
if (-not $ClientPrefix.EndsWith('/')) { $ClientPrefix += '/' }

if ($Version -notmatch '^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$') {
    throw "客户端版本号不合法：$Version"
}
$history = Join-Path $PSScriptRoot "releases\$Version.json"
if (-not (Test-Path -LiteralPath $history -PathType Leaf)) {
    throw "本地没有这个已发布版本记录：$history"
}
$meta = Get-Content -LiteralPath $history -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$meta.version -ne $Version) { throw "发布记录版本不匹配：$history" }

# Reapply CURRENT policy, never restore an obsolete floor from historical metadata.
$promotionDir = Join-Path $workspaceRoot 'artifacts\client'
New-Item -ItemType Directory -Force -Path $promotionDir | Out-Null
$promoted = Join-Path $promotionDir "promote-$Version.json"
Merge-ClientUpdatePolicy $workspaceRoot $history $PolicyPath $promoted

$ossutil = Get-Command ossutil -ErrorAction SilentlyContinue
if ($ossutil) { $oss = $ossutil.Path } else {
    $oss = Join-Path $env:LOCALAPPDATA 'Programs\ossutil\ossutil-2.4.0-windows-amd64\ossutil.exe'
    if (-not (Test-Path -LiteralPath $oss)) { throw '找不到 ossutil 2.x' }
}
$common = @(); if ($Region) { $common += @('--region', $Region) }

# 晋升前必须确认不可变历史对象真实存在，避免 latest 指向空版本。
$requiredObjects = @([string]$meta.ossObject, [string]$meta.releaseObject)
if ([bool]$meta.updaterCompatible) { $requiredObjects += [string]$meta.signatureObject }
foreach ($object in $requiredObjects) {
    if (-not $object) { throw "版本 $Version 的发布记录缺少 OSS 对象字段" }
    $target = "oss://$Bucket/$object"
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & $oss stat $target @common *> $null
        $exists = $LASTEXITCODE -eq 0
    } finally { $ErrorActionPreference = $previous }
    if (-not $exists) { throw "OSS 历史对象不存在，拒绝晋升：$target" }
}

$latest = "oss://$Bucket/$($ClientPrefix)latest/metadata.json"
Write-Host "将客户端 latest 切换到 $Version" -ForegroundColor Cyan
if (-not $PSCmdlet.ShouldProcess($latest, "晋升/回滚客户端到 $Version")) { return }
& $oss cp $promoted $latest -f --content-type 'application/json; charset=utf-8' --cache-control 'no-cache, no-store, must-revalidate' @common
if ($LASTEXITCODE -ne 0) { throw '更新 latest metadata 失败' }

# 同步镜像内 fallback；真正线上当前版本仍以 OSS latest metadata 为准。
Copy-Item -LiteralPath $promoted -Destination (Join-Path $workspaceRoot 'server\launcher-release.json') -Force
Write-Host "latest 已切换到 $Version；历史二进制未发生任何修改。" -ForegroundColor Green

