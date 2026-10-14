"""Read-only scan of a private entity-region directory for explicit fixture UUIDs."""
from pathlib import Path
import argparse,gzip,io,json,struct,uuid,zlib

class Nbt:
 def __init__(self,data):self.f=io.BytesIO(data)
 def read(self,n):
  if n<0 or n>32*1024*1024:raise ValueError('Unreasonable NBT size')
  b=self.f.read(n)
  if len(b)!=n:raise ValueError('Truncated NBT')
  return b
 def number(self,fmt):return struct.unpack('>'+fmt,self.read(struct.calcsize(fmt)))[0]
 def string(self):return self.read(self.number('H')).decode('utf-8',errors='replace')
 def payload(self,kind,depth=0):
  if depth>128:raise ValueError('NBT nesting limit')
  if kind in (1,2,3,4,5,6):return self.number({1:'b',2:'h',3:'i',4:'q',5:'f',6:'d'}[kind])
  if kind==8:return self.string()
  if kind in (7,11,12):
   count=self.number('i')
   if count<0 or count>4*1024*1024:raise ValueError('NBT array limit')
   if kind==7:return self.read(count)
   fmt='i' if kind==11 else 'q';return [self.number(fmt) for _ in range(count)]
  if kind==9:
   child=self.number('B');count=self.number('i')
   if count<0 or count>1024*1024:raise ValueError('NBT list limit')
   return [self.payload(child,depth+1) for _ in range(count)]
  if kind==10:
   out={}
   while True:
    child=self.number('B')
    if child==0:return out
    name=self.string();out[name]=self.payload(child,depth+1)
  raise ValueError('Unknown NBT tag '+str(kind))
 def root(self):
  kind=self.number('B');self.string();return self.payload(kind)

def entity_id(entity):
 value=entity.get('UUID')
 if isinstance(value,list) and len(value)==4:return str(uuid.UUID(int=sum((n&0xffffffff)<<(96-32*i) for i,n in enumerate(value))))
 if 'UUIDMost' in entity and 'UUIDLeast' in entity:return str(uuid.UUID(int=((entity['UUIDMost']&((1<<64)-1))<<64)|(entity['UUIDLeast']&((1<<64)-1))))
 return ''

def scan(root,wanted):
 matches=[];errors=[];chunks=0
 for file in sorted(root.glob('r.*.*.mca')):
  data=file.read_bytes()
  if not data:continue
  if len(data)<8192:errors.append({'file':file.name,'error':'Short region header'});continue
  _,rx,rz,_=file.name.split('.');rx=int(rx);rz=int(rz)
  for slot in range(1024):
   entry=int.from_bytes(data[slot*4:slot*4+4],'big');sector=entry>>8
   if not sector:continue
   try:
    offset=sector*4096;length=int.from_bytes(data[offset:offset+4],'big');compression=data[offset+4];raw=data[offset+5:offset+4+length]
    cx=rx*32+slot%32;cz=rz*32+slot//32
    if compression&128:raw=(root/('c.%d.%d.mcc'%(cx,cz))).read_bytes()
    method=compression&127
    if method==1:raw=gzip.decompress(raw)
    elif method==2:raw=zlib.decompress(raw)
    elif method!=3:raise ValueError('Unsupported compression '+str(method))
    doc=Nbt(raw).root();chunks+=1
    def visit(e):
     key=entity_id(e)
     if key in wanted:matches.append({'uuid':key,'file':file.name,'chunk':[cx,cz],'id':e.get('id'),'pos':e.get('Pos'),'health':e.get('Health'),'noAi':e.get('NoAI'),'onGround':e.get('OnGround'),'passengers':[entity_id(p) for p in e.get('Passengers',[])]})
     for p in e.get('Passengers',[]):visit(p)
    for entity in doc.get('Entities',[]):visit(entity)
   except Exception as exc:errors.append({'file':file.name,'slot':slot,'error':str(exc)})
 return {'readOnly':True,'entityRegionRoot':str(root.resolve()),'chunksRead':chunks,'matches':matches,'missingFromSavedRegions':sorted(wanted-{r['uuid'] for r in matches}),'errors':errors,'limits':'Missing saved entity does not establish destruction; other region roots/storage may retain it.'}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--entities-root',type=Path,required=True);p.add_argument('--uuid',action='append',required=True);args=p.parse_args()
 print(json.dumps(scan(args.entities_root,{str(uuid.UUID(v)) for v in args.uuid}),indent=2))
