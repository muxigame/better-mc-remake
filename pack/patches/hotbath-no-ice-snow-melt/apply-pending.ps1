param([string]$ServerRoot)
$ErrorActionPreference = 'Stop'
if (-not $ServerRoot) { $ServerRoot = Split-Path -Parent $PSScriptRoot }
$ServerRoot = [IO.Path]::GetFullPath($ServerRoot)
$bundle = Join-Path $ServerRoot '.pending-mod-updates\hotbath-c2me-v1'
$stamp = Join-Path $bundle 'applied.json'
# One-shot deployment: never downgrade a later manually installed mod version.
if (Test-Path -LiteralPath $stamp) { return }

$originalHash = '54e87d946936af0b2da7e1e5b0d8bd775f5ea7f3e3c3680870677dadd653c74c'
$patchedHash = '8f94e4d34736a83895fdc1494ea49289b353b6bda10486514e98b3b459e74e48'
$c2meHash = '67fe384ae84f947e999a02ebde5f5e7de3a632803304a625beee9c4268509a76'
$c2meName = 'c2me-neoforge-mc1.21.1-0.4.0-alpha.0.120.jar'
$hotStage = Join-Path $bundle 'hotbath-1.21.1-4.1.0-muxi-no-melt.jar'
$c2meStage = Join-Path $bundle $c2meName
$mods = Join-Path $ServerRoot 'mods'
$c2meDest = Join-Path $mods $c2meName
$locks = New-Object 'System.Collections.Generic.List[System.IDisposable]'
$changedHotBath = $false
$addedC2me = $false
$disabledDirchunk = New-Object 'System.Collections.Generic.List[object]'
$hotTemp = $null
$c2meTemp = $null

function File-Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

try {
    # Disable dirchunk as requested; do not attempt another compatibility patch.
    $dirchunkJars = @(Get-ChildItem -LiteralPath $mods -File | Where-Object { $_.Name -like '*dirchunk*.jar' })
    if ((File-Hash $hotStage) -ne $patchedHash -or (File-Hash $c2meStage) -ne $c2meHash) {
        throw 'Pending mod hash mismatch. No changes applied.'
    }
    $hotBath = @(Get-ChildItem -LiteralPath $mods -File | Where-Object { $_.Name -like '*hotbath-1.21.1-4.1.0.jar' })
    if ($hotBath.Count -ne 1) { throw 'Expected exactly one Hot Bath 4.1.0 JAR.' }
    $hotDest = $hotBath[0].FullName
    $beforeHash = File-Hash $hotDest
    if ($beforeHash -ne $originalHash -and $beforeHash -ne $patchedHash) {
        throw 'Installed Hot Bath has unexpected changes; refusing to overwrite.'
    }
    $c2meJars = @(Get-ChildItem -LiteralPath $mods -File | Where-Object { $_.Name -like '*c2me*.jar' })
    if ($c2meJars.Count -gt 1 -or ($c2meJars.Count -eq 1 -and $c2meJars[0].Name -ne $c2meName)) {
        throw 'Another C2ME version is already installed; refusing to duplicate it.'
    }
    if ((Test-Path -LiteralPath $c2meDest) -and (File-Hash $c2meDest) -ne $c2meHash) {
        throw 'Installed C2ME differs from the pinned artifact.'
    }

    # Only inspect lock handles; do not read or change world data or credentials.
    # A running server must release its world and JAR before this can continue.
    foreach ($directory in Get-ChildItem -LiteralPath $ServerRoot -Directory) {
        $sessionLock = Join-Path $directory.FullName 'session.lock'
        if (Test-Path -LiteralPath $sessionLock) {
            $locks.Add([IO.File]::Open($sessionLock, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None))
        }
    }
    foreach ($jarPath in @($hotDest) + @($dirchunkJars | ForEach-Object { $_.FullName })) {
        $jarLock = [IO.File]::Open($jarPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
        $jarLock.Dispose()
    }

    $backup = Join-Path $bundle 'backup'
    [IO.Directory]::CreateDirectory($backup) | Out-Null
    $hotBackup = Join-Path $backup ('hotbath-before-' + [Guid]::NewGuid().ToString('N') + '.jar')
    $transaction = [Guid]::NewGuid().ToString('N')
    $hotTemp = $hotDest + '.' + $transaction + '.muxi-pending'
    $c2meTemp = $c2meDest + '.' + $transaction + '.muxi-pending'

    if ($dirchunkJars.Count -gt 0) {
        $disabledDirectory = Join-Path $ServerRoot 'mods-disabled'
        [IO.Directory]::CreateDirectory($disabledDirectory) | Out-Null
        foreach ($jar in $dirchunkJars) {
            $disabledPath = Join-Path $disabledDirectory $jar.Name
            if (Test-Path -LiteralPath $disabledPath) {
                $disabledPath = Join-Path $disabledDirectory ($jar.BaseName + '.' + $transaction + '.jar')
            }
            $move = [pscustomobject]@{ original = $jar.FullName; disabled = $disabledPath; sha256 = (File-Hash $jar.FullName) }
            [IO.File]::Move($move.original, $move.disabled)
            $disabledDirchunk.Add($move)
            if ((File-Hash $move.disabled) -ne $move.sha256) { throw 'Disabled dirchunk hash mismatch.' }
        }
    }
    if ($beforeHash -ne $patchedHash) {
        [IO.File]::Copy($hotStage, $hotTemp, $false)
        [IO.File]::Replace($hotTemp, $hotDest, $hotBackup)
        $changedHotBath = $true
    }
    if (-not (Test-Path -LiteralPath $c2meDest)) {
        [IO.File]::Copy($c2meStage, $c2meTemp, $false)
        [IO.File]::Move($c2meTemp, $c2meDest)
        $addedC2me = $true
    }
    if ((File-Hash $hotDest) -ne $patchedHash -or (File-Hash $c2meDest) -ne $c2meHash) {
        throw 'Installed artifact verification failed.'
    }
    if (@(Get-ChildItem -LiteralPath $mods -File | Where-Object { $_.Name -like '*dirchunk*.jar' }).Count -ne 0) {
        throw 'dirchunk remains in the enabled mod directory.'
    }
    $record = [ordered]@{
        appliedAt = [DateTimeOffset]::Now.ToString('o')
        hotBath = $hotBath[0].Name
        hotBathSha256 = $patchedHash
        c2me = $c2meName
        c2meSha256 = $c2meHash
        originalBackup = if ($changedHotBath) { $hotBackup } else { $null }
        disabledDirchunk = @($disabledDirchunk | ForEach-Object { $_ })
    }
    [IO.File]::WriteAllText($stamp, ($record | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Write-Host '[muxi] dirchunk disabled; existing Hot Bath no-melt patch installed; server C2ME restored.'
} catch {
    $failure = $_
    if ($addedC2me -and (Test-Path -LiteralPath $c2meDest)) { [IO.File]::Delete($c2meDest) }
    if ($changedHotBath -and (Test-Path -LiteralPath $hotBackup)) {
        [IO.File]::Copy($hotBackup, $hotTemp, $false)
        # Windows PowerShell converts $null here to an invalid empty path.
        # Preserve the replaced patch as well, rather than passing a null backup.
        $rollbackBackup = Join-Path $backup ('hotbath-rollback-replaced-' + [Guid]::NewGuid().ToString('N') + '.jar')
        [IO.File]::Replace($hotTemp, $hotDest, $rollbackBackup)
    }
    for ($index = $disabledDirchunk.Count - 1; $index -ge 0; $index--) {
        $move = $disabledDirchunk[$index]
        if (Test-Path -LiteralPath $move.disabled) { [IO.File]::Move($move.disabled, $move.original) }
    }
    throw "Pending Hot Bath/C2ME update not applied. Stop the server before starting again. $failure"
} finally {
    foreach ($lock in $locks) { $lock.Dispose() }
    if ($hotTemp -and (Test-Path -LiteralPath $hotTemp)) { [IO.File]::Delete($hotTemp) }
    if ($c2meTemp -and (Test-Path -LiteralPath $c2meTemp)) { [IO.File]::Delete($c2meTemp) }
}
