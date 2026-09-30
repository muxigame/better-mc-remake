[CmdletBinding()]
param(
    [string]$ServerRoot
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not $ServerRoot) {
    $candidate = Join-Path (Split-Path $repoRoot -Parent) 'bmc5server'
    if (Test-Path -LiteralPath $candidate -PathType Container) {
        $ServerRoot = $candidate
    }
}
if (-not $ServerRoot -or -not (Test-Path -LiteralPath $ServerRoot -PathType Container)) {
    throw 'Server root unavailable. Pass -ServerRoot so client/server-sensitive files can be verified before publishing.'
}

$clientRoot = Join-Path $PSScriptRoot 'source\Better MC Remake [FORGE]'

function Get-Sha256([string]$Path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($Path)))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Assert-SameFile([string]$RelativePath) {
    $serverPath = Join-Path $ServerRoot $RelativePath
    $clientPath = Join-Path $clientRoot $RelativePath
    if (-not (Test-Path -LiteralPath $serverPath -PathType Leaf)) { throw "Server file missing: $RelativePath" }
    if (-not (Test-Path -LiteralPath $clientPath -PathType Leaf)) { throw "Client mirror missing: $RelativePath" }
    $serverHash = Get-Sha256 $serverPath
    $clientHash = Get-Sha256 $clientPath
    if ($serverHash -ne $clientHash) {
        $nl = [Environment]::NewLine
        throw ("Client/server mirror mismatch: $RelativePath" + $nl + "server=$serverHash" + $nl + "client=$clientHash")
    }
}

# KubeJS startup item modifications are local item-component mutations.
# If only the server runs them, the client tooltip can disagree with actual combat values.
Assert-SameFile 'kubejs\startup_scripts\iaf_weapon_balance.js'

# Ice and Fire's common config participates in constructing item tiers and armor.
Assert-SameFile 'config\iceandfire\iaf-common.json'

$serverCore = @(Get-ChildItem -LiteralPath (Join-Path $ServerRoot 'mods') -File -Filter 'muxi-game-core-*.jar')
$clientCore = @(Get-ChildItem -LiteralPath (Join-Path $clientRoot 'mods') -File -Filter 'muxi-game-core-*.jar')
if ($serverCore.Count -ne 1 -or $clientCore.Count -ne 1) {
    throw "Expected exactly one muxi-game-core JAR on each side (server=$($serverCore.Count), client=$($clientCore.Count))."
}
if ($serverCore[0].Name -ne $clientCore[0].Name) {
    throw "muxi-game-core version mismatch: server=$($serverCore[0].Name), client=$($clientCore[0].Name)"
}
if ((Get-Sha256 $serverCore[0].FullName) -ne (Get-Sha256 $clientCore[0].FullName)) {
    throw 'muxi-game-core filename matches but bytes differ.'
}

Write-Host 'Client/server-sensitive Ice and Fire files and muxi-game-core are synchronized.' -ForegroundColor Green
