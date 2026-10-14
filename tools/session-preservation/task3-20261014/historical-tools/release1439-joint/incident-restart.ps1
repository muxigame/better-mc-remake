$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$s='C:\Users\Administrator\WorkSpace\muxigame\bmc5server'
$r='C:\Users\Administrator\WorkSpace\muxigame\.releases\production1439-joint\incident-20261014-1301'
$plan=Get-Content 'C:\Users\Administrator\WorkSpace\muxigame\.releases\production1439-joint\payload\execution-plan.json' -Raw -Encoding UTF8|ConvertFrom-Json
function Save($name,$value){$value|ConvertTo-Json -Depth 8|Set-Content "$r\$name" -Encoding UTF8}
function Hash($path){(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()}
if(Get-CimInstance Win32_Process -Filter "Name='java.exe'"|Where-Object{$_.CommandLine -like '*neoforge*win_args.txt*'}){throw 'Production JVM remains'}
if(netstat -ano|Select-String ':25565 .*LISTENING|:25575 .*LISTENING'){throw 'Production listener already present'}
$lock=[IO.File]::Open("$s\world\session.lock",[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None);$lock.Dispose()
foreach($f in $plan.startup){if((Hash (Join-Path $s $f.path)) -ne $f.sha256){throw 'Original startup script drift'}}
foreach($f in $plan.afterMods){if((Hash "$s\mods\$($f.name)") -ne $f.sha256){throw 'Released mod drift'}}
$start=[DateTime]::UtcNow
$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
$launched=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /d /c start.bat mc';CurrentDirectory=$s;ProcessStartupInformation=$startup}
if($launched.ReturnValue -ne 0){throw 'Original startup failed'}
Save 'restart-starting.json' @{atUtc=$start.ToString('o');launcherPid=$launched.ProcessId;version='1.4.39';oldJvmNormallyExited=$true;newBackupCreated=$false}
$until=[DateTime]::UtcNow.AddMinutes(15)
do {Start-Sleep -Seconds 3;$ready=((Get-Item "$s\logs\latest.log").LastWriteTimeUtc -ge $start) -and (Select-String "$s\logs\latest.log" -Pattern 'Done \(' -Quiet);$listeners=@(Get-NetTCPConnection -LocalPort 25565 -State Listen -ErrorAction SilentlyContinue);if([DateTime]::UtcNow -gt $until){throw 'Restart startup timeout'}} while(-not($ready -and $listeners.Count))
$p=Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0].OwningProcess)";if($p.CreationDate.ToUniversalTime() -lt $start){throw 'Stale process'}
$online=(& "$s\tools\rcon.ps1" 'list'|Out-String)
$first=(& "$s\tools\rcon.ps1" 'time query gametime'|Out-String);Start-Sleep -Seconds 3;$second=(& "$s\tools\rcon.ps1" 'time query gametime'|Out-String)
$a=[regex]::Matches($first,'\d+');$b=[regex]::Matches($second,'\d+');if(-not $a.Count -or -not $b.Count -or [long]$b[$b.Count-1].Value -le [long]$a[$a.Count-1].Value){throw 'Tick not progressing'}
$tps=(& "$s\tools\rcon.ps1" 'forge tps'|Out-String)
foreach($f in $plan.afterMods){if((Hash "$s\mods\$($f.name)") -ne $f.sha256){throw 'Released mod drift'}}
$errors=@(Select-String "$s\logs\latest.log" -Pattern 'private_playback_v1 may not be sent|Preparing crash report|\[Server thread/FATAL\]'|ForEach-Object{$_.Line})
$out=@{atUtc=[DateTime]::UtcNow.ToString('o');version='1.4.39';pid=$p.ProcessId;createdUtc=$p.CreationDate.ToUniversalTime().ToString('o');rconResponded=$true;online=$online;gametimeBefore=$a[$a.Count-1].Value;gametimeAfter=$b[$b.Count-1].Value;tps=$tps;modsVerified=$plan.afterMods.Count;musicErrorRecurrence=$errors;newBackupCreated=$false;worldReplaced=$false;forceTermination=$false}
Save 'restart-healthy.json' $out
$out|ConvertTo-Json -Depth 6
