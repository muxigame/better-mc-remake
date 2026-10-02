"""Run exact isolated Core sources with existing 131 JDK/Python, no application install."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
REMOTE='C:/Users/ranzh/Documents/Codex/terminal-sso-dedicated-task4-20261002'
paths={'core':'repos/muxi-game-core-dedicated-sso/src/main/java/net/muxigame/core/feature/login/TerminalPassportServer.java',
       'harness':'repos/muxi-game-core-dedicated-sso/tests/terminal-sso-trust/TerminalTrustHarness.java',
       'trusted':'dedicated-sso/inputs/TrustedAccounts.java'}
checks=''
hashes={}
for name,relative in paths.items():
    digest=hashlib.sha256((ROOT/relative).read_bytes()).hexdigest();hashes[name]=digest
    checks+=f"if((Get-FileHash -LiteralPath '{REMOTE}/{relative}' -Algorithm SHA256).Hash.ToLowerInvariant() -ne '{digest}'){{throw 'Source mismatch: {name}'}};"
script="$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8;"+checks+(
    "& 'C:\\Python38\\python.exe' '{root}/repos/muxi-game-core-dedicated-sso/tests/run_terminal_sso_trust.py' "
    "--java-home 'C:/Users/ranzh/Documents/Codex/terminal-integration-task6-20261001/tools/jdk/jdk-21.0.12.1+1' "
    "--gson 'C:/Users/ranzh/workspace/dev/muxigame/bmc5server/libraries/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar' "
    "--trusted-source '{root}/dedicated-sso/inputs/TrustedAccounts.java';exit $LASTEXITCODE").format(root=REMOTE)
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=8',
    'JBC-1@192.168.110.131','powershell','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],capture_output=True,timeout=75)
log=p.stdout.decode('utf-8-sig',errors='replace')+p.stderr.decode('utf-8-sig',errors='replace')
(ROOT/'dedicated-sso/core-trust-131.log').write_text(log,encoding='utf-8')
count=re.search(r'Dedicated terminal connection assertions: (\d+) passed',log)
if p.returncode or not count:raise SystemExit('131 Core trust tests failed; inspect local test log')
record={'status':'passed','assertions':int(count[1]),'host':'JBC-1@192.168.110.131','source_sha256':hashes,
        'real_elapsed_queue_expiry_test':True,'production_mutation':False,'minecraft_listeners':'fixtures'}
(ROOT/'dedicated-sso/core-trust-131.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))
