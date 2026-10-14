"""Read only: show the latest marked horse lab without interacting with Minecraft."""
from pathlib import Path
import argparse,json
p=argparse.ArgumentParser()
p.add_argument('--output-root',type=Path,default=Path(__file__).resolve().parents[2]/'build/qa/unified')
a=p.parse_args()
def read(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return None
info=read(a.output_root/'horse-unified-latest.json')
if not info:raise SystemExit('No horse lab manifest yet')
lab=Path(info['lab']);control=Path(info['control'])
print(json.dumps({'lab':str(lab),'control':str(control),'port':info['port']}))
for role in ('server','host','guest'):
    status=read(lab/'coordinator'/('status-'+role+'.json')) or {}
    own=read(lab/'coordinator'/('horse-status-'+role+'.json')) or {}
    keys=('tick','players','connected','screen','dimension','glRenderer')
    print(json.dumps({'role':role,**{k:status[k] for k in keys if k in status},**own}))
    log=lab/role/'boot.log'
    if log.exists():print('\n'.join(log.read_text(errors='replace').splitlines()[-3:]))
for name in ('scenario-result.json','summary.json'):
    value=read(control/name)
    if value:print(json.dumps({name:{k:value[k] for k in ('success','error','checks') if k in value}}))
result=read(lab/'run-result.json')
print(json.dumps({'runtime':result or 'pending'}))
