from pathlib import Path
import json,hashlib,zipfile,shutil,datetime
r=Path('next-release-20261012');smoke=r/'fullpack-smoke';owner=Path('C:/Users/ranzh/Documents/Codex/2026-10-02/task/shop-ui-20261011/race5-integration-qa4-20261012-r2');read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'));sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
h=read(owner/'HANDOFF.json');c=h['newHorseCandidate'];src=Path(c['source']);assert sha(src)==c['candidateSha256']=='2152b223f09f591b9d66ce4c297e532f391decb75e4fca8bb5d3a16f6b7748d0'
old=r/'comparison-baseline'/Path(c['baselinePath']).name;assert sha(old)==c['baselineSha256'];prior=r/'frozen-inputs/muxi-horse-racing-0.2.0-shop-ui-qa.3.jar'
with zipfile.ZipFile(old) as za,zipfile.ZipFile(src) as zb,zipfile.ZipFile(prior) as zp:
 a={n:za.read(n) for n in za.namelist() if not n.endswith('/')};b={n:zb.read(n) for n in zb.namelist() if not n.endswith('/')}
 removed=sorted(a.keys()-b.keys());changed=sorted(n for n in a.keys()&b.keys() if a[n]!=b[n]);added=sorted(b.keys()-a.keys());protected=[n for n in a if n.endswith('.class') and n!='net/muxigame/minigames/horse/HorseTrading.class']
 assert not removed;assert changed==['META-INF/neoforge.mods.toml','net/muxigame/minigames/horse/HorseTrading.class'];assert all(a[n]==b[n] for n in protected)
 media=[x['resourcePath'] for x in h['media']];assert len(media)==6 and all(b[n]==zp.read(n) for n in media)
 assert all(a[n]==b[n] for n in ['umamusume_catalog_v1.json','horse_trade_catalog_v1.json','horse_control.mixins.json'])
 result={'candidateSha256':sha(src),'baselineSha256':sha(old),'removed':removed,'changed':changed,'added':added,'protectedClassesByteIdentical':len(protected),'horseRoomsPreserved':a['net/muxigame/minigames/horse/HorseRooms.class']==b['net/muxigame/minigames/horse/HorseRooms.class'],'sixMediaUnchangedFromQa3':True,'catalogsAndMixinUnchanged':True,'ownerRegression':read(owner/'offline-qa/REGRESSION-RESULT.json'),'newCombinedNativePassed':False}
(r/'shop-qa4-independent-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');dst=r/'frozen-inputs'/src.name;shutil.copy2(src,dst)
lock=read(r/'input-lock.json');row=next(x for x in lock['inputs'] if x['component']=='muxi-horse-racing');row.update(source=str(src),frozenPath=str(dst.resolve()),candidateSha256=sha(dst));lock['updatedUtc']=datetime.datetime.now(datetime.timezone.utc).isoformat();lock['supersededHorseCandidateNotPublishable']='90f8d8eab40f8601cec5ba5f5b2493542526aeb45ff487d2749ada11e6b834f2';(r/'input-lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2),encoding='utf-8')
full=read(smoke/'frozen-fullpack-inputs.json')
for side in ['client','server']:
 info=full['sides'][side]
 for x in lock['inputs']:
  if x['component'] not in ['muxi-horse-racing','muxi-terminal']:continue
  oldp=smoke/side/x['baselinePath'];assert sha(oldp)==x['baselineSha256'];oldp.unlink();p=Path(x['frozenPath']);target=smoke/side/'mods'/p.name;shutil.copy2(p,target)
  info['files']=[f for f in info['files'] if f['path']!=x['baselinePath']];info['files'].append({'path':'mods/'+p.name,'bytes':p.stat().st_size,'sha256':sha(p)});info['changes'].append({'old':x['baselinePath'],'new':'mods/'+p.name,'sha256':sha(p)})
 assert len(list((smoke/side/'mods').glob('*.jar')))==info['modCount']
(smoke/'frozen-fullpack-inputs.json').write_text(json.dumps(full,ensure_ascii=False,indent=2),encoding='utf-8')
plan=read(smoke/'smoke-acceptance-plan.json');plan['replacementsOnly'] += ['muxi-terminal shop-ui-qa.3 bd4a181268a4b1df7e555ac41f22d79407639e3694337aad3b979e4587512292','muxi-horse-racing race.5-shop-ui-qa.4 '+sha(src)];plan['checks'] += ['Real MCEF shop: six GIF decode, visible pixel motion, category switch, scroll restoration and repeated open/close','Race entry wait/countdown, remote tick/collision smoke, normal shutdown with active room and reconnect return recovery; preserve prior race.5 acceptance limits'];plan['limitations'] += ['Existing automatic CEF orphan-free exit issue retained; no qa.3 result relabeled as qa.4 acceptance'];(smoke/'smoke-acceptance-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
release=read(r/'release-plan.json');release['shopBlocked']='Old baseline regression resolved offline by independently verified qa.4; combined native smoke still pending';release['remaining']=[x for x in release['remaining'] if not x.startswith('Shop horse integration')];release['remaining'].append('New four-module combined smoke incl. shop GIF/category and race.5 regression/normal recovery');release['combinedFrozenInputs']=lock['inputs'];(r/'release-plan.json').write_text(json.dumps(release,ensure_ascii=False,indent=2),encoding='utf-8')
with zipfile.ZipFile(r/'combined-four-module-qa4-review-only.zip','w',zipfile.ZIP_DEFLATED) as z:
 for x in lock['inputs']:z.write(x['frozenPath'],'mods/'+Path(x['frozenPath']).name)
 for p in [r/'input-lock.json',r/'release-plan.json',r/'shop-qa4-independent-verification.json']:z.write(p,p.name)
print(json.dumps(result,ensure_ascii=False));print('combinedZipSha256',sha(r/'combined-four-module-qa4-review-only.zip'))
