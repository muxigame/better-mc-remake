[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Bucket,
    [string]$Prefix,
    [string]$Region,
    [string]$ManifestPath,
    [string]$PackFilesDir,
    [string]$RemoteManifestUrl
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
        $name = $pair[0].Trim(); $value = $pair[1].Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

function Get-Utf8Json([string]$Uri) {
    $client = New-Object System.Net.WebClient
    try {
        $client.Headers['User-Agent'] = 'BatterMC-Pack-Release/1.0'
        $bytes = $client.DownloadData($Uri)
        return ([System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json)
    } finally { $client.Dispose() }
}

$workspaceRoot = Split-Path $PSScriptRoot -Parent
Import-DotEnv (Join-Path $workspaceRoot '.env')
if (-not $Bucket) { $Bucket = $env:OSS_BUCKET }
if (-not $Prefix) { $Prefix = $env:OSS_PREFIX }
if (-not $Region) { $Region = $env:OSS_REGION }
if (-not $Bucket) { $Bucket = 'muxigame-prod-static-cn' }
if (-not $Prefix) { $Prefix = 'bmc/release/latest/' }
if (-not $Region) { $Region = 'cn-hangzhou' }
$Prefix = $Prefix.Replace('\','/').TrimStart('/'); if (-not $Prefix.EndsWith('/')) { $Prefix += '/' }
if (-not $ManifestPath) { $ManifestPath = Join-Path $PSScriptRoot 'staging\manifest.json' }
if (-not $PackFilesDir) { $PackFilesDir = Join-Path $PSScriptRoot 'staging\files' }
if (-not $RemoteManifestUrl) {
    $publicBase = if ($env:BMC_PUBLIC_URL) { $env:BMC_PUBLIC_URL.TrimEnd('/') } else { 'https://mc.muxigame.com' }
    $control = Get-Utf8Json "$publicBase/api/v1/manifest"
    $RemoteManifestUrl = [string]$control.manifestUrl
    if (-not $RemoteManifestUrl) {
        # 兼容切换到“OSS manifest 唯一真相源”之前的旧控制面。
        $legacyBase = if ($env:BMC_OSS_BASE_URL) {
            $env:BMC_OSS_BASE_URL.TrimEnd('/')
        } else {
            "https://$Bucket.oss-$Region.aliyuncs.com/$($Prefix.TrimEnd('/'))"
        }
        $RemoteManifestUrl = "$legacyBase/manifest.json"
        Write-Host "旧控制面未返回 manifestUrl，迁移期回退到 $RemoteManifestUrl" -ForegroundColor DarkGray
    }
}
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "manifest 不存在：$ManifestPath" }

$ossutil = Get-Command ossutil -ErrorAction SilentlyContinue
if ($ossutil) { $ossutilPath = $ossutil.Path } else {
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\ossutil\ossutil-2.4.0-windows-amd64\ossutil.exe'
    if (-not (Test-Path -LiteralPath $candidate)) { throw '找不到 ossutil 2.x' }
    $ossutilPath = $candidate
}
$common=@(); if($Region){$common += @('--region',$Region)}
$local = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$remote = $null
try { $remote = Get-Utf8Json $RemoteManifestUrl } catch { Write-Host "无法读取线上 manifest，将按全量上传：$($_.Exception.Message)" -ForegroundColor Yellow }
$remoteByPath=@{}; if($remote -and $remote.files){ foreach($f in $remote.files){$remoteByPath[[string]$f.path]=[string]$f.sha1} }
$localByPath=@{}; $changed=@(); foreach($f in $local.files){ $p=[string]$f.path; $s=[string]$f.sha1; $localByPath[$p]=$s; if(-not $remoteByPath.ContainsKey($p) -or $remoteByPath[$p] -ne $s){$changed += $f} }
$removed=@(); foreach($p in $remoteByPath.Keys){ if(-not $localByPath.ContainsKey($p)){$removed += $p} }
Write-Host "整合包 $($local.pack.version)：$($local.files.Count) 文件，新增/变化 $($changed.Count)，移除 $($removed.Count)" -ForegroundColor Cyan
if($changed.Count -gt 0 -and -not (Test-Path -LiteralPath $PackFilesDir -PathType Container)){ throw "缺少 staging 文件目录：$PackFilesDir" }
if(-not $PSCmdlet.ShouldProcess("oss://$Bucket/$Prefix", "增量发布整合包 $($local.pack.version)")){ return }
$checkpoint=Join-Path $PSScriptRoot 'staging\.ossutil-checkpoint'; New-Item -ItemType Directory -Force -Path $checkpoint | Out-Null
function Upload([string]$Source,[string]$Object,[string]$ContentType,[string]$CacheControl){
    $target="oss://$Bucket/$Object"; Write-Host "上传 $Object" -ForegroundColor DarkCyan
    & $ossutilPath cp $Source $target -f --checkpoint-dir $checkpoint --content-type $ContentType --cache-control $CacheControl @common
    if($LASTEXITCODE -ne 0){throw "上传失败：$target"}
}
foreach($f in $changed){
    if ($f.externalDownload) {
        if (-not ([string]$f.url).StartsWith('https://cdn.modrinth.com/data/')) { throw 'Invalid external mod download origin' }
        Write-Host "使用作者源站下载，不转载到 OSS：$($f.path)" -ForegroundColor DarkGray
        continue
    }
    $rel=([string]$f.path).Replace('\','/').TrimStart('/'); if($rel.Split('/') -contains '..'){throw "非法路径：$rel"}
    $source=Join-Path $PackFilesDir ($rel.Replace('/',[IO.Path]::DirectorySeparatorChar)); if(-not(Test-Path -LiteralPath $source -PathType Leaf)){throw "缺少待上传文件：$source"}
    Upload $source ($Prefix+'files/'+$rel) 'application/octet-stream' 'no-cache, must-revalidate'
}
# manifest 永远最后发布，避免客户端先看到尚未上传完的新内容。
Upload $ManifestPath ($Prefix+'manifest.json') 'application/json; charset=utf-8' 'no-cache, no-store, must-revalidate'
if($removed.Count -gt 0){Write-Host "$($removed.Count) 个旧 OSS 对象已从 manifest 移除，暂留待垃圾回收。" -ForegroundColor DarkGray}
Write-Host '整合包发布完成；OSS manifest 是唯一发布真相源。' -ForegroundColor Green
