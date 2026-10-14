from pathlib import Path
import subprocess,json
N=Path(__file__).resolve().parent
source=r'''import subprocess,json,pathlib,hashlib
def run(a):return subprocess.check_output(a,text=True)
d=json.loads(run(['docker','inspect','muxi-auth-muxi-auth-1']))[0]
out={'name':d['Name'],'imageId':d['Image'],'imageName':d['Config']['Image'],'workingDir':d['Config']['WorkingDir'],'command':d['Config']['Cmd'],'mounts':[{'source':x['Source'],'destination':x['Destination'],'rw':x['RW']} for x in d['Mounts']],'composeProject':d['Config']['Labels'].get('com.docker.compose.project.working_dir'),'composeFiles':d['Config']['Labels'].get('com.docker.compose.project.config_files'),'status':d['State']['Status'],'health':d['State'].get('Health',{}).get('Status'),'images':json.loads(run(['docker','image','inspect',d['Image']]))[0]['RepoTags'],'disk':run(['df','-h','/opt/muxi-auth']).splitlines(),'files':{}}
for n in ['compose.prod.yaml','Dockerfile','requirements.txt','app/main.py']:
 p=pathlib.Path('/opt/muxi-auth')/n
 if p.is_file():out['files'][n]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
out['musicConfigNames']=[v.split('=',1)[0] for v in d['Config']['Env'] if v.startswith('MUSIC_')]
print(json.dumps(out))
'''
hop=r'C:\Windows\System32\OpenSSH\ssh.exe -T -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes root@<PRIVATE_HOST> python3 -'
r=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','administrator@<PRIVATE_HOST>',hop],input=source.encode(),capture_output=True,timeout=45)
if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace')[-1500:])
o=json.loads(r.stdout.decode('utf-8-sig'));(N/'backend-host-baseline.json').write_text(json.dumps(o,indent=2),encoding='utf-8');print(json.dumps(o,indent=2))
