import pathlib,json,hashlib,shutil,subprocess,importlib.util,zipfile
W=pathlib.Path.cwd();O=W/'.local-operations/release-20261008-takeover';D=O/'final-integration-20261008-r2'
def read(p):return json.loads(p.read_bytes())
a=read(O/'final-integration-20261008-r1/FINAL-COMBINATION-MANIFEST.json');b=read(D/'FINAL-COMBINATION-MANIFEST.json')
diffs={}
for side in ['client','server']:
 x={f['path']:f['sha256'] for f in a['inventories'][side]['files']};y={f['path']:f['sha256'] for f in b['inventories'][side]['files']}
 diffs[side]=[p for p in sorted(x.keys()|y.keys()) if x.get(p)!=y.get(p)]
 assert diffs[side]==['mods/muxi-game-core-1.12.3-task14-qa.1.jar'],diffs
old=O/'final-integration-20261008-r1/server/mods/muxi-game-core-1.12.3-task14-qa.1.jar';new=D/'server/mods/muxi-game-core-1.12.3-task14-qa.1.jar'
with zipfile.ZipFile(old) as z1,zipfile.ZipFile(new) as z2:
 assert set(z1.namelist())==set(z2.namelist())
 entries=[n for n in z1.namelist() if z1.read(n)!=z2.read(n)]
 assert entries==['net/muxigame/core/feature/dimensions/MinigameTravelRules.class'],entries
C=D/'release-contract';(C/'scripts').mkdir(parents=True,exist_ok=True)
contract=read(W/'release-inputs.json');projects=read(W/'projects.json')
commit=subprocess.check_output(['git','-C',str(W/'muxi-game-core'),'rev-parse','2f7dceb'],text=True).strip()
for item in contract['artifacts']:
 if item['project']=='muxi-game-core':
  item.update(commit=commit,sha256=hashlib.sha256(new.read_bytes()).hexdigest(),sha1=hashlib.sha1(new.read_bytes()).hexdigest(),build_output='muxi-tower-defense/build/core-travel-2f7dceb/muxi-game-core/build/libs/'+new.name)
for item in projects['projects']:
 if item['path']=='muxi-game-core':item['head']=commit
for name,data in [('release-inputs.json',contract),('projects.json',projects)]: (C/name).write_text(json.dumps(data,indent=2),encoding='utf-8')
shutil.copy2(W/'scripts/check-release-inputs.py',C/'scripts/check-release-inputs.py')
checks=[]
for side,mods in [('client',D/'pack/source/Better MC Remake [FORGE]/mods'),('server',D/'server/mods')]:
 args=['python',str(C/'scripts/check-release-inputs.py'),'--mods-dir',str(mods)]
 if side=='client':args+=['--manifest',str(D/'pack/manifest.json')]
 r=subprocess.run(args,capture_output=True,text=True);checks.append({'side':side,'exitCode':r.returncode,'output':r.stdout+r.stderr});assert r.returncode==0,checks
m=read(D/'pack/manifest.json');root=pathlib.Path('\\\\?\\'+str(D/'pack/source/Better MC Remake [FORGE]'))
for f in m['files']:
 if not f.get('externalDownload'):assert hashlib.sha1((root/f['path']).read_bytes()).hexdigest()==f['sha1'],f['path']
report={'r1ToR2Changes':diffs,'coreChangedZipEntries':entries,'coreCommit':commit,'pairContractChecks':checks,'qaFindings':b['qaFindings'],'allPackSha1Verified':True,'nativeEvidenceCommit':'582150e','towerZombieNativeChecks':40,'blocking':['unknown exit1','r2 Outbreak native retest','normal client/server exit'],'productionAllowed':False,'sharedSourceModified':False}
(O/'final-integration-r2-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
