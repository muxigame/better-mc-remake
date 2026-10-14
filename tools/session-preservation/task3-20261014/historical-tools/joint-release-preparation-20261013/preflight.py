from pathlib import Path
import json,hashlib,urllib.request,datetime
from remote import remote
N=Path(__file__).resolve().parent
r=json.loads(remote((N/'preflight.ps1').read_text(encoding='utf-8'),600));(N/'production-baseline.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
api=json.load(urllib.request.urlopen('https://mc.muxigame.com/api/v1/manifest',timeout=30));raw=urllib.request.urlopen(urllib.request.Request(api['manifestUrl'],headers={'Cache-Control':'no-cache'}),timeout=30).read();m=json.loads(raw)
(N/'baseline-manifest.json').write_bytes(raw);(N/'api-baseline.json').write_text(json.dumps(api,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'host':r['hostname'],'root':r['productionRoot'],'identity':r['listenerIdentity'],'mods':len(r['mods']),'freeGiB':r['freeBytes']/1073741824,'owners':r['owners'],'releaseDirectories':r['releaseDirectories'],'publicVersion':m['pack']['version'],'manifestSha256':hashlib.sha256(raw).hexdigest(),'apiKeys':list(api)},ensure_ascii=False))
