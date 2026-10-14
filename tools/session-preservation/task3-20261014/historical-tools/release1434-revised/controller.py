from pathlib import Path
import argparse, subprocess,base64,hashlib,json
N=Path(__file__).resolve().parent
SSH=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','administrator@<PRIVATE_HOST>']
REMOTE=r'C:\Users\Administrator\WorkSpace\muxigame\.releases\production1434-race5'
def remote(code,timeout=7200):
 wrapper='& ([scriptblock]::Create([Console]::In.ReadToEnd()))'
 return subprocess.run(SSH+['powershell','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(wrapper.encode('utf-16-le')).decode()],input=code.encode('utf-8'),check=True,timeout=timeout)
p=argparse.ArgumentParser();p.add_argument('phase',choices=['Activate','Publish','Finalize']);p.add_argument('--backup-policy',choices=['none']);args=p.parse_args()
if args.phase=='Activate':assert args.backup_policy=='none','Explicit no-backup policy required for this authorized release'
plan=json.loads((N/'execution-plan.json').read_text(encoding='utf-8'))
for row in plan['items']:
 assert hashlib.sha256((N/(row['side']+'-overlay')/row['path']).read_bytes()).hexdigest()==row['sha256']
assert hashlib.sha256((N/'manifest.json').read_bytes()).hexdigest()==plan['manifestSha256']
digest=hashlib.sha256((N/'payload.zip').read_bytes()).hexdigest()
if args.phase in ('Stage','Activate'):
 for label in ['release1434-shutdownfix-smoke1','release1434-recovery-smoke2','release1434-visual-final']:
  result=json.loads((N.parent/(label+('-acceptance-retry.json' if 'recovery' in label else '-acceptance.json'))).read_text(encoding='utf-8'))
  assert result['passed'] and result['completed'],'Merged smoke not passed: '+label
  exitreceipt=json.loads((N.parent/'runtime-tools/build/local-mc-debug'/label/'run-result.json').read_text(encoding='utf-8'))
  assert exitreceipt['normalExit'] and all(x==0 for x in exitreceipt['exitCodes'].values()),'MC shutdown not verified'
 review=json.loads((N.parent/'visual-review.json').read_text(encoding='utf-8'))
 assert review['allSixVerified'],'Six actual model frames not reviewed'
if args.phase=='Stage':
 remote("$ErrorActionPreference='Stop';if(Test-Path -LiteralPath '"+REMOTE+"'){throw 'Release root already exists'};New-Item -ItemType Directory -Path '"+REMOTE+"'|Out-Null")
 for name in ['payload.zip','remote-release.ps1']:
  subprocess.run(['scp','-o','BatchMode=yes',str(N/name),'administrator@<PRIVATE_HOST>:'+REMOTE.replace('\\','/')+'/'+name],check=True)
scriptdigest=hashlib.sha256((N/'remote-release.ps1').read_bytes()).hexdigest()
remote("$ErrorActionPreference='Stop';if((Get-FileHash -LiteralPath '"+REMOTE+"\\remote-release.no-backup.ps1' -Algorithm SHA256).Hash.ToLowerInvariant() -ne '"+scriptdigest+"'){throw 'Release script drift'};& '"+REMOTE+"\\remote-release.no-backup.ps1' -Phase "+args.phase+" -PayloadSha256 '"+digest+"'")
