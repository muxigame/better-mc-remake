from pathlib import Path
import subprocess,base64
SSH=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','administrator@<PRIVATE_HOST>']
def remote(code,timeout=300):
 wrapper="& ([scriptblock]::Create([Console]::In.ReadToEnd()))"
 p=subprocess.run(SSH+['powershell','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(wrapper.encode('utf-16-le')).decode()],input=code.encode('utf-8'),capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-3000:])
 return p.stdout.decode('utf-8-sig')
