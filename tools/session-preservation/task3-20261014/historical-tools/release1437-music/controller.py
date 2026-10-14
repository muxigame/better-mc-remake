from pathlib import Path
import argparse,json,hashlib,subprocess,base64
from remote import remote,SSH
N=Path(__file__).resolve().parent
R=r'C:\Users\Administrator\WorkSpace\muxigame\.releases\production1437-music'
a=argparse.ArgumentParser();a.add_argument('phase',choices=['Stage','Activate','Publish','Finalize','Rollback','Status']);args=a.parse_args()
if args.phase in {'Stage','Activate','Publish','Finalize'}:
 gate=json.loads((N/'formal-release-policy.json').read_text(encoding='utf-8'))
 if gate['candidateStatus']!='VALIDATED_FOR_DAILY_UID_POLICY':
  raise SystemExit('Release blocked: replace superseded cumulative-budget candidate with validated daily-UID production candidate')
plan=json.loads((N/'execution-plan.json').read_text(encoding='utf-8'))
for f in plan['items']:assert hashlib.sha256((N/(f['side']+'-overlay')/f['path']).read_bytes()).hexdigest()==f['sha256']
assert hashlib.sha256((N/'manifest.json').read_bytes()).hexdigest()==plan['manifestSha256']
digest=hashlib.sha256((N/'payload.zip').read_bytes()).hexdigest();scriptdigest=hashlib.sha256((N/'remote-release.ps1').read_bytes()).hexdigest()
if args.phase=='Status':
 code="$ErrorActionPreference='Stop';$r='"+R+"';$o=@{};foreach($n in @('staged','maintenance-notice','stop-request','stopped','applied','starting','server-healthy','published','final-verification','rolled-back')){if(Test-Path (Join-Path $r ($n+'.json'))){$o[$n]=Get-Content (Join-Path $r ($n+'.json')) -Raw|ConvertFrom-Json}};$o|ConvertTo-Json -Depth 12"
 raw=remote(code);(N/'last-status.json').write_text(raw,encoding='utf-8');print(raw);raise SystemExit()
if args.phase=='Stage':
 remote("$ErrorActionPreference='Stop';if(Test-Path -LiteralPath '"+R+"'){throw 'Release root exists; inspect'};New-Item -ItemType Directory -Path '"+R+"'|Out-Null")
 for name in ['payload.zip','remote-release.ps1']:
  subprocess.run(['scp','-o','BatchMode=yes',str(N/name),'administrator@<PRIVATE_HOST>:'+R.replace('\\','/')+'/'+name],check=True)
code="$ErrorActionPreference='Stop';if((Get-FileHash -LiteralPath '"+R+"\\remote-release.ps1' -Algorithm SHA256).Hash.ToLowerInvariant() -ne '"+scriptdigest+"'){throw 'Release script drift'};& '"+R+"\\remote-release.ps1' -Phase "+args.phase+" -PayloadSha256 '"+digest+"'"
wrapper="& ([scriptblock]::Create([Console]::In.ReadToEnd()))"
with (N/(args.phase.lower()+'-output.log')).open('w',encoding='utf-8') as log:
 p=subprocess.run(SSH+['powershell','-NoProfile','NonInteractive' if False else '-NonInteractive','-EncodedCommand',base64.b64encode(wrapper.encode('utf-16-le')).decode()],input=code.encode('utf-8'),stdout=log,stderr=subprocess.STDOUT,timeout=3000)
print(args.phase+' exit '+str(p.returncode));raise SystemExit(p.returncode)
