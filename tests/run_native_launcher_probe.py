"""Actual RpcHost/TLS issuer/named-pipe probe. Does not start Minecraft or CEF."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import uuid
import zipfile
import httpx
ROOT=Path(__file__).resolve().parents[1]
def main():
    sdk,jdk,workspace=map(Path,sys.argv[1:4]);lab=ROOT/'build'/('native-launcher-probe-'+uuid.uuid4().hex[:12]);lab.mkdir(parents=True)
    ready=lab/'native-ready.json';log=(lab/'backend.log').open('w',encoding='utf-8');backend=None;state=None
    try:
        backend=subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/bootstrap_python_runtime.py'),str(ROOT/'tests/run_terminal_mcef_backend.py'),'--native','--java-home',str(jdk),'--gson',str(workspace/'bmc5server/libraries/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar'),'--trusted-source',str(ROOT.parent/'muxi-minigames/src/main/java/net/muxigame/minigames/TrustedAccounts.java'),'--ready',str(ready),'--report',str(lab/'backend-result.json')],cwd=lab,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
        deadline=time.monotonic()+30
        while not ready.exists():
            if backend.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Native TLS services failed; inspect private backend.log')
            time.sleep(.05)
        state=json.loads(ready.read_text(encoding='utf-8'))
        classes=lab/'java-proof-classes';classes.mkdir()
        sources=[ROOT/'tests/terminal-native-launcher/JavaProofProbe.java',ROOT.parent/'muxi-game-core/src/main/java/net/muxigame/core/client/TerminalCredentialBrokerClient.java',ROOT/'tests/terminal-mcef-qa/java/net/muxigame/terminal/smoke/NativeIssuerEndpoint.java']
        subprocess.run([str(jdk/'bin/javac.exe'),'-encoding','UTF-8','-d',str(classes),*map(str,sources)],check=True,capture_output=True)
        agent_source=ROOT/'tests/terminal-mcef-qa/java/net/muxigame/terminal/smoke/TcpSelectorQaAgent.java'
        subprocess.run([str(jdk/'bin/javac.exe'),'-encoding','UTF-8','-d',str(classes),str(agent_source)],check=True,capture_output=True)
        agent=lab/'tcp-selector-qa-only.jar'
        with zipfile.ZipFile(agent,'w') as jar:
            jar.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\nPremain-Class: net.muxigame.terminal.smoke.TcpSelectorQaAgent\n\n')
            name='net/muxigame/terminal/smoke/TcpSelectorQaAgent.class';jar.write(classes/name,name)
        config={'controller':state['url'],'authURL':state['auth_url'],'siteURL':state['site_url'],'spki':state['spki'],'java':str(jdk/'bin/java.exe'),'javaClasses':str(classes),'javaAgent':str(agent),'truststore':state['truststore'],'launcherState':str(lab/'private-launcher-state'),'report':str(lab/'native-probe-result.json')}
        public=lab/'native-probe-public-config.json';public.write_text(json.dumps(config),encoding='utf-8')
        env=dict(os.environ);env['MUXI_LAUNCHER_QA_CONTROL']=state['launcher_capability'];env['MUXI_GAME_QA_CONTROL']=state['capability']
        launcher=ROOT/'tests/terminal-native-launcher/bin/Debug/net9.0-windows/NativeLauncherQA.dll'
        with (lab/'native-probe.log').open('w',encoding='utf-8') as output:
            result=subprocess.run([str(sdk/'dotnet.exe'),str(launcher),str(public),'--probe-only'],cwd=lab,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=50)
        if result.returncode:raise RuntimeError('Actual native probe failed; inspect '+str(lab/'native-probe.log'))
        report=json.loads((lab/'native-probe-result.json').read_text(encoding='utf-8'));report['lab']=str(lab);print(json.dumps(report))
    finally:
        if state:
            try:httpx.post(state['url'],headers={'X-Muxi-QA-Control':state['capability']},json={'action':'stop'},trust_env=False,timeout=5)
            except Exception:pass
        if backend:
            try:backend.wait(timeout=12)
            except subprocess.TimeoutExpired:backend.terminate();backend.wait(timeout=5)
        log.close()
if __name__=='__main__':main()
