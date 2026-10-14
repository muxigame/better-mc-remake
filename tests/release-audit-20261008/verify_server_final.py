import pathlib,json,hashlib
R=pathlib.Path(r'C:\Users\Administrator\WorkSpace\muxigame\.releases\production1432-r2');S=pathlib.Path(r'C:\Users\Administrator\WorkSpace\muxigame\bmc5server');plan=json.loads((R/'server-payload/server-plan.json').read_bytes());rows=[]
for f in plan['updates']+plan['verifyUnchanged']:
 p=S/f['path'];h=hashlib.sha256(p.read_bytes()).hexdigest();assert h==f['sha256'],f['path'];rows.append({'path':f['path'],'sha256':h})
for f in plan['remove']:assert not (S/f['path']).exists(),f['path']
backup=pathlib.Path(json.loads((R/'cold-backup-result.json').read_text(encoding='utf-8-sig'))['backup'])
def properties(p):
 return dict(line.split('=',1) for line in p.read_text(encoding='utf-8-sig').splitlines() if line and not line.startswith(('#','!')) and '=' in line)
properties_bytes_unchanged=(S/'server.properties').read_bytes()==(backup/'server.properties').read_bytes()
assert properties(S/'server.properties')==properties(backup/'server.properties'),'server.properties semantic values changed'
for rel in ['start.bat','user_jvm_args.txt']:
 assert hashlib.sha256((S/rel).read_bytes()).digest()==hashlib.sha256((backup/rel).read_bytes()).digest(),rel
baseline=json.loads((R/'preflight.json').read_text(encoding='utf-8-sig')); replacements={x['path']:x for x in plan['updates']}; removed={x['path'] for x in plan['remove']}; artifact_count=0
for f in baseline['rows']:
 rel=f['path']
 if rel in removed or not rel.startswith(('mods/','kubejs/','tacz/','tlm_custom_pack/','third-party-notices/')):continue
 expected=replacements.get(rel,f)['sha256']; p=S/rel
 assert hashlib.sha256(p.read_bytes()).hexdigest()==expected,rel;artifact_count+=1
mods=list((S/'mods').glob('*.jar'));assert not any(any(x in p.name.lower() for x in ['qa-only','dimension-probe','exit-witness','exit-diagnostics']) for p in mods)
for prefix in ['muxi-flight-','muxi-game-core-','muxi-horse-racing-','muxi-minigames-','muxi-outbreak-','muxi-terminal-','muxi-tower-defense-','muxi-zombie-challenge-']:
 assert len([p for p in mods if p.name.startswith(prefix)])==1,prefix
native=json.loads((R/'server-payload/native-manifest.json').read_bytes());region=S/'world/dimensions/muxi_minigames/flight_iwo_half/region';assert len(list(region.glob('*.mca')))>=230
r={'allBaselineRuntimeArtifactHashesVerified':artifact_count,'selectedServerFileHashesVerified':len(rows),'removedOldJarCount':len(plan['remove']),'productionNetworkStartJvmSettingsUnchanged':True,'serverPropertiesBytesUnchanged':properties_bytes_unchanged,'serverPropertiesAllValuesUnchanged':True,'exactOneOfEachChangedMod':True,'qaStandaloneAbsent':True,'nativeRegionCountAtInstall':230,'rows':rows};(R/'server-final-hash-verification.json').write_text(json.dumps(r,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in r.items() if k!='rows'}))
