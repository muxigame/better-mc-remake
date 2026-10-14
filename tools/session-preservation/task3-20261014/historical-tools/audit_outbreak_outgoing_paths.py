exec(open('source_serial_finalize.py',encoding='utf-8-sig').read().split('result={}')[0])
repo=ROOT/'muxi-outbreak'; remote='c0e61bcbb0abb259a44c27820e678bbf4c6de8c2'; head=git(repo,'rev-parse','HEAD')
objects=git(repo,'rev-list','--objects',head,'^'+remote).splitlines()
flags=[]
for row in objects:
    parts=row.split(' ',1)
    if len(parts)<2:continue
    obj,path=parts
    if pathlib.PurePosixPath(path).suffix.lower()=='.nbt' or 'outbreak_geometry/' in path or 'outbreak_details/' in path or path.endswith('MODEL_PICKUP_HANDOFF.json'):
        if git(repo,'cat-file','-t',obj)=='blob':flags.append({'path':path,'object':obj,'bytes':int(git(repo,'cat-file','-s',obj))})
out={'local_head':head,'remote_dev':remote,'status':'Not pushed by coordinator; B notified successfully and stopped whole dev push','flagged_resource_objects':flags,'ahead':int(git(repo,'rev-list','--count',remote+'..'+head))}
pathlib.Path('outbreak-outgoing-resource-review.json').write_text(json.dumps(out,indent=2))
print(json.dumps({'ahead':out['ahead'],'flagged_resource_objects':len(flags)}))
