"""Configure DLL search only in this task-owned test process, then run its script."""
import os
from pathlib import Path
import sys

runtime=Path(sys.executable).resolve().parent
handles=[]
if hasattr(os,'add_dll_directory'):handles.append(os.add_dll_directory(str(runtime)))
import runpy
script=Path(sys.argv[1]).resolve();sys.argv=sys.argv[1:]
runpy.run_path(str(script),run_name='__main__')
