from pathlib import Path
import json,time,os
LAB=Path(r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\create-native-14-042813")
C=LAB/'coordinator';OUT=Path(__file__).parent;owner=json.loads((LAB/'local-mc-owner.json').read_text());rows=[]
def read(p):return json.loads(p.read_text(encoding='utf8'))
def cmd(role,kind,**kw):
 p=C/('command-'+role+'.json');i=(read(p).get('id',0) if p.exists() else 0)+1;q=dict(id=i,runId=owner['runId'],type=kind,**kw);t=p.with_suffix('.pending');t.write_text(json.dumps(q),encoding='utf8');replaceDeadline=time.monotonic()+3
 while True:
  try:os.replace(t,p);break
  except PermissionError:
   if time.monotonic()>replaceDeadline:raise
   time.sleep(.05)
 res=C/('result-'+role+'-'+str(i)+'.json');end=time.monotonic()+30
 while time.monotonic()<end:
  if res.exists():
   r=read(res);assert r.get('ok'),r;rows.append({'request':q,'result':r});(OUT/'command-evidence.json').write_text(json.dumps(rows,indent=2));return r
  time.sleep(.1)
 raise TimeoutError(q)
def execute(s):return cmd('server','execute',command=s)
D='execute in muxi_game_core:overworld run '
