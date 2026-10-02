"""Load a self-contained local Python runtime's DLLs before running a QA script."""
import os
from pathlib import Path
import runpy
import sys
handles=[]
for directory in (Path(sys.executable).parent,Path(sys.executable).parent/'DLLs'):
    if directory.is_dir() and hasattr(os,'add_dll_directory'):handles.append(os.add_dll_directory(str(directory)))
script=Path(sys.argv.pop(1));sys.path.insert(0,str(script.parent));sys.argv[0]=str(script)
runpy.run_path(str(script),run_name='__main__')
