import os,sys,runpy,tempfile
from pathlib import Path
handles=[os.add_dll_directory(str(p)) for p in (Path(sys.executable).parent,Path(sys.executable).parent/'DLLs') if p.is_dir()]
base=Path(r'C:\Users\ranzh\Documents\Codex\2026-10-02\task\dev-sync-20261002\muxi-auth')
temp=tempfile.TemporaryDirectory(prefix='131-direct-auth-')
os.environ['MUXI_DATABASE_PATH']=str(Path(temp.name)/'auth.db')
os.environ['MUXI_SIGNING_KEY_PATH']=str(Path(temp.name)/'oidc.pem')
sys.path.insert(0,str(base))
sys.argv=['unittest','discover','-s',str(base/'tests'),'-v']
runpy.run_module('unittest',run_name='__main__')
