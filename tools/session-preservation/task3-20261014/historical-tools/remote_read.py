from pathlib import Path
import subprocess,base64,json
s=Path(r'C:/Users/ranzh/Documents/Codex/2026-10-09/task-3/new-release/preflight.ps1').read_text(encoding='utf-8')
w="& ([scriptblock]::Create([Console]::In.ReadToEnd()))"
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','administrator@<PRIVATE_HOST>','powershell','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(w.encode('utf-16-le')).decode()],input=s.encode('utf-8'),capture_output=True,timeout=180)
if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-1500:])
r=json.loads(p.stdout.decode('utf-8-sig'));Path('production-preflight.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in r.items() if k not in ['models','mods']},ensure_ascii=False));print('mods',len(r['mods']),'model files',len(r['models']))
