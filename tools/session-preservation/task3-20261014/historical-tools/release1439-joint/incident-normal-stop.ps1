$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$s='C:\Users\Administrator\WorkSpace\muxigame\bmc5server'
$r='C:\Users\Administrator\WorkSpace\muxigame\.releases\production1439-joint\incident-20261014-1301'
$p=Get-CimInstance Win32_Process -Filter 'ProcessId=59544'
if(-not $p -or $p.CreationDate.ToUniversalTime().ToString('o') -ne '2026-10-14T12:51:53.3734770Z' -or $p.CommandLine -notlike '*neoforge*win_args.txt*'){throw 'Identity changed'}
$lock=[IO.File]::Open("$s\world\session.lock",[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None);$lock.Dispose()
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class OwnedConsoleSignal{[DllImport("kernel32.dll",SetLastError=true)]public static extern bool FreeConsole();[DllImport("kernel32.dll",SetLastError=true)]public static extern bool AttachConsole(uint id);[DllImport("kernel32.dll",SetLastError=true)]public static extern bool SetConsoleCtrlHandler(IntPtr handler,bool add);[DllImport("kernel32.dll",SetLastError=true)]public static extern bool GenerateConsoleCtrlEvent(uint type,uint group);}'
[OwnedConsoleSignal]::FreeConsole()|Out-Null
if(-not [OwnedConsoleSignal]::AttachConsole(59544)){throw ('AttachConsole failed '+[Runtime.InteropServices.Marshal]::GetLastWin32Error())}
[OwnedConsoleSignal]::SetConsoleCtrlHandler([IntPtr]::Zero,$true)|Out-Null
$sent=[OwnedConsoleSignal]::GenerateConsoleCtrlEvent(0,0)
Start-Sleep -Seconds 1
[OwnedConsoleSignal]::FreeConsole()|Out-Null
if(-not $sent){throw 'CTRL_C failed'}
$until=[DateTime]::UtcNow.AddSeconds(30)
while((Get-Process -Id 59544 -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $until){Start-Sleep -Seconds 1}
$out=@{atUtc=[DateTime]::UtcNow.ToString('o');normalConsoleCtrlCSent=$sent;forceTermination=$false;oldPid=59544;oldProcessExited=(-not [bool](Get-Process -Id 59544 -ErrorAction SilentlyContinue));worldSavedAndUnlockedBeforeSignal=$true}
$out|ConvertTo-Json|Set-Content "$r\normal-console-stop.json" -Encoding UTF8
$out|ConvertTo-Json
