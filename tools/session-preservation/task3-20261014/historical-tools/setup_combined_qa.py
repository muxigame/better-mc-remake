from pathlib import Path
import shutil
root=Path('next-release-20261012/fullpack-smoke');p=root/'qa-project'
for sub in ['scripts','tests/local-mc-debug']:shutil.copytree(Path('runtime-tools')/sub,p/sub,dirs_exist_ok=True)
s=Path('C:/Users/ranzh/workspace/dev/muxigame/muxi-game-core/build/teleport-network-20261012-020620/native-project/tests/local-mc-debug')
for f in s.glob('TeleportProbe*.java'):(p/'tests/local-mc-debug'/f.name).write_text(f.read_text().replace('DebugHost','10000'),encoding='utf-8')
s=Path('C:/Users/ranzh/Documents/Codex/2026-10-02/task/shop-ui-20261011/published-five-v3/native-execution')
for f in (s/'qa-source').rglob('*.java'):
 t=f.read_text().replace('modid="muxi_horse_racing"','modid="local_mc_debug_qa"')
 if f.name=='UmamusumeShopClientQA.java':t=t.replace('||done)return','||done||!Files.exists(root().resolve("begin-shop")))return')
 dest=p/'tests/local-mc-debug/shop'/f.relative_to(s/'qa-source');dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(t,encoding='utf-8')
shutil.copy2(s/'shop-client.js',root/'shop-client.js');shutil.copy2(Path('C:/Users/ranzh/workspace/dev/muxigame/muxi-tower-defense/build/tower-visual-qa.jar'),root/'tower-visual-qa.jar')
print('QA sources isolated')
