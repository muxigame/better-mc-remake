"""Bundle existing public Python runtime/dependencies for task-owned 131 tests only.

No app, .env, database, private key, SSH configuration, user profile or production
file enters this archive. No packages are installed system-wide or downloaded.
"""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BASE=Path(r'C:\ProgramData\miniconda3')
PACKAGES=Path(r'C:\Users\Administrator\WorkSpace\muxigame\muxi-auth\.venv\Lib\site-packages')
TARGET=ROOT/'dedicated-sso/python-test-runtime.zip'
files={}
for name in ('python.exe','python312.dll','python3.dll','LICENSE.txt'):
    p=BASE/name
    if p.is_file():files[name]=p
for p in BASE.glob('*.dll'):files[p.name]=p
for p in (BASE/'DLLs').iterdir():
    if p.is_file() and p.suffix.lower() in ('.pyd','.dll'):files['DLLs/'+p.name]=p
for name in ('libssl-3-x64.dll','libcrypto-3-x64.dll','sqlite3.dll','ffi.dll','ffi-7.dll','ffi-8.dll','libbz2.dll','liblzma.dll'):
    p=BASE/'Library/bin'/name
    if p.is_file():files[p.name]=p
for p in (BASE/'Lib').rglob('*'):
    if not p.is_file():continue
    relative=p.relative_to(BASE/'Lib')
    if 'site-packages' in relative.parts or '__pycache__' in relative.parts or p.suffix=='.pyc':continue
    files['Lib/'+relative.as_posix()]=p
for p in PACKAGES.rglob('*'):
    if not p.is_file():continue
    relative=p.relative_to(PACKAGES)
    if '__pycache__' in relative.parts or p.suffix=='.pyc':continue
    files['Lib/site-packages/'+relative.as_posix()]=p
for name in files:
    assert Path(name).name!='.env' and not name.lower().endswith(('.db','.sqlite','.key','.pfx'))
    assert 'id_rsa' not in name and 'id_ed25519' not in name
with zipfile.ZipFile(TARGET,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for name,p in sorted(files.items()):z.write(p,name)
with zipfile.ZipFile(TARGET) as z:assert z.testzip() is None
record={'purpose':'isolated 131 synthetic tests only, not a release/runtime installer',
        'sha256':hashlib.sha256(TARGET.read_bytes()).hexdigest(),'bytes':TARGET.stat().st_size,
        'files':len(files),'applications_or_credentials_included':False,'system_wide_install':False}
(ROOT/'dedicated-sso/python-test-runtime.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))
