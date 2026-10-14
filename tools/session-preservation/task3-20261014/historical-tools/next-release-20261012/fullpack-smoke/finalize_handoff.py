from pathlib import Path
import json,datetime
ROOT=Path(__file__).resolve().parent
WS=ROOT.parents[1]
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
results={n:read(WS/'q/build/local-mc-debug'/n/'run-result.json') for n in ['r7','r8']}
assert all(r['normalExit'] and r['exitCodes']=={'server':0,'host':0} for r in results.values())
receipt=read(ROOT/'combined-acceptance.json')
latest={c['name']:c for c in receipt['checks']}
required=[n for n in latest if n.startswith(('tower ','map ','race '))]
assert len(required)>=25 and all(latest[n]['passed'] for n in required)
shop=[read(WS/'q/build/local-mc-debug/r5'/f'shop-client-result-cycle{i}.json') for i in range(3)]
assert all(x['success'] for x in shop)
pixels=read(ROOT/'combined-gif-pixels.json')
assert len(pixels['rows'])==18 and all(x['motionObserved'] for x in pixels['rows'])
clear=read(ROOT/'window-clearance.json')
assert not clear['javaOrJcef'] and not clear['port25847Listeners'] and all(x['exclusiveOpen'] for x in clear['worldLocks'])
plan=read(ROOT.parent/'release-plan.json')
handoff=dict(status='FOUR_FROZEN_MODULES_NATIVE_ACCEPTANCE_PASSED_WINDOW_RELEASED',utc=now,
 productionMutation=False,productionStop=False,manifestSwitched=False,newProductionBackup=False,
 baselineVersion=plan['baselineVersion'],baselineManifestSha256=plan['baselineManifestSha256'],
 frozenInputs=plan['combinedFrozenInputs'],productCounts={'client':436,'server':350},qaExtraJarsPerSide=2,
 fullCatalogueModels=587,clientHeapMiB=16384,serverHeapMiB=6144,
 latestFunctionalChecks=[{'name':n,'passed':latest[n]['passed']} for n in required],
 shop={'cycles':3,'domAssertions':sum(len(x['dom']['checks']) for x in shop),'nativeGifMotionGroups':18,'nativeFrames':54,'instance':'q/build/local-mc-debug/r5','sameFrozenProducts':True},
 normalExitResults=results,clearance=clear,
 orphanHelperCleanup={'run':'r8','pid':42516,'parentPid':33760,'parentExitCode':0,'exactPrivateExecutableAndCacheVerified':True,'action':'Stopped only orphan JCEF renderer after parent absence; no Java force kill'},
 evidence=['combined-acceptance.json','combined-gif-pixels.json','r7-installed-product-verification.json','attempt-notes.json','8g-heap-pressure-evidence.json','native-xp-boundary.json'],
 limitations=[
 'Private numeric account 10000 used through actual network client; no formal SSO and no physical OS input or human-feel acceptance.',
 'R3/R5 underallocated 8192MiB exhausted heap; R3 native jvm.dll crash cause beyond heap pressure not proven. Existing launcher auto target12288MiB was not stress-retested; final functional pass used16384MiB. Production memory settings unchanged.',
 'Native Waystones21.1.42 exact9XP float-affordability edge not claimed passed. Normal100XP to91XP and insufficient0XP behavior passed.',
 'Prior failed attempts retained in combined receipt; final latest named checks supersede corrected harness attempts only.',
 'Music real upload/burn/play, Outbreak .4 and other owner candidates are outside this frozen four-module acceptance. No additional release is claimed.'
 ],nextWindow='Outbreak .4 first; music after its isolated preparation is ready',
 crossThreadProgressMessage='Auto-review rejected manual send to parent because no direct human authorization for cross-thread messaging; completion uses automatic parent notification.')
write(ROOT/'FINAL-HANDOFF.json',handoff)
notes=read(ROOT/'attempt-notes.json')
for a in notes['attempts']:
 if a['name']=='r7':a.update(outcome='Full587-model catalogue stable; tower/map/race native checks passed; server and host normal exit0',reason='Same frozen products.16GiB test heap; production settings unchanged.')
notes['attempts'].append(dict(name='r8',clientHeapMiB=16384,outcome='Copied only r7 private world; normal restart cleared durable return marker, restored exact position/mode, no stale room/tickets; both JVMs exit0. Exact private orphan JCEF42516 cleaned after parent33760 exit0; no Java force kill.'))
write(ROOT/'attempt-notes.json',notes)
plan['status']='FOUR_MODULES_PASSED_AWAITING_FINAL_SCOPE_AND_RELEASE_WINDOW'
plan['shopBlocked']=None
plan['fourModuleAcceptance']='fullpack-smoke/FINAL-HANDOFF.json'
plan['remaining']=['Owner final Outbreak .4 and music native results and exact artifacts for any expanded scope','Recheck live production and public manifest immediately before approved release','Freeze exact subset/version and coordinate actual production release window']
plan['tonightInstruction']='Prepare tonight release; no new backup; no actual production stop until final scope/window. Next MC window Outbreak .4; music follows when private isolated preparation is ready.'
plan['musicIntake']['qaRouteProposal']='Earlier public-route proposal superseded: owner plans private QA mod to loopback HTTPS plus existing authenticated SSH stdio to temporary restricted signer, existing launcher bearer/active connection checks. No public account route change.'
plan['musicIntake']['state']='REAL_BACKEND_OSS_PASSED_PRIVATE_MC_PREPARATION_PENDING'
write(ROOT.parent/'release-plan.json',plan)
print(json.dumps({'handoff':str(ROOT/'FINAL-HANDOFF.json'),'functionalChecks':len(required),'shopAssertions':handoff['shop']['domAssertions'],'utc':now}))
