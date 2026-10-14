from pathlib import Path
import json,os
r=Path(r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\create-native-14-042813")
o=json.loads((r/'local-mc-owner.json').read_text());assert o['runId']=='5199ab64edb145f5a30972043f34a0de';p=r/'stop-request.json';t=p.with_suffix('.pending');t.write_text(json.dumps({'runId':o['runId'],'normalStopOnly':True,'reason':'save and reload compatibility check'}));os.replace(t,p);print('normal stop requested',flush=True)
