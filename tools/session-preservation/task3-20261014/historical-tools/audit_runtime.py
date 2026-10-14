from pathlib import Path
import subprocess,base64,json
n=Path('release1434-revised');s=(n/'audit-runtime.ps1').read_text();wrapper='& ([scriptblock]::Create([Console]::In.ReadToEnd()))';p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','administrator@<PRIVATE_HOST>','powershell','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(wrapper.encode('utf-16-le')).decode()],input=s.encode(),capture_output=True,timeout=50)
if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-1200:])
o=json.loads(p.stdout.decode('utf-8-sig'));(n/'runtime-final-audit.json').write_text(json.dumps(o,ensure_ascii=False,indent=2));print(json.dumps(o,ensure_ascii=False))
