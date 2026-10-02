"""Install hash-pinned official aircraft files into explicitly named local development mods folders."""
from pathlib import Path
import argparse,hashlib,json,shutil,urllib.request

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mods',type=Path,action='append',required=True);p.add_argument('--cache',type=Path,required=True);p.add_argument('--download',action='store_true');a=p.parse_args()
 lock=json.loads((Path(__file__).parent/'dependencies/flight-131.json').read_text(encoding='utf-8'));a.cache.mkdir(parents=True,exist_ok=True)
 for row in lock['files']:
  name=row['file'];source=a.cache/name
  if not source.exists():
   if not a.download:raise SystemExit('Missing cached official file: '+name)
   fid=str(row['fileId']);url='https://edge.forgecdn.net/files/'+fid[:-3]+'/'+str(int(fid[-3:]))+'/'+urllib.parse.quote(name)
   urllib.request.urlretrieve(url,source)
  if hashlib.sha256(source.read_bytes()).hexdigest()!=row['sha256']:raise SystemExit('Official SHA-256 mismatch: '+name)
  for target in a.mods:
   target.mkdir(parents=True,exist_ok=True);dest=target/name
   if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()!=row['sha256']:raise SystemExit('Existing jar differs; refusing replacement: '+str(dest))
   shutil.copy2(source,dest)
  print(name+' '+row['sha256'])
 print('Local development installation only. No Git staging, upload, or publication performed.')
if __name__=='__main__':main()
