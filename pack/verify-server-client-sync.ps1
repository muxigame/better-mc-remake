[CmdletBinding()]
param(
    [string]$ServerRoot,
    [switch]$AllowTerminalClientOnlyUpdate
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

$serverTerminal = @(Get-ChildItem -LiteralPath (Join-Path $ServerRoot 'mods') -File -Filter 'muxi-terminal-*.jar')
$clientTerminal = @(Get-ChildItem -LiteralPath (Join-Path $clientRoot 'mods') -File -Filter 'muxi-terminal-*.jar')
if ($serverTerminal.Count -ne 1 -or $clientTerminal.Count -ne 1) {
    throw "Expected exactly one muxi-terminal JAR on each side (server=$($serverTerminal.Count), client=$($clientTerminal.Count))."
}
if ($AllowTerminalClientOnlyUpdate) {
    $python = (Get-Command python -ErrorAction Stop).Source
    & $python (Join-Path $PSScriptRoot 'verify-terminal-compatibility.py') --server $serverTerminal[0].FullName --client $clientTerminal[0].FullName
    if ($LASTEXITCODE -ne 0) { throw 'Terminal client update changes the server contract; coordinated server deployment is required.' }
} elseif ($serverTerminal[0].Name -ne $clientTerminal[0].Name) {
    throw "muxi-terminal version mismatch: server=$($serverTerminal[0].Name), client=$($clientTerminal[0].Name)"
} elseif ((Get-Sha256 $serverTerminal[0].FullName) -ne (Get-Sha256 $clientTerminal[0].FullName)) {
    throw 'muxi-terminal filename matches but bytes differ.'
}

$serverOutbreak = @(Get-ChildItem -LiteralPath (Join-Path $ServerRoot 'mods') -File -Filter 'muxi-outbreak-*.jar')
$clientOutbreak = @(Get-ChildItem -LiteralPath (Join-Path $clientRoot 'mods') -File -Filter 'muxi-outbreak-*.jar')
$packSpec = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'packspec.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($packSpec.exclude -contains 'mods/muxi-outbreak-*.jar') {
    # Outbreak is an independent server-side battle mode. It is intentionally
    # excluded from the Better MC main client release, but its server artifact
    # may still exist on the server. Only verify the client does not ship it.
    if ($clientOutbreak.Count -ne 0) { throw 'Outbreak is excluded from this client release but remains in the client package.' }
} elseif ($serverOutbreak.Count -ne 1 -or $clientOutbreak.Count -ne 1) {
    throw 'Expected exactly one muxi-outbreak JAR on each side.'
} elseif ($serverOutbreak[0].Name -ne $clientOutbreak[0].Name -or (Get-Sha256 $serverOutbreak[0].FullName) -ne (Get-Sha256 $clientOutbreak[0].FullName)) {
    throw 'muxi-outbreak client/server artifact mismatch.'
}
foreach ($module in @('muxi-minigames', 'muxi-zombie-challenge')) {
    $serverFiles = @(Get-ChildItem -LiteralPath (Join-Path $ServerRoot 'mods') -File -Filter "$module-*.jar")
    $clientFiles = @(Get-ChildItem -LiteralPath (Join-Path $clientRoot 'mods') -File -Filter "$module-*.jar")
    if ($serverFiles.Count -ne 1 -or $clientFiles.Count -ne 1) { throw "Expected exactly one $module JAR on each candidate side." }
    if ($serverFiles[0].Name -ne $clientFiles[0].Name -or (Get-Sha256 $serverFiles[0].FullName) -ne (Get-Sha256 $clientFiles[0].FullName)) { throw "$module candidate artifact mismatch." }
}
Write-Host 'Client/server-sensitive Ice and Fire files and five coordinated module artifacts checked.' -ForegroundColor Green
