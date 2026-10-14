param([ValidateSet('Stage','Activate','Publish','Finalize')][string]$Phase,[string]$PayloadSha256)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
$w='C:\Users\Administrator\WorkSpace\muxigame';$s="$w\bmc5server";$r="$w\.releases\production1434-race5"
function Hash($p){(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()}
function Save($n,$v){$v|ConvertTo-Json -Depth 10|Set-Content -LiteralPath "$r\$n" -Encoding UTF8}
function Safe($root,$rel){$p=[IO.Path]::GetFullPath((Join-Path $root $rel));if(-not $p.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escape'};$p}
function Fetch($url){$c=New-Object Net.WebClient;try{$c.Headers['Cache-Control']='no-cache';return ,$c.DownloadData($url)}finally{$c.Dispose()}}
function Digest($b){([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))).Replace('-','').ToLowerInvariant()}
function EnvOSS{foreach($line in Get-Content -LiteralPath "$w\better-mc-remake\.env"){$t=$line.Trim();if(-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')){continue};if($t.StartsWith('export ')){$t=$t.Substring(7).Trim()};$a=$t.Split('=',2);[Environment]::SetEnvironmentVariable($a[0].Trim(),$a[1].Trim().Trim('"').Trim("'"),'Process')};$c=Get-Command ossutil -ErrorAction SilentlyContinue;if($c){$script:oss=$c.Path}else{$script:oss=Join-Path $env:LOCALAPPDATA 'Programs\ossutil\ossutil-2.4.0-windows-amd64\ossutil.exe'}}

$p="$r\payload";$plan=Get-Content "$p\execution-plan.json" -Raw -Encoding UTF8|ConvertFrom-Json
$expected='890fec14ed13ac97651e2588215713712b3f51b7742853e392d697831a632710'
if((Hash "$r\payload.zip") -ne $expected){throw 'Rollback payload mismatch'}
if((Hash "$r\rollback-manifest-1.4.33.json") -ne $plan.baseManifestSha256){throw 'Rollback manifest mismatch'}
$owner=Get-Content "$w\.releases\release-owner.json" -Raw|ConvertFrom-Json
if($owner.owner -ne 'release1434-successor-20261011' -or $owner.payloadSha256 -ne $expected){throw 'Rollback owner mismatch'}
$active=@(Get-CimInstance Win32_Process -Filter "Name='java.exe'"|Where-Object{$_.CommandLine -like '*neoforge*win_args.txt*'})
if($active.Count -gt 1){throw 'Ambiguous running JVM'}
if($active.Count -eq 1){& "$s\tools\rcon.ps1" 'save-all flush' 'stop';if(-not $?){throw 'Normal rollback stop unavailable; never force kill'};$until=[DateTime]::UtcNow.AddMinutes(10);while(Get-Process -Id $active[0].ProcessId -ErrorAction SilentlyContinue){if([DateTime]::UtcNow -gt $until){throw 'Rollback stop timeout; no kill'};Start-Sleep -Seconds 2}}
if(Get-NetTCPConnection -LocalPort 25565 -State Listen -ErrorAction SilentlyContinue){throw 'Server remains listening'}
$lock=[IO.File]::Open("$s\world\session.lock",[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$lock.Dispose()
foreach($f in $plan.items|Where-Object{$_.side -eq 'server'}){$src=Safe $s $f.path;if(Test-Path -LiteralPath $src){if((Hash $src) -ne $f.sha256){throw ('Changed candidate requires inspection '+$f.path)};$dst=Safe "$r\failed-overlay" $f.path;if(Test-Path -LiteralPath $dst){throw 'Rollback destination occupied'};New-Item -ItemType Directory -Force (Split-Path $dst)|Out-Null;Move-Item -LiteralPath $src -Destination $dst}}
foreach($f in $plan.remove){$dst=Safe $s $f.path;if(Test-Path -LiteralPath $dst){if((Hash $dst) -ne $f.sha256){throw 'Existing old mod drift'}}else{$src=Safe "$r\retired-mods" $f.path;if((Hash $src) -ne $f.sha256){throw 'Retired original missing or changed'};Move-Item -LiteralPath $src -Destination $dst}}
$mods=@(Get-ChildItem -LiteralPath "$s\mods" -Filter *.jar -File);if($mods.Count -ne $plan.beforeMods.Count){throw 'Rollback mod count mismatch'}
foreach($f in $plan.beforeMods){if((Hash (Safe $s ('mods/'+$f.name))) -ne $f.sha256){throw ('Rollback mod hash mismatch '+$f.name)}}
$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
$launched=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /d /c start.bat mc';CurrentDirectory=$s;ProcessStartupInformation=$startup};if($launched.ReturnValue -ne 0){throw 'Rollback startup failed'}
$until=[DateTime]::UtcNow.AddMinutes(15)
do{Start-Sleep -Seconds 3;$ready=Select-String -LiteralPath "$s\logs\latest.log" -Pattern 'Done \(' -Quiet;$listeners=@(Get-NetTCPConnection -LocalPort 25565 -State Listen -ErrorAction SilentlyContinue);if([DateTime]::UtcNow -gt $until){throw 'Rollback startup timeout'}}while(-not($ready -and $listeners.Count))
& "$s\tools\rcon.ps1" 'list';if(-not $?){throw 'Rollback RCON failed'}
$current=Digest (Fetch $plan.publicManifestUrl)
if($current -eq $plan.manifestSha256){EnvOSS;& $oss cp "$r\rollback-manifest-1.4.33.json" 'oss://muxigame-prod-static-cn/bmc/release/latest/manifest.json' -f --region cn-hangzhou --content-type 'application/json; charset=utf-8' --cache-control 'no-cache, no-store, must-revalidate';if($LASTEXITCODE -ne 0){throw 'Rollback manifest update uncertain'}}elseif($current -ne $plan.baseManifestSha256){throw 'Unexpected public manifest; do not overwrite'}
if((Digest (Fetch $plan.publicManifestUrl)) -ne $plan.baseManifestSha256){throw 'Rollback public verification failed'}
Save 'rolled-back.json' @{version='1.4.33';modsVerified=350;worldReplaced=$false;newBackupCreated=$false;atUtc=[DateTime]::UtcNow.ToString('o')}
