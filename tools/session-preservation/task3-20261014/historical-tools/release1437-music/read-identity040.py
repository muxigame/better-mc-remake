from pathlib import Path
import subprocess,json
N=Path(__file__).resolve().parent
source=r'''import pathlib,json,os,sqlite3,hashlib,urllib.request,ast
root=pathlib.Path('/app');main=root/'app/main.py';data=pathlib.Path(os.environ.get('MUXI_DATABASE_PATH','data/muxi-auth.db'));data=data if data.is_absolute() else root/data
out={'mainSha256':hashlib.sha256(main.read_bytes()).hexdigest(),'musicModules':sorted(p.name for p in (root/'app').glob('music*.py')),'productionMusicEnable':os.environ.get('MUSIC_OSS_ENABLE','0'),'databaseExists':data.is_file(),'credentialValuesOutput':False,'writes':False}
tree=ast.parse(main.read_text());names=set()
for n in ast.walk(tree):
 if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store):names.add(n.id)
out['requiredMainExports']={n:n in names for n in ['app','store','signer','settings','terminal_access']}
if data.is_file():
 db=sqlite3.connect('file:'+str(data)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 # Only public account mapping fields; never tokens/password/email/session secrets.
 out['qaUidActiveBindings']=db.execute("SELECT COUNT(*) FROM terminal_access_connections c JOIN accounts a ON a.id=c.account_id JOIN access_tokens t ON t.token_hash=c.source_hash AND t.account_id=c.account_id WHERE a.uid=10000 AND t.expires_at>strftime('%Y-%m-%dT%H:%M:%fZ','now') AND t.revoked_at IS NULL").fetchone()[0];db.close()
try:
 with urllib.request.urlopen('http://127.0.0.1:9000/healthz',timeout=5) as r:out['healthStatus']=r.status
except Exception:out['healthStatus']='unavailable'
print(json.dumps(out,ensure_ascii=False))
'''
hop=r'C:\Windows\System32\OpenSSH\ssh.exe -T -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes root@<PRIVATE_HOST> docker exec -i muxi-auth-muxi-auth-1 python -'
r=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','administrator@<PRIVATE_HOST>',hop],input=source.encode(),capture_output=True,timeout=45)
if r.returncode:raise RuntimeError('Existing maintenance hop failed: '+r.stderr.decode(errors='replace')[-1200:])
o=json.loads(r.stdout.decode('utf-8-sig'));(N/'backend040-identity-readiness.json').write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(o,ensure_ascii=False,indent=2))
