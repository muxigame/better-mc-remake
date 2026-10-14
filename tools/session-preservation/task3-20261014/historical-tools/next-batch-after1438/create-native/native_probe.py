from pathlib import Path
import json,time,os
LAB=Path(r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\create-native-14-042813")
C=LAB/'coordinator';OUT=Path(__file__).parent;owner=json.loads((LAB/'local-mc-owner.json').read_text());rows=[]
def read(p):return json.loads(p.read_text(encoding='utf8'))
def cmd(role,kind,**kw):
 p=C/('command-'+role+'.json');i=(read(p).get('id',0) if p.exists() else 0)+1;q=dict(id=i,runId=owner['runId'],type=kind,**kw);t=p.with_suffix('.pending');t.write_text(json.dumps(q),encoding='utf8');os.replace(t,p);res=C/('result-'+role+'-'+str(i)+'.json');end=time.monotonic()+30
 while time.monotonic()<end:
  if res.exists():
   r=read(res);assert r.get('ok'),r;rows.append({'request':q,'result':r});(OUT/'command-evidence.json').write_text(json.dumps(rows,indent=2));return r
  time.sleep(.1)
 raise TimeoutError(q)
def execute(s):return cmd('server','execute',command=s)
D='execute in muxi_game_core:overworld run '
for s in ['forceload add 0 0','fill -3 119 -3 8 119 8 minecraft:stone','setblock 0 120 0 create:basin','setblock 0 122 0 sliceanddice:slicer','setblock 1 122 0 create:cogwheel[axis=y]','setblock 1 123 0 create:creative_motor[facing=down]','data merge block 1 123 0 {GeneratedSpeed:64.0f}','data merge block 0 122 0 {HeldItem:{id:"farmersdelight:iron_knife",count:1}}','data merge block 0 120 0 {InputItems:{Size:9,Items:[{Slot:0,id:"minecraft:melon",count:1}]}}','setblock 4 120 0 interiors:red_chair[facing=north]','setblock 6 120 0 sliceanddice:sprinkler[type=floor]']:
 execute(D+s)
time.sleep(12)
for label,pos in [('slicer','0 122 0'),('basin','0 120 0'),('motor','1 123 0')]:execute(D+'data modify storage muxiqa:create '+label+' set from block '+pos)
execute('data get storage muxiqa:create')
print('native block setup and actual data snapshots complete',flush=True)
end=time.monotonic()+180
while time.monotonic()<end:
 p=C/'status-host.json'
 if p.exists() and read(p).get('connected') and read(p).get('screen')=='NONE':break
 time.sleep(1)
else:raise TimeoutError('client not in world')
execute('execute in muxi_game_core:overworld run tp 10000 4.5 120 -1.5 0 10')
cmd('host','select',slot=8);cmd('host','block-interact',x=4,y=120,z=0);time.sleep(2)
execute(D+'data get entity 10000 RootVehicle');execute(D+'execute as @e[type=interiors:big_seat] run data get entity @s Passengers')
print('actual client chair interaction requested',flush=True)
