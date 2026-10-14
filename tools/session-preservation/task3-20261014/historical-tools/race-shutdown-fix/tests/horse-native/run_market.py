"""Separate-JVM transactional sandbox. Does not launch Minecraft or call production APIs."""
from pathlib import Path
import shutil
import argparse,datetime,json,os,subprocess
def split_framework(repo, lab):
    shared=repo.resolve().parent/'muxi-minigames'
    meta=json.loads((shared/'mod.json').read_text(encoding='utf-8'))
    jar=shared/'build/libs'/('muxi-minigames-'+meta['version']+'.jar')
    if not jar.is_file():raise RuntimeError('Build sibling muxi-minigames before running game QA')
    if (lab/'mods').is_dir():shutil.copy2(jar,lab/'mods'/jar.name)
    return jar

def main():
    p=argparse.ArgumentParser();p.add_argument('--jar',type=Path);p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.repo=a.repo.resolve();a.output=a.output.resolve()
    jdk=Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-<PRIVATE_HOST>+1');client=Path(r'C:\Users\ranzh\workspace\dev\muxigame\_client_test\game');server=Path(r'C:\Users\ranzh\workspace\dev\muxigame\bmc5server')
    lab=a.output/('horse-market-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'));lab.mkdir(parents=True);(lab/'HORSE_QA_ONLY').write_text('Mirrored points and abstract stock fixtures only\n');classes=lab/'classes';classes.mkdir()
    framework=split_framework(a.repo,lab)
    jar=a.jar.resolve() if a.jar else next((a.repo/'build/libs').glob('muxi-horse-racing-*.jar'));mapped=[client/'libraries/net/neoforged/neoforge/21.1.250/neoforge-21.1.250-client.jar',client/'libraries/net/minecraft/client/1.21.1-20240808.144430/client-1.21.1-20240808.144430-srg.jar'];libs=list(dict.fromkeys([framework,jar]+mapped+list((server/'libraries').rglob('*.jar'))));cp=os.pathsep.join(map(str,libs));args=['--release','21','-encoding','UTF-8','-proc:none','-cp',cp,'-d',str(classes),str(a.repo/'tests/horse-native/HorseMarketQA.java')];(lab/'compile.args').write_text('\n'.join('"'+v.replace('\\','/')+'"' for v in args),encoding='utf-8');subprocess.run([str(jdk/'bin/javac.exe'),'@'+str(lab/'compile.args')],check=True)
    args=['-Xmx256M','-Dfile.encoding=UTF-8','-Dmuxi.horse.economyLab=true','-cp',str(classes)+os.pathsep+cp,'net.muxigame.minigames.horseqa.HorseMarketQA',str(lab)];file=lab/'launch.args';file.write_text('\n'.join('"'+v.replace('\\','/')+'"' for v in args),encoding='utf-8');subprocess.run([str(jdk/'bin/java.exe'),'@'+str(file)],cwd=lab,check=True);file.write_text(file.read_text(encoding='utf-8')+'\n"resume"\n',encoding='utf-8');subprocess.run([str(jdk/'bin/java.exe'),'@'+str(file)],cwd=lab,check=True)
    first=json.loads((lab/'market-first-result.json').read_text(encoding='utf-8'));second=json.loads((lab/'market-resume-result.json').read_text(encoding='utf-8'));first['checks']+=second['checks'];first.update(actualSeparateJvm=True,lab=str(lab),success=first['success'] and second['success']);(lab/'summary.json').write_text(json.dumps(first,indent=2),encoding='utf-8');print(json.dumps({'success':first['success'],'checks':len(first['checks']),'lab':str(lab)}))
if __name__=='__main__':main()
