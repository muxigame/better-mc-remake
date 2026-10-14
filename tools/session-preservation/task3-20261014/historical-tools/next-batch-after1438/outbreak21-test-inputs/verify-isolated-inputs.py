"""Read-only full input check before startup. Never installs or starts anything."""
import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--server',type=Path,required=True);p.add_argument('--client',type=Path,required=True);p.add_argument('--qa-mod',action='append',default=[],help='Explicit QA-only JAR filename allowed in addition to locked product mods');a=p.parse_args()
d=json.loads(Path(__file__).with_name('input-lock.json').read_text(encoding='utf-8'));errors=[];counts={}
for side,root,rows,alg in [('server',a.server,[{'path':'mods/'+x['name'],'digest':x['sha256']} for x in d['serverMods']],'sha256'),('client',a.client,[{'path':x['path'],'digest':x['sha1']} for x in d['clientFiles']],'sha1')]:
 expected={x['path'] for x in rows if x['path'].startswith('mods/') and x['path'].endswith('.jar')}
 actual={'mods/'+f.name for f in (root/'mods').glob('*.jar')};allowed={'mods/'+x for x in a.qa_mod}
 errors.extend(side+': unexpected '+x for x in sorted(actual-expected-allowed))
 for x in rows:
  f=root/x['path']
  if not f.is_file():errors.append(side+': missing '+x['path']);continue
  with f.open('rb') as stream:got=hashlib.file_digest(stream,alg).hexdigest()
  if got!=x['digest']:errors.append(side+': mismatch '+x['path'])
 counts[side]=len(rows)
out={'passed':not errors,'checked':counts,'errors':errors,'explicitQaMods':a.qa_mod,'scope':'full client9257 entries and server350 product mods; pre-start only; no runtime/model/world mutation'}
print(json.dumps(out,ensure_ascii=False,indent=2));raise SystemExit(0 if not errors else 1)
