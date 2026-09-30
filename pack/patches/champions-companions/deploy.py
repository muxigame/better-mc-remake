"""Deploy only the tested companion bridge and V3 health script, with backup and rollback."""
from pathlib import Path
from datetime import datetime
import argparse, hashlib, json, os, subprocess, sys, tempfile, tomllib
from zipfile import ZipFile
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
SOURCE=ROOT/'better-mc-remake/pack/source/Better MC Remake [FORGE]'
HEALTH=ROOT/'better-mc-remake/pack/patches/champions-health'

def sha(data):return hashlib.sha256(data).hexdigest()
def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False,suffix='.tmp') as f:f.write(data);temp=Path(f.name)
    try:os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    smoke=json.loads((HERE/'build/last-smoke-result.json').read_text());assert smoke['success'] and smoke['exitCode']==0
    lab=Path(smoke['lab'])
    jar=HERE/'build/muxi-champion-companions-1.0.4.jar';script=HEALTH/'muxi_champions_health.js'
    config=HERE/'config-examples/muxi-champion-companions-server.toml'
    assert tomllib.loads(config.read_text(encoding='utf-8'))=={'spawning':{'ownedCompanionChance':0.33}}
    golem=ROOT/'better-mc-remake/pack/patches/combat-balance/muxi_golem_combat_balance.js'
    assert jar.read_bytes()==(lab/'mods'/jar.name).read_bytes(),'Artifact changed after successful smoke'
    assert script.read_bytes()==(lab/'kubejs/server_scripts'/script.name).read_bytes(),'Health script changed after successful smoke'
    assert golem.read_bytes()==(lab/'kubejs/server_scripts'/golem.name).read_bytes(),'Golem runtime compatibility differs from smoke'
    assert any('Independent natural 10% / owned 33%' in result for result in smoke['passed']), 'Independent probability gates were not tested'
    assert any('Owned 50/25/15/7/3 and natural 50/25/15/8/2 tier lotteries' in result for result in smoke['passed']), 'Tier distributions were not tested'
    assert any('Film revive preserves exact Champion tier/affixes and one-roll state' in result for result in smoke['passed']), 'Maid film revive was not tested'
    subprocess.run([sys.executable,str(HEALTH/'apply_champions_health.py')],check=True)
    subprocess.run(['node',str(golem.with_name('test_balance.cjs'))],check=True)
    plans={}
    protected={}
    for root in [ROOT/'bmc5server',SOURCE]:
        plans[root/'mods'/jar.name]=jar.read_bytes()
        plans[root/'config'/config.name]=config.read_bytes()
        # Never leave two versions of the same mod in mods/. Back up the old jar
        # before removing it; rollback the entire group if any file is locked.
        for old in (root/'mods').glob('muxi-champion-companions-*.jar'):
            if old.name!=jar.name:
                assert old.name in {'muxi-champion-companions-1.0.0.jar','muxi-champion-companions-1.0.1.jar','muxi-champion-companions-1.0.2.jar','muxi-champion-companions-1.0.3.jar'},'Unexpected companion version: '+str(old)
                plans[old]=None
        plans[root/'kubejs/server_scripts'/script.name]=script.read_bytes()
        plans[root/'kubejs/server_scripts'/golem.name]=golem.read_bytes()
        for relative in ['config/champions-server.toml','config/tacz-server.toml','config/l2configs/modulargolems-server.toml']:
            p=root/relative
            if p.exists():protected[p]=sha(p.read_bytes())
        for p in (root/'kubejs').rglob('*'):
            if p.is_file() and p.name not in {script.name,golem.name}:protected[p]=sha(p.read_bytes())
        for p in (root/'mods').glob('muxi-game-core-*.jar'):protected[p]=sha(p.read_bytes())
        # Same installed Champion and core versions as the successful lab.
        for pattern in ['*champions*.jar','muxi-game-core-*.jar']:
            found=list((root/'mods').glob(pattern));assert len(found)==1
            labjar=list((lab/'mods').glob(pattern))
            assert len(labjar)==1
            if pattern.startswith('muxi-game-core'):
                # Source core 1.3 and server core 1.4 have identical champion classes.
                # Do not overwrite either unrelated core release just to match its filename.
                with ZipFile(found[0]) as actual, ZipFile(labjar[0]) as tested:
                    for name in ['net/muxigame/core/feature/champions/ChampionRules.class',
                                 'net/muxigame/core/feature/champions/ChampionsFeature.class',
                                 'net/muxigame/core/mixin/ChampionSpawnHandlerMixin.class']:
                        assert actual.read(name)==tested.read(name),'Champion core integration differs from tested classes'
            else:
                assert found[0].read_bytes()==labjar[0].read_bytes(),'Runtime dependency differs from lab'
    p=ROOT/'better-mc-remake/pack/packspec.json';protected[p]=sha(p.read_bytes())
    before={p:p.read_bytes() if p.exists() else None for p in plans}
    changes={p:v for p,v in plans.items() if before[p]!=v}
    print('CHANGES:',[str(p.relative_to(ROOT)) for p in changes])
    if not args.apply or not changes:print('No live/source writes');return
    backup=ROOT/'better-mc-remake/pack/archive'/('champions-companion-maid-film-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    index=[]
    for p,old in before.items():
        index.append({'path':p.relative_to(ROOT).as_posix(),'existed':old is not None,'sha256':sha(old) if old else None})
        if old is not None:atomic(backup/'before'/p.relative_to(ROOT),old)
    (backup/'before.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
    written=[]
    try:
        for p,data in changes.items():
            assert (p.read_bytes() if p.exists() else None)==before[p],'Concurrent change: '+str(p)
            if data is None:p.unlink()
            else:atomic(p,data)
            written.append(p)
            assert (p.read_bytes() if p.exists() else None)==data
        for p,digest in protected.items():assert sha(p.read_bytes())==digest,'Protected file changed: '+str(p)
    except Exception:
        for p in reversed(written):
            if (p.read_bytes() if p.exists() else None)==plans[p]:
                if before[p] is None:p.unlink()
                else:atomic(p,before[p])
        raise
    report={'files':[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p.read_bytes()) if p.exists() else None,
                      'removed':not p.exists()} for p in plans],
        'natural_chance':0.1,'owned_companion_chance':0.33,
        'natural_tier_weights':[50,25,15,8,2],'owned_companion_tier_weights':[50,25,15,7,3],
        'protected_unchanged':len(protected),'smoke':smoke,'production_server_restarted':False,'published_to_oss':False}
    (backup/'after.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFIED:',len(plans),'deployed files;',len(protected),'protected files unchanged')
    print('BACKUP:',backup)
    print('No core rebuild, no production restart/reload, no packspec edits, no OSS publication')

if __name__=='__main__':main()
