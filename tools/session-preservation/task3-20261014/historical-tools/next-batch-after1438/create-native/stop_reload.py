from pathlib import Path
import json,os
r=Path(r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\create-native-reload-14-045505")
o=json.loads((r/'local-mc-owner.json').read_text());assert o['runId']=='aff45e674e844c51ba519924e2546e46';p=r/'stop-request.json';t=p.with_suffix('.pending');t.write_text(json.dumps({'runId':o['runId'],'normalStopOnly':True,'reason':'bounded addon native compatibility pass complete; release window for music'}));os.replace(t,p);print('normal stop requested',flush=True)
