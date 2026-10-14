import socket,json,struct,pathlib,time,sys
P=pathlib.Path(__file__).resolve().parent
HOST=sys.argv[1] if len(sys.argv)>1 else 'minecraft.muxigame.com';PORT=25565
def varint(n):
 b=bytearray()
 while True:
  value=n&127;n>>=7;b.append(value|(128 if n else 0))
  if not n:return bytes(b)
def take(s,n):
 data=b''
 while len(data)<n:
  more=s.recv(n-len(data))
  if not more:raise EOFError('Server closed status connection')
  data+=more
 return data
def readvar(s):
 value=0
 for i in range(5):
  b=take(s,1)[0];value|=(b&127)<<(7*i)
  if not b&128:return value
 raise ValueError('VarInt too long')
with socket.create_connection((HOST,PORT),timeout=10) as s:
 s.settimeout(10);h=HOST.encode();payload=varint(0)+varint(767)+varint(len(h))+h+struct.pack('>H',PORT)+varint(1);s.sendall(varint(len(payload))+payload+b'\x01\x00');size=readvar(s);pid=readvar(s);assert pid==0;data=json.loads(take(s,readvar(s)))
result={'atUtc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'endpoint':HOST+':'+str(PORT),'statusResponded':True,'version':data.get('version'),'playersOnline':data.get('players',{}).get('online'),'realLoginAttempted':False};(P/('server-status-'+HOST.replace(':','_')+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
