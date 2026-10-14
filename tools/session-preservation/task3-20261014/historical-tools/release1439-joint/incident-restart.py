from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).parent))
from remote import remote
n=Path(__file__).parent
raw=remote((n/'incident-restart.ps1').read_text('utf8'),timeout=1000)
(n/'incident-restart-result.json').write_text(raw,'utf8')
print(raw)
