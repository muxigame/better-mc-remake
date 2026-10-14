from pathlib import Path
import json,re,datetime
root=Path(__file__).resolve().parent;lab=root.parents[1]/'q/build/local-mc-debug/r9-default12g'
read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
gc=(lab/'host/gc.log').read_text(encoding='utf-8',errors='replace')
pauses=[];full=[];heaps=[]
for line in gc.splitlines():
 m=re.search(r'Pause .* (\d+(?:\.\d+)?)ms$',line)
 if m:pauses.append(float(m[1]))
 if 'Pause Full' in line and m:full.append({'ms':float(m[1]),'line':line})
 m=re.search(r'(\d+)M->(\d+)M\((\d+)M\)',line)
 if m:heaps.append(tuple(map(int,m.groups())))
logs={}
for role in ['host','server']:
 p=lab/role/'logs/latest.log'
 logs[role]=[{'line':i,'text':l[:500]} for i,l in enumerate(p.read_text(encoding='utf-8',errors='replace').splitlines(),1) if 'OutOfMemoryError' in l or 'GC overhead' in l]
samples=[json.loads(l) for l in (root/'default12-memory-samples.jsonl').read_text().splitlines()]
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'heapLimitMiB':12288,'gc':{'pauseCount':len(pauses),'maximumPauseMs':max(pauses,default=0),'pauseOver1000ms':sum(x>1000 for x in pauses),'fullGC':full,'peakBeforeGCMiB':max((x[0] for x in heaps),default=0),'peakAfterGCMiB':max((x[1] for x in heaps),default=0)},'memory':{'samples':len(samples),'maxRSSMiB':{r:max((s['roles'].get(r,{}).get('rssMiB',0) for s in samples),default=0) for r in ['host','server']},'rssIncludesNativeMemoryNotJustHeap':True},'heapFailureLogs':logs,'productionMutation':False}
for f,key in [('default12-acceptance.json','acceptance'),('default12-installed-products.json','products')]:
 if (root/f).exists():out[key]=read(root/f) if key=='products' else {'checks':read(root/f)['checks'],'failure':read(root/f).get('failure')}
if (lab/'run-result.json').exists():out['processResult']=read(lab/'run-result.json')
(root/'default12-runtime-summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in out.items() if k not in ['acceptance','products','processResult']},ensure_ascii=False))
