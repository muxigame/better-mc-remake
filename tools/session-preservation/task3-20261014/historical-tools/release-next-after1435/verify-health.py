from pathlib import Path
import socket,struct,json,time,urllib.request,datetime
N=Path(__file__).resolve().parent
def vi(n):
 b=bytearray()
 while True:
  x=n&127;n>>=7;b.append(x|(128 if n else 0))
  if not n:return bytes(b)
def readv(s):
 n=0
 for i in range(5):
  b=s.recv(1)
  if not b:raise EOFError('status response ended')
  n|=(b[0]&127)<<(7*i)
  if not b[0]&128:return n
 raise ValueError('invalid varint')
def readn(s,n):
 b=b''
 while len(b)<n:
  v=s.recv(n-len(b))
  if not v:raise EOFError('short response')
  b+=v
 return b
host='minecraft.muxigame.com';start=time.monotonic()
with socket.create_connection((host,25565),timeout=20) as s:
 s.settimeout(20);h=host.encode();body=b'\x00'+vi(767)+vi(len(h))+h+struct.pack('>H',25565)+b'\x01';s.sendall(vi(len(body))+body+b'\x01\x00');length=readv(s);packet=readv(s);assert packet==0;size=readv(s);status=json.loads(readn(s,size))
e=json.load(urllib.request.urlopen('https://mc.muxigame.com/api/v1/tunnel/endpoints',timeout=20))
o={'atUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'publicMinecraftStatusResponded':True,'elapsedMs':round((time.monotonic()-start)*1000),'protocol':status['version'],'players':status.get('players',{}).get('online'),'endpointTopKeys':list(e),'online':e.get('online'),'ageSeconds':e.get('ageSeconds')}
if 'online' in e:assert e['online'] is True
(N/'public-server-health.json').write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(o,ensure_ascii=False))
