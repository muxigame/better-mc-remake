from remote import remote
from pathlib import Path
import json,urllib.request
code=r"""$ErrorActionPreference='Stop';$w='C:\Users\Administrator\WorkSpace\muxigame'
function EnvOSS{foreach($line in Get-Content -LiteralPath "$w\better-mc-remake\.env"){$t=$line.Trim();if(-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')){continue};if($t.StartsWith('export ')){$t=$t.Substring(7).Trim()};$a=$t.Split('=',2);[Environment]::SetEnvironmentVariable($a[0].Trim(),$a[1].Trim().Trim('"').Trim("'"),'Process')};$c=Get-Command ossutil -ErrorAction SilentlyContinue;if($c){$script:oss=$c.Path}else{$script:oss=Join-Path $env:LOCALAPPDATA 'Programs\ossutil\ossutil-2.4.0-windows-amd64\ossutil.exe'}}
EnvOSS
$listing=@(& $oss ls 'oss://muxigame-prod-static-cn/bmc/release/releases/1.4.37' --region cn-hangzhou)
if($LASTEXITCODE -ne 0){throw 'Version namespace read failed'}
@{versionPrefixListing=$listing;localVersionRecordExists=(Test-Path "$w\better-mc-remake\pack\releases\1.4.37.json");packSpecVersion=((Get-Content "$w\better-mc-remake\pack\packspec.json" -Raw -Encoding UTF8|ConvertFrom-Json).pack.version)}|ConvertTo-Json -Depth 5
"""
r=json.loads(remote(code));Path(__file__).with_name('version-availability.json').write_text(json.dumps(r,indent=2));print(json.dumps(r))
s=json.load(urllib.request.urlopen('https://mc.muxigame.com/api/v1/site',timeout=30));Path(__file__).with_name('api-site-baseline.json').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8');print('siteKeys',list(s))

