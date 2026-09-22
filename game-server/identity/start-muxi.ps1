[CmdletBinding()]
param([string]$JavaExe)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Test-Java21([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Candidate
    $info.Arguments = '-version'
    $info.UseShellExecute = $false
    $info.RedirectStandardError = $true
    $info.RedirectStandardOutput = $true
    $info.CreateNoWindow = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        if (-not $process.WaitForExit(5000)) { $process.Kill(); return $false }
        $text = $process.StandardError.ReadToEnd() + $process.StandardOutput.ReadToEnd()
        return ($process.ExitCode -eq 0 -and $text -match 'version "21[.]')
    } catch { return $false } finally { $process.Dispose() }
}

if (-not $JavaExe) {
    $candidates = @()
    if ($env:JAVA_HOME) { $candidates += Join-Path $env:JAVA_HOME 'bin\java.exe' }
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Android Studio\jbr\bin\java.exe'
    foreach ($root in @('C:\Program Files\Eclipse Adoptium', 'C:\Program Files\Java', 'C:\Program Files\Microsoft')) {
        if (Test-Path -LiteralPath $root) {
            $candidates += @(Get-ChildItem -LiteralPath $root -Directory | ForEach-Object { Join-Path $_.FullName 'bin\java.exe' })
        }
    }
    $command = Get-Command java -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $JavaExe = $candidates | Where-Object { Test-Java21 $_ } | Select-Object -First 1
}
if (-not (Test-Java21 $JavaExe)) { throw 'Java 21 is required. Pass -JavaExe with a Java 21 executable.' }
foreach ($file in @('config\muxi-identity-bridge.json', 'mods\muxi-identity-1.0.0.jar', 'mods\simplenicknames-1.21.1-neoforge-0.8.0.jar', 'libraries\net\neoforged\neoforge\21.1.250\win_args.txt')) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required server file missing: $file" }
}
# Intentionally do not start/kill FRP, change RCON, accept EULA, or move worlds.
Write-Host "Java 21: $JavaExe"
& $JavaExe '@user_jvm_args.txt' '@libraries/net/neoforged/neoforge/21.1.250/win_args.txt' nogui
exit $LASTEXITCODE
