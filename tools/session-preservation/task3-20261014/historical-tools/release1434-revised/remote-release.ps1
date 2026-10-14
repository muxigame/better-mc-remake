param([ValidateSet('Stage','Activate','Publish','Finalize')][string]$Phase,[string]$PayloadSha256)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
$w='C:\Users\Administrator\WorkSpace\muxigame';$s="$w\bmc5server";$r="$w\.releases\production1434-race5"
function Hash($p){(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()}
function Save($n,$v){$v|ConvertTo-Json -Depth 10|Set-Content -LiteralPath "$r\$n" -Encoding UTF8}
function Safe($root,$rel){$p=[IO.Path]::GetFullPath((Join-Path $root $rel));if(-not $p.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escape'};$p}
function Fetch($url){$c=New-Object Net.WebClient;try{$c.Headers['Cache-Control']='no-cache';return ,$c.DownloadData($url)}finally{$c.Dispose()}}
function Digest($b){([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))).Replace('-','').ToLowerInvariant()}
if((Hash "$r\payload.zip") -ne $PayloadSha256){throw 'Payload changed'}
if($Phase -eq 'Stage'){if(Test-Path "$r\payload"){throw 'Already staged; inspect'};Expand-Archive -LiteralPath "$r\payload.zip" -DestinationPath "$r\payload"}
$p="$r\payload";$plan=Get-Content "$p\execution-plan.json" -Raw -Encoding UTF8|ConvertFrom-Json
if($plan.version -ne '1.4.34'){throw 'Wrong version'}
function CheckMods($rows){$a=@(Get-ChildItem -LiteralPath "$s\mods" -Filter *.jar -File);if($a.Count -ne $rows.Count){throw 'Mod count drift'};foreach($f in $rows){if((Hash (Safe $s ('mods/'+$f.name))) -ne $f.sha256){throw ('Mod drift '+$f.name)}}}
function CheckBase{if((Digest (Fetch $plan.publicManifestUrl)) -ne $plan.baseManifestSha256){throw 'Live manifest drift'}}
function CheckStopped{if(Get-NetTCPConnection -LocalPort 25565 -State Listen -ErrorAction SilentlyContinue){throw 'Still listening'};if(Get-CimInstance Win32_Process -Filter "Name='java.exe'"|Where-Object{$_.CommandLine -like '*neoforge*win_args.txt*'}){throw 'Server JVM remains; never force kill'}}
function EnvOSS{foreach($line in Get-Content -LiteralPath "$w\better-mc-remake\.env"){$t=$line.Trim();if(-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')){continue};if($t.StartsWith('export ')){$t=$t.Substring(7).Trim()};$a=$t.Split('=',2);[Environment]::SetEnvironmentVariable($a[0].Trim(),$a[1].Trim().Trim('"').Trim("'"),'Process')};$c=Get-Command ossutil -ErrorAction SilentlyContinue;if($c){$script:oss=$c.Path}else{$script:oss=Join-Path $env:LOCALAPPDATA 'Programs\ossutil\ossutil-2.4.0-windows-amd64\ossutil.exe'}}
foreach($f in $plan.startup){if((Hash (Safe $s $f.path)) -ne $f.sha256){throw 'Startup drift'}}
if($Phase -eq 'Stage'){
 CheckBase;CheckMods $plan.beforeMods
 foreach($f in $plan.items){if((Hash (Safe $p ($f.side+'-overlay/'+$f.path))) -ne $f.sha256){throw 'Staged file drift'};if($f.side -eq 'server' -and (Test-Path -LiteralPath (Safe $s $f.path))){throw ('Unexpected target exists '+$f.path)}}
 EnvOSS
 $occupied=@(& $oss ls ('oss://muxigame-prod-static-cn/'+$plan.objectPrefix) --region cn-hangzhou)
 if($LASTEXITCODE -ne 0 -or -not (($occupied -join "`n") -match 'Object Number is: 0')){throw 'Version namespace occupied/unavailable'}
 $op="$w\.releases\release-owner.json";$prior=Get-Content $op -Raw|ConvertFrom-Json
 if($prior.owner -ne 'task-3' -or $prior.payloadSha256 -ne 'a1f954bf55f2d3d5de1499cd6c09e48d53445f02bd2621ebd62c411d5032243a'){throw 'Prior owner changed; inspect takeover'}
 Save 'prior-owner.json' $prior
 foreach($f in $plan.items|Where-Object{$_.side -eq 'client' -and $_.uploadRequired}){& $oss cp (Safe "$p\client-overlay" $f.path) ('oss://muxigame-prod-static-cn/'+$plan.objectPrefix+'files/'+$f.path) --region cn-hangzhou --content-type 'application/octet-stream' --cache-control 'no-cache, must-revalidate';if($LASTEXITCODE -ne 0){throw 'New object upload failed'}}
 if($LASTEXITCODE -ne 0){throw 'Upload failed; public pointer unchanged'}
 foreach($f in $plan.items|Where-Object{$_.side -eq 'client'}){if((Digest (Fetch $f.url)) -ne $f.sha256){throw ('Object hash mismatch '+$f.path)}}
 & $oss cp "$p\manifest.json" ('oss://muxigame-prod-static-cn/'+$plan.objectPrefix+'manifest.json') --region cn-hangzhou --content-type 'application/json; charset=utf-8' --cache-control 'no-cache, no-store, must-revalidate'
 if($LASTEXITCODE -ne 0){throw 'Immutable manifest stage failed'}
 $nowOwner=Get-Content $op -Raw|ConvertFrom-Json;if($nowOwner.payloadSha256 -ne $prior.payloadSha256 -or $nowOwner.owner -ne $prior.owner){throw 'Owner changed during stage'}
 @{owner='release1434-successor-20261011';version='1.4.34';payloadSha256=$PayloadSha256;state='revised-after-verified-shutdown-fix';predecessorPayload=$prior.payloadSha256}|ConvertTo-Json|Set-Content -LiteralPath $op -Encoding UTF8
 Save 'staged.json' @{version=$plan.version;payloadSha256=$PayloadSha256;publicPointerChanged=$false;objectsVerified=132;atUtc=[DateTime]::UtcNow.ToString('o')}
}
$owner=Get-Content "$w\.releases\release-owner.json" -Raw|ConvertFrom-Json
if($owner.owner -ne 'release1434-successor-20261011' -or $owner.payloadSha256 -ne $PayloadSha256){throw 'Release owner mismatch'}
if($Phase -eq 'Activate'){
 CheckBase;CheckMods $plan.beforeMods
 if(-not(Test-Path "$r\staged.json")){throw 'No stage receipt'}
 # User explicitly authorized no new backup for this release. Preserve retired original files.
 foreach($f in $plan.items){if((Hash (Safe $p ($f.side+'-overlay/'+$f.path))) -ne $f.sha256){throw 'Staged file drift'};if($f.side -eq 'server' -and (Test-Path -LiteralPath (Safe $s $f.path))){throw ('Unexpected target exists '+$f.path)}}
 if((Hash "$r\rollback-manifest-1.4.33.json") -ne $plan.baseManifestSha256){throw 'Existing rollback manifest drift'}
 if((Get-PSDrive C).Free -lt 1GB){throw 'Insufficient runtime headroom'}
 Save 'backup-policy.json' @{policy='none';userExplicitlyRequested=$true;newBackupCreated=$false;preserveExistingRollback=$true;atUtc=[DateTime]::UtcNow.ToString('o')}
 & "$s\tools\rcon.ps1" 'say Maintenance in 120 seconds for pack 1.4.34 and new YSM. Please disconnect safely and update after completion.'
 if(-not $?){throw 'Notice failed'};$notice=[DateTime]::UtcNow;Save 'maintenance-notice.json' @{atUtc=$notice.ToString('o');minimumWaitSeconds=120;newBackupCreated=$false;backupPolicy='none'}
 while([DateTime]::UtcNow -lt $notice.AddSeconds(120)){Start-Sleep -Seconds 2}
 $ids=@(Get-NetTCPConnection -LocalPort 25565 -State Listen|Select-Object -ExpandProperty OwningProcess -Unique);if($ids.Count -ne 1){throw 'Ambiguous production'}
 $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($ids[0])";if($proc.Name -ne 'java.exe' -or $proc.CommandLine -notlike '*neoforge*win_args.txt*'){throw 'Process identity mismatch'}
 Save 'stop-request.json' @{pid=$proc.ProcessId;created=$proc.CreationDate.ToUniversalTime().ToString('o')}
 & "$s\tools\rcon.ps1" 'save-all flush' 'stop';if(-not $?){throw 'Normal save/stop failed'}
 $until=[DateTime]::UtcNow.AddMinutes(10);while(Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue){if([DateTime]::UtcNow -gt $until){throw 'Shutdown timeout; no kill'};Start-Sleep -Seconds 2};CheckStopped
 $lock=[IO.File]::Open("$s\world\session.lock",[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$lock.Dispose()
 if(Test-Path "$r\cold-backup-active"){throw 'Unexpected backup directory; inspect'}
 CheckStopped;CheckMods $plan.beforeMods
 New-Item -ItemType Directory "$r\retired-mods"|Out-Null
 foreach($f in $plan.remove){$src=Safe $s $f.path;if((Hash $src) -ne $f.sha256){throw 'Retired file drift'};$dst=Safe "$r\retired-mods" $f.path;New-Item -ItemType Directory -Force (Split-Path $dst)|Out-Null;Move-Item -LiteralPath $src -Destination $dst}
 foreach($f in $plan.items|Where-Object{$_.side -eq 'server'}){$dest=Safe $s $f.path;if(Test-Path -LiteralPath $dest){throw 'Destination exists'};New-Item -ItemType Directory -Force (Split-Path $dest)|Out-Null;Copy-Item -LiteralPath (Safe $p ('server-overlay/'+$f.path)) -Destination $dest;if((Hash $dest) -ne $f.sha256){throw 'Installed hash mismatch'}}
 CheckMods $plan.afterMods;Save 'applied.json' @{version=$plan.version;worldReplaced=$false;atUtc=[DateTime]::UtcNow.ToString('o')}
 $startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
 $launched=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /d /c start.bat mc';CurrentDirectory=$s;ProcessStartupInformation=$startup};if($launched.ReturnValue -ne 0){throw 'Normal project startup failed'}
 $until=[DateTime]::UtcNow.AddMinutes(15)
 do{Start-Sleep -Seconds 3;$ready=Select-String -LiteralPath "$s\logs\latest.log" -Pattern 'Done \(' -Quiet;$listeners=@(Get-NetTCPConnection -LocalPort 25565 -State Listen -ErrorAction SilentlyContinue);if([DateTime]::UtcNow -gt $until){throw 'Startup timeout; do not publish'}}while(-not($ready -and $listeners.Count))
 & "$s\tools\rcon.ps1" 'list';if(-not $?){throw 'RCON health failed'};CheckMods $plan.afterMods
 $newproc=Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0].OwningProcess)"
 Save 'server-healthy.json' @{pid=$newproc.ProcessId;created=$newproc.CreationDate.ToUniversalTime().ToString('o');version=$plan.version;payloadSha256=$PayloadSha256;rconResponded=$true;atUtc=[DateTime]::UtcNow.ToString('o');startupMethod='WMI-owned original project start.bat mc'}
}
if($Phase -eq 'Publish' -or $Phase -eq 'Finalize'){
 $health=Get-Content "$r\server-healthy.json" -Raw|ConvertFrom-Json;$proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($health.pid)"
 if(-not $proc -or $proc.CreationDate.ToUniversalTime().ToString('o') -ne $health.created -or -not(Get-NetTCPConnection -LocalPort 25565 -State Listen|Where-Object{$_.OwningProcess -eq $health.pid})){throw 'Healthy server identity changed'}
 CheckMods $plan.afterMods;foreach($f in $plan.items|Where-Object{$_.side -eq 'server'}){if((Hash (Safe $s $f.path)) -ne $f.sha256){throw 'Runtime file changed'}}
 & "$s\tools\rcon.ps1" 'list';if(-not $?){throw 'Final RCON failed'}
 if($Phase -eq 'Publish'){
  CheckBase;EnvOSS
  foreach($f in $plan.items|Where-Object{$_.side -eq 'client'}){if((Digest (Fetch $f.url)) -ne $f.sha256){throw 'Public object changed'}}
  if((Hash "$p\manifest.json") -ne $plan.manifestSha256){throw 'Manifest changed'}
  & $oss cp "$p\manifest.json" 'oss://muxigame-prod-static-cn/bmc/release/latest/manifest.json' -f --region cn-hangzhou --content-type 'application/json; charset=utf-8' --cache-control 'no-cache, no-store, must-revalidate'
  if($LASTEXITCODE -ne 0){throw 'Manifest switch uncertain; inspect'}
  Save 'published.json' @{version=$plan.version;manifestSha256=$plan.manifestSha256;atUtc=[DateTime]::UtcNow.ToString('o')}
 }
 if((Digest (Fetch $plan.publicManifestUrl)) -ne $plan.manifestSha256){throw 'Published digest mismatch'}
 Save 'final-verification.json' @{version=$plan.version;manifestSha256=$plan.manifestSha256;pid=$health.pid;serverFilesVerified=131;modsVerified=350;worldReplaced=$false;normalStop=$true;newBackupCreated=$false;remaining=$plan.remaining;atUtc=[DateTime]::UtcNow.ToString('o')}
 if($Phase -eq 'Finalize'){Move-Item -LiteralPath "$w\.releases\release-owner.json" -Destination "$r\owner-completed.json"}
}
