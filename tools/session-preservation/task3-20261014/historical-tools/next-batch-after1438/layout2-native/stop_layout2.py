from pathlib import Path
import json,os,time
r=Path(r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\jui-layout2-14-030112")
o=json.loads((r/'local-mc-owner.json').read_text());assert o['runId']=='1778047709494e2ba49f02a794cc2703'
p=r/'stop-request.json';tmp=p.with_suffix('.pending');tmp.write_text(json.dumps({'runId':o['runId'],'normalStopOnly':True,'reason':'layout2 minimal cold native verification complete'}));os.replace(tmp,p);print('normal stop requested',flush=True)
