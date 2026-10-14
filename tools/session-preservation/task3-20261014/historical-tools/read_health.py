from pathlib import Path
import subprocess,base64,json
s=r'''$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$w='C:\Users\Administrator\WorkSpace\muxigame';$r="$w\.releases\production1434";Get-Content "$w\.releases\release-owner.json" -Raw;Get-Content "$r\staged.json" -Raw;& "$w\bmc5server\tools\rcon.ps1" 'list' '''
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','administrator@<PRIVATE_HOST>','powershell','-NoProfile','-EncodedCommand',base64.b64encode(s.encode('utf-16-le')).decode()],capture_output=True,timeout=40);print(p.stdout.decode('utf-8-sig'));print('exit',p.returncode)
Path('production-lease-health.txt').write_text(p.stdout.decode('utf-8-sig'),encoding='utf-8')
