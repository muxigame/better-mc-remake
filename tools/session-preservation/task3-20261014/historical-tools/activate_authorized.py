from pathlib import Path
import subprocess,base64,hashlib,json
N=Path(__file__).resolve().parent/'release1434-revised'
remote=r'C:\Users\Administrator\WorkSpace\muxigame\.releases\production1434-race5'
ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','administrator@<PRIVATE_HOST>']
def run(code):
 p=subprocess.run(ssh+['powershell','-NoProfile','-EncodedCommand',base64.b64encode(code.encode('utf-16-le')).decode()],capture_output=True,timeout=40)
 if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-1500:])
 return p.stdout.decode('utf-8-sig')
pre=r"$ErrorActionPreference='Stop';$r='"+remote+r"';$o=Get-Content 'C:\Users\Administrator\WorkSpace\muxigame\.releases\release-owner.json' -Raw|ConvertFrom-Json;if($o.owner -ne 'release1434-successor-20261011' -or $o.payloadSha256 -ne '890fec14ed13ac97651e2588215713712b3f51b7742853e392d697831a632710'){throw 'Owner drift'};if(Test-Path \"$r\applied.json\"){throw 'Already activated; inspect'};if((Get-FileHash \"$r\remote-release.ps1\" -Algorithm SHA256).Hash.ToLowerInvariant() -ne '8d0700e6a2d798d0ce3403f324153e3d011bd92c39213ae39bbd66291982537c'){throw 'Original script drift'}"
pre=pre.replace('\\"','"');run(pre)
for local,name in [('remote-release.ps1','remote-release.no-backup.ps1'),('rollback-existing.ps1','rollback-existing.ps1'),('rollback-manifest-1.4.33.json','rollback-manifest-1.4.33.json'),('no-backup-authorization.json','no-backup-authorization.json')]:
 subprocess.run(['scp','-o','BatchMode=yes',str(N/local),'administrator@<PRIVATE_HOST>:'+remote.replace('\\','/')+'/'+name],check=True)
 digest=hashlib.sha256((N/local).read_bytes()).hexdigest()
 run("$ErrorActionPreference='Stop';if((Get-FileHash -LiteralPath '"+remote+'\\'+name+"' -Algorithm SHA256).Hash.ToLowerInvariant() -ne '"+digest+"'){throw 'Uploaded file drift'}")
print('No-backup execution and existing rollback materials verified; original staged script preserved')
with (N/'activate-output.log').open('w',encoding='utf-8') as out:
 p=subprocess.run(['python','-X','utf8',str(N/'controller.py'),'Activate','--backup-policy','none'],stdout=out,stderr=subprocess.STDOUT)
print('ActivateExit',p.returncode)
raise SystemExit(p.returncode)
