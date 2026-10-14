from pathlib import Path
import json,hashlib,urllib.request,urllib.parse,subprocess,base64,datetime
out=Path('music-readonly-audit');out.mkdir(exist_ok=True)
a=json.load(urllib.request.urlopen('https://mc.muxigame.com/api/v1/manifest',timeout=30));raw=urllib.request.urlopen(a['manifestUrl'],timeout=30).read();m=json.loads(raw)
entries=[f for f in m['files'] if f['path'].startswith('mods/') and any(k in f['path'].lower() for k in ['netmusic','net_music','nmbl','music-compat','muxi-terminal'])]
rows=[]
for f in entries:
 url=f.get('url') or a['manifestUrl'].rsplit('/',1)[0]+'/files/'+urllib.parse.quote(f['path'])
 b=urllib.request.urlopen(url,timeout=40).read();rows.append({**f,'downloadUrl':url,'actualSha256':hashlib.sha256(b).hexdigest(),'manifestHashMatches':hashlib.sha1(b).hexdigest()==f['sha1']})
res={'atUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'version':m['pack']['version'],'manifestSha256':hashlib.sha256(raw).hexdigest(),'files':rows}
(out/'public.json').write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(res,ensure_ascii=False))
s=r'''$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$root='C:\Users\Administrator\WorkSpace\muxigame\bmc5server';$files=@(Get-ChildItem -LiteralPath "$root\mods" -File|Where-Object{$_.Name -match 'netmusic|net_music|nmbl|music-compat|muxi-terminal'}|ForEach-Object{@{name=$_.Name;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower();bytes=$_.Length}});$lines=@();foreach($name in @('latest.log','debug.log')){$p=Join-Path "$root\logs" $name;if(Test-Path -LiteralPath $p){$lines+=@(Select-String -LiteralPath $p -Pattern 'netmusic|net_music_list|nmbl|music.compat|muximusic'|Where-Object{$_.Line -match 'Loading|Loaded|Found mod|Mod File|\|.*\|' -and $_.Line -notmatch 'token|cookie|password|https?://' }|Select-Object -First 25|ForEach-Object{@{file=$name;line=$_.LineNumber;text=$_.Line}})}};@{files=$files;loadingEvidence=$lines}|ConvertTo-Json -Depth 6 -Compress'''
w='& ([scriptblock]::Create([Console]::In.ReadToEnd()))'
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','administrator@<PRIVATE_HOST>','powershell','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(w.encode('utf-16-le')).decode()],input=s.encode('utf-8'),capture_output=True,timeout=55);assert p.returncode==0,p.stderr.decode(errors='replace');r=json.loads(p.stdout.decode('utf-8-sig'));r['allServerFilesMatchPublic']=all(any(x['path']=='mods/'+f['name'] and x['actualSha256']==f['sha256'] for x in rows) for f in r['files']);(out/'server.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(r,ensure_ascii=False))
