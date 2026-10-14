"""Standalone rule/scoring tests. Does not launch a Minecraft server or client."""
from pathlib import Path
import argparse, subprocess,json
p=argparse.ArgumentParser();p.add_argument('--jdk',type=Path,required=True);a=p.parse_args()
root=Path(__file__).resolve().parents[2];out=root/'build/race-rules';out.mkdir(parents=True,exist_ok=True)
src=root/'src/main/java/net/muxigame/minigames/horse'
sources=[src/'HorseGeometry.java',src/'HorsePace.java',src/'HorseRaceGrid.java',src/'HorseScores.java',root/'tests/race-rules/RaceRulesTest.java',root/'tests/horse-points/HorseScoresQA.java']
subprocess.run([str(a.jdk/'bin/javac.exe'),'-J-XX:ActiveProcessorCount=1','-encoding','UTF-8','-d',str(out),*map(str,sources)],check=True)
results=[]
for cls in ['RaceRulesTest','HorseScoresQA']:
 r=subprocess.run([str(a.jdk/'bin/java.exe'),'-XX:ActiveProcessorCount=1','-cp',str(out),cls],check=True,capture_output=True,text=True)
 print(r.stdout,end='');results.append({'test':cls,'result':r.stdout.strip(),'exitCode':r.returncode})
(root/'docs/diagnosis/rule-tests.json').write_text(json.dumps({'minecraftStarted':False,'tests':results},indent=2)+'\n',encoding='utf-8')
