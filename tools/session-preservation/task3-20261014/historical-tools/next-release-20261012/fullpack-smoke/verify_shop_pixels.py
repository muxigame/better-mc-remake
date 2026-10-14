from pathlib import Path
from PIL import Image
import json,hashlib,numpy as np,sys
root=Path(__file__).resolve().parent
lab=root.parents[1]/'q/build/local-mc-debug/r5'
output='combined-gif-pixels.json'
if sys.argv[1:]==['--default12']:
 lab=root.parents[1]/'q/build/local-mc-debug/r9-default12g'
 output='default12-gif-pixels.json'
elif sys.argv[1:]:raise SystemExit('Unknown argument')
rows=[]
for cycle in range(3):
 result=json.loads((lab/f'shop-client-result-cycle{cycle}.json').read_text())
 assert result['success']
 for n in range(6):
  frames=[];details=[]
  for phase in range(3):
   name=f'shop-cycle{cycle}-native-cover-{n}-{phase}'
   meta=json.loads((lab/(name+'-coordinates.json')).read_text());capture=next(c for c in result['dom']['captures'] if c['capture']==f'native-cover-{n}-{phase}')
   b=capture['bounds'];scale=meta['guiScale'];left=meta['left'];top=meta['top']+meta['APP_BAR']
   bounds=tuple(round(x*scale) for x in [left+b['x']+4,top+b['y']+4,left+b['x']+b['width']-4,top+b['y']+b['height']-4])
   with Image.open(lab/(name+'.png')) as image:
    assert bounds[0]>=0 and bounds[1]>=0 and bounds[2]<=image.width and bounds[3]<=image.height
    pixels=np.asarray(image.convert('RGB').crop(bounds)).copy()
   frames.append(pixels);details.append(dict(path=str(lab/(name+'.png')),bounds=bounds,pixelSha256=hashlib.sha256(pixels.tobytes()).hexdigest()))
  counts=[int(np.any(np.abs(frames[0].astype(int)-f.astype(int))>5,axis=2).sum()) for f in frames[1:]]
  rows.append(dict(cycle=cycle,cover=n,resourceId=capture['resourceId'],changedPixels=counts,motionObserved=max(counts)>30,frames=details))
out=dict(actualMinecraftFramebuffer=True,physicalOSInput=False,rows=rows,passed=all(r['motionObserved'] for r in rows))
(root/output).write_text(json.dumps(out,indent=2))
print(json.dumps(dict(passed=out['passed'],covers=len(rows),motion=[r['changedPixels'] for r in rows])))
assert out['passed']
