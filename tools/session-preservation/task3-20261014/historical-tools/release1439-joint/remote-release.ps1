param([ValidateSet('Stage','Activate','Publish','Finalize','Rollback')][string]$Phase,[string]$PayloadSha256)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$w='C:\Users\Administrator\WorkSpace\muxigame';$s="$w\bmc5server";$r="$w\.releases\production1439-joint";$op="$w\.releases\release-owner.json"
function Hash($p){(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()}
function Save($n,$v){$v|ConvertTo-Json -Depth 12|Set-Content -LiteralPath "$r\$n" -Encoding UTF8}
function Safe($root,$rel){$p=[IO.Path]::GetFullPath((Join-Path $root $rel));if(-not $p.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escape'};$p}
function Fetch($url){$c=New-Object Net.WebClient;try{$c.Headers['Cache-Control']='no-cache';return ,$c.DownloadData($url)}finally{$c.Dispose()}}
function Digest($b){([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))).Replace('-','').ToLowerInvariant()}
function EnvOSS{foreach($line in Get-Content -LiteralPath "$w\better-mc-remake\.env"){$t=$line.Trim();if(-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')){continue};if($t.StartsWith('export ')){$t=$t.Substring(7).Trim()};$a=$t.Split('=',2);[Environment]::SetEnvironmentVariable($a[0].Trim(),$a[1].Trim().Trim('"').Trim("'"),'Process')};$c=Get-Command ossutil -ErrorAction SilentlyContinue;if($c){$script:oss=$c.Path}else{$script:oss=Join-Path $env:LOCALAPPDATA 'Programs\ossutil\ossutil-2.4.0-windows-amd64\ossutil.exe'}}
if($env:COMPUTERNAME -ne 'JBY-008'){throw 'Wrong production machine'}
if((Hash "$r\payload.zip") -ne $PayloadSha256){throw 'Payload drift'}
if($Phase -eq 'Stage'){if(Test-Path "$r\payload"){throw 'Already staged; inspect'};Expand-Archive -LiteralPath "$r\payload.zip" -DestinationPath "$r\payload"}
$p="$r\payload";$plan=Get-Content "$p\execution-plan.json" -Raw -Encoding UTF8|ConvertFrom-Json
if($plan.version -ne '1.4.39' -or $plan.productionRoot -ne $s -or $plan.host -ne $env:COMPUTERNAME){throw 'Target identity mismatch'}
function CheckMods($rows){$a=@(Get-ChildItem -LiteralPath "$s\mods" -Filter *.jar -File);if($a.Count -ne $rows.Count){throw 'Mod count drift'};foreach($f in $rows){if((Hash (Safe $s ('mods/'+$f.name))) -ne $f.sha256){throw ('Mod drift '+$f.name)}}}
function CheckModels{foreach($f in $plan.models){if((Hash (Safe $s $f.path)) -ne $f.sha256){throw ('Existing YSM changed '+$f.path)}}}
function CheckBase{if((Digest (Fetch $plan.publicManifestUrl)) -ne $plan.baseManifestSha256){throw 'Live manifest drift'}}
function CheckPayload{foreach($f in $plan.items){if((Hash (Safe $p ($f.side+'-overlay/'+$f.path))) -ne $f.sha256){throw 'Staged product drift'}};if((Hash "$p\manifest.json") -ne $plan.manifestSha256 -or (Hash "$p\baseline-manifest.json") -ne $plan.baseManifestSha256){throw 'Manifest payload drift'}}
function CheckStopped{if(Get-NetTCPConnection -LocalPort 25565 -State Listen -ErrorAction SilentlyContinue){throw 'Still listening'};if(Get-CimInstance Win32_Process -Filter "Name='java.exe'"|Where-Object{$_.CommandLine -like '*neoforge*win_args.txt*'}){throw 'Server JVM remains; never force kill'};$lock=[IO.File]::Open("$s\world\session.lock",[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None);$lock.Dispose()}
function CheckOwner{$owner=Get-Content $op -Raw|ConvertFrom-Json;if($owner.owner -ne 'task3-joint1439' -or $owner.payloadSha256 -ne $PayloadSha256){throw 'Release owner mismatch'}}
function Rcon([string[]]$commands){& "$s\tools\rcon.ps1" @commands;if(-not $?){throw 'RCON command failed'}}
function NormalStop{
 $ids=@(Get-NetTCPConnection -LocalPort 25565 -State Listen|Select-Object -ExpandProperty OwningProcess -Unique);if($ids.Count -ne 1){throw 'Ambiguous production listener'}
 $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($ids[0])";if($proc.Name -ne 'java.exe' -or $proc.CommandLine -notlike '*neoforge*win_args.txt*'){throw 'Wrong production process'}
 Save 'stop-request.json' @{pid=$proc.ProcessId;created=$proc.CreationDate.ToUniversalTime().ToString('o');atUtc=[DateTime]::UtcNow.ToString('o')}
 Rcon @('save-all flush','stop')
 $until=[DateTime]::UtcNow.AddMinutes(10);while(Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue){if([DateTime]::UtcNow -gt $until){throw 'Normal shutdown timed out; no kill'};Start-Sleep -Seconds 2};CheckStopped
 Save 'stopped.json' @{oldPid=$proc.ProcessId;worldLockExclusive=$true;atUtc=[DateTime]::UtcNow.ToString('o')}
}
function StartHealthy($version,$mods){
 CheckStopped;$start=[DateTime]::UtcNow
 $startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
 $launched=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /d /c start.bat mc';CurrentDirectory=$s;ProcessStartupInformation=$startup};if($launched.ReturnValue -ne 0){throw 'Original project startup failed'}
 Save 'starting.json' @{version=$version;atUtc=$start.ToString('o');launcherPid=$launched.ProcessId}
 $until=[DateTime]::UtcNow.AddMinutes(15)
 do{Start-Sleep -Seconds 3;$ready=$false;if(Test-Path "$s\logs\latest.log"){$ready=((Get-Item "$s\logs\latest.log").LastWriteTimeUtc -ge $start) -and (Select-String -LiteralPath "$s\logs\latest.log" -Pattern 'Done \(' -Quiet)};$listeners=@(Get-NetTCPConnection -LocalPort 25565 -State Listen -ErrorAction SilentlyContinue);if([DateTime]::UtcNow -gt $until){throw 'Startup timeout; do not publish'}}while(-not($ready -and $listeners.Count))
 $np=Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0].OwningProcess)";if($np.CreationDate.ToUniversalTime() -lt $start){throw 'Stale process identity'}
 Rcon @('list');$first=(Rcon @('time query gametime')|Out-String);Start-Sleep -Seconds 3;$second=(Rcon @('time query gametime')|Out-String)
 $a=[regex]::Matches($first,'\d+');$b=[regex]::Matches($second,'\d+');if(-not $a.Count -or -not $b.Count -or [long]$b[$b.Count-1].Value -le [long]$a[$a.Count-1].Value){throw 'World tick progression not verified'}
 CheckMods $mods;CheckModels
 Save 'server-healthy.json' @{version=$version;pid=$np.ProcessId;created=$np.CreationDate.ToUniversalTime().ToString('o');atUtc=[DateTime]::UtcNow.ToString('o');rconResponded=$true;gameTimeBefore=$a[$a.Count-1].Value;gameTimeAfter=$b[$b.Count-1].Value;modsVerified=$mods.Count;newBackupCreated=$false}
}
foreach($f in $plan.startup){if((Hash (Safe $s $f.path)) -ne $f.sha256){throw 'Startup script drift'}}
CheckPayload
if($Phase -eq 'Stage'){
 CheckBase;CheckMods $plan.beforeMods;CheckModels
 if(Test-Path $op){throw 'Another release owner exists'}
 EnvOSS;$listing=@(& $oss ls ('oss://muxigame-prod-static-cn/'+$plan.objectPrefix) --region cn-hangzhou)
 if($LASTEXITCODE -ne 0 -or -not (($listing -join "`n") -match 'Object Number is: 0')){throw 'Version namespace occupied or unavailable'}
 $lease=@{owner='task3-joint1439';version='1.4.39';payloadSha256=$PayloadSha256;createdUtc=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json
 $fs=[IO.File]::Open($op,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$bytes=[Text.Encoding]::UTF8.GetBytes($lease);$fs.Write($bytes,0,$bytes.Length)}finally{$fs.Dispose()}
 foreach($f in $plan.items|Where-Object{$_.side -eq 'client'}){& $oss cp (Safe "$p\client-overlay" $f.path) ('oss://muxigame-prod-static-cn/'+$plan.objectPrefix+'files/'+$f.path) --region cn-hangzhou --content-type 'application/octet-stream' --cache-control 'public, max-age=31536000, immutable';if($LASTEXITCODE -ne 0){throw 'Immutable object upload failed'}}
 foreach($f in $plan.items|Where-Object{$_.side -eq 'client'}){if((Digest (Fetch $f.url)) -ne $f.sha256){throw 'Staged public object hash mismatch'}}
 & $oss cp "$p\manifest.json" ('oss://muxigame-prod-static-cn/'+$plan.objectPrefix+'manifest.json') --region cn-hangzhou --content-type 'application/json; charset=utf-8' --cache-control 'public, max-age=31536000, immutable';if($LASTEXITCODE -ne 0){throw 'Immutable manifest upload failed'}
 CheckOwner;Save 'staged.json' @{version=$plan.version;objectsVerified=2;publicPointerChanged=$false;atUtc=[DateTime]::UtcNow.ToString('o')};exit
}
CheckOwner
if($Phase -eq 'Activate'){
 CheckBase;CheckMods $plan.beforeMods;CheckModels;if(-not(Test-Path "$r\staged.json")){throw 'No stage receipt'};if(Test-Path "$r\applied.json"){throw 'Already activated; inspect'}
 if((Get-PSDrive C).Free -lt 1GB){throw 'Insufficient disk headroom'}
 foreach($f in $plan.remove){if((Hash (Safe $s $f.path)) -ne $f.sha256){throw 'Rollback original changed'}}
 Rcon @('say Maintenance in 120 seconds for pack 1.4.39. Please disconnect safely and update after completion.')
 $notice=[DateTime]::UtcNow;Save 'maintenance-notice.json' @{atUtc=$notice.ToString('o');minimumWaitSeconds=120;backupPolicy='none-explicit-user';newBackupCreated=$false}
 while([DateTime]::UtcNow -lt $notice.AddSeconds(120)){Start-Sleep -Seconds 2}
 NormalStop;CheckMods $plan.beforeMods
 foreach($f in $plan.remove){$src=Safe $s $f.path;$dst=Safe "$r\retired-mods" $f.path;New-Item -ItemType Directory -Force (Split-Path $dst)|Out-Null;if(Test-Path $dst){throw 'Retired target occupied'};Move-Item -LiteralPath $src -Destination $dst}
 foreach($f in $plan.items|Where-Object{$_.side -eq 'server'}){$dst=Safe $s $f.path;if(Test-Path $dst){throw 'Install target occupied'};Copy-Item -LiteralPath (Safe $p ('server-overlay/'+$f.path)) -Destination $dst;if((Hash $dst) -ne $f.sha256){throw 'Installed file hash mismatch'}}
 CheckMods $plan.afterMods;Save 'applied.json' @{version=$plan.version;atUtc=[DateTime]::UtcNow.ToString('o');worldReplaced=$false;newBackupCreated=$false}
 StartHealthy $plan.version $plan.afterMods;exit
}
if($Phase -eq 'Rollback'){
 $current=Digest (Fetch $plan.publicManifestUrl);if($current -ne $plan.baseManifestSha256 -and $current -ne $plan.manifestSha256){throw 'Unknown public manifest; rollback requires inspection'}
 if(Get-NetTCPConnection -LocalPort 25565 -State Listen -ErrorAction SilentlyContinue){NormalStop}else{CheckStopped}
 foreach($f in $plan.items|Where-Object{$_.side -eq 'server'}){$src=Safe $s $f.path;if(Test-Path $src){$h=Hash $src;if($h -eq $f.sha256){$dst=Safe "$r\failed-overlay" $f.path;New-Item -ItemType Directory -Force (Split-Path $dst)|Out-Null;Move-Item -LiteralPath $src -Destination $dst}elseif(-not($plan.remove|Where-Object{$_.path -eq $f.path -and $_.sha256 -eq $h})){throw 'Unknown installed rollback bytes'}}}
 foreach($f in $plan.remove){$dst=Safe $s $f.path;if(Test-Path $dst){if((Hash $dst) -ne $f.sha256){throw 'Old file mismatch'}}else{$src=Safe "$r\retired-mods" $f.path;if((Hash $src) -ne $f.sha256){throw 'Existing rollback bytes missing'};Move-Item -LiteralPath $src -Destination $dst}}
 StartHealthy $plan.baseVersion $plan.beforeMods
 if($current -eq $plan.manifestSha256){EnvOSS;& $oss cp "$p\baseline-manifest.json" 'oss://muxigame-prod-static-cn/bmc/release/latest/manifest.json' -f --region cn-hangzhou --content-type 'application/json; charset=utf-8' --cache-control 'no-cache, no-store, must-revalidate';if($LASTEXITCODE -ne 0){throw 'Rollback pointer uncertain'}}
 if((Digest (Fetch $plan.publicManifestUrl)) -ne $plan.baseManifestSha256){throw 'Rollback manifest not verified'}
 Save 'rolled-back.json' @{version=$plan.baseVersion;atUtc=[DateTime]::UtcNow.ToString('o');newBackupCreated=$false};exit
}
$health=Get-Content "$r\server-healthy.json" -Raw|ConvertFrom-Json;$proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($health.pid)"
if($health.version -ne $plan.version -or -not $proc -or $proc.CreationDate.ToUniversalTime().ToString('o') -ne $health.created){throw 'Healthy production identity changed'}
CheckMods $plan.afterMods;CheckModels;Rcon @('list')
if($Phase -eq 'Publish'){
 CheckBase;EnvOSS;foreach($f in $plan.items|Where-Object{$_.side -eq 'client'}){if((Digest (Fetch $f.url)) -ne $f.sha256){throw 'Public object drift'}}
 & $oss cp "$p\manifest.json" 'oss://muxigame-prod-static-cn/bmc/release/latest/manifest.json' -f --region cn-hangzhou --content-type 'application/json; charset=utf-8' --cache-control 'no-cache, no-store, must-revalidate';if($LASTEXITCODE -ne 0){throw 'Manifest switch uncertain; inspect'}
 Save 'published.json' @{version=$plan.version;manifestSha256=$plan.manifestSha256;atUtc=[DateTime]::UtcNow.ToString('o')}
}
if((Digest (Fetch $plan.publicManifestUrl)) -ne $plan.manifestSha256){throw 'Public manifest verification failed'}
Save 'final-verification.json' @{version=$plan.version;manifestSha256=$plan.manifestSha256;pid=$health.pid;modsVerified=350;worldReplaced=$false;newBackupCreated=$false;atUtc=[DateTime]::UtcNow.ToString('o')}
if($Phase -eq 'Finalize'){Move-Item -LiteralPath $op -Destination "$r\owner-completed.json"}
