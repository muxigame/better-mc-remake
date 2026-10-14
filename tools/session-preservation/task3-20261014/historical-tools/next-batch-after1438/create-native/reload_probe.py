from pathlib import Path
import json,time
end=time.monotonic()+480
lab=Path(r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\create-native-reload-14-045505")
while time.monotonic()<end:
 p=lab/'coordinator/status-host.json'
 if p.exists():
  try:
   j=json.loads(p.read_text());
   if j.get('connected') and j.get('screen')=='NONE':break
  except (OSError,ValueError):pass
 if (lab/'run-result.json').exists():raise RuntimeError('reload runner ended before client ready')
 time.sleep(1)
else:raise TimeoutError('reload client ready')
from reload_control import *
execute(D+'data get block 20 120 0');execute(D+'data get block 0 120 0');execute(D+'sable info @e');execute(D+'execute store result storage muxiqa:reload soupCount int 1 run data get block 20 120 0 OutputItems.Items[{id:"farmersdelight:vegetable_soup"}].count');execute('data get storage muxiqa:reload');execute(D+'sable remove @e');execute(D+'sable info @e');execute('save-all flush');print('reload snapshot, actual soup count and native sublevel destruction recorded',flush=True)
