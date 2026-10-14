"""Prepared controller. Run only after approved exclusive window and r9 launch."""
from pathlib import Path
import sys,json,time,traceback
import combined_acceptance as a
if sys.argv[1:]!=['--window-approved']:raise SystemExit('Explicit window approval required; nothing started.')
a.LAB=a.ROOT.parents[1]/'q/build/local-mc-debug/r9-default12g'
a.OUT=a.ROOT/'default12-acceptance.json'
a.receipt=dict(checks=[],events=[],productionMutation=False,physicalOSInput=False,formalSSO=False,heapMiB=12288)
def status():
 p=a.LAB/'coordinator/status-host.json'
 try:return json.loads(p.read_text())
 except (FileNotFoundError,json.JSONDecodeError):return {}
def stable():
 s=status()
 return s.get('connected') and s.get('overlay')=='NONE' and s.get('ysmRemaining')==0 and s.get('ysmLoaded')==587
try:
 a.wait(stable,900)
 # Two minutes beyond complete catalogue load, then MCEF/shop and tower work.
 end=time.monotonic()+120
 while time.monotonic()<end:
  if not stable():raise AssertionError('Catalogue/connection ceased to be stable')
  time.sleep(5)
 a.check('12GiB template-cache full catalogue stable120seconds',True,{k:v for k,v in status().items() if k!='ysmKeys'})
 a.shop()
 a.tower()
 a.maps()
 a.check('12GiB post-shop tower map connection alive',status().get('connected'))
 a.race()
 log=a.LAB/'host/logs/latest.log'
 a.check('12GiB no Java heap exhaustion in runtime log','OutOfMemoryError' not in log.read_text(encoding='utf-8',errors='replace'))
except BaseException as e:
 a.receipt['failure']=dict(error=str(e),traceback=traceback.format_exc());a.save();raise
finally:
 owner=a.LAB/'local-mc-owner.json'
 if owner.exists():a.qa.stop_files(a.LAB,json.loads(owner.read_text()))
