"""Create a private QA MCEF copy without the global Windows process-kill workaround.

Does not modify the installed/product jar. Normal CefApp shutdown stays intact.
The caller must place the destination in its isolated test instance only.
"""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile

def create_private_copy(source: Path, destination: Path):
    source=source.resolve(strict=True);destination=destination.resolve()
    if source==destination or destination.exists():
        raise ValueError("Use a new private QA destination, never overwrite the installed jar")
    destination.parent.mkdir(parents=True,exist_ok=True)
    disabled=0
    try:
        with zipfile.ZipFile(source) as original,zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as private:
            for member in original.infolist():
                data=original.read(member.filename)
                if member.filename.endswith('mixins.json'):
                    config=json.loads(data)
                    for section in ('client','mixins','server'):
                        entries=config.get(section,[])
                        filtered=[name for name in entries if name.rsplit('.',1)[-1]!='CefWindowsShutdownMixin']
                        disabled+=len(entries)-len(filtered)
                        if section in config:config[section]=filtered
                    data=json.dumps(config).encode()
                private.writestr(member,data)
        if disabled!=1:raise AssertionError("Expected exactly one CefWindowsShutdownMixin")
    except Exception:
        if destination.is_file():destination.unlink()
        raise
    result={'qaOnly':True,'globalKillMixinDisabled':True,'originalSHA256':hashlib.sha256(source.read_bytes()).hexdigest(),'privateSHA256':hashlib.sha256(destination.read_bytes()).hexdigest()}
    destination.with_suffix('.qa-isolation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(create_private_copy(args.source,args.destination)))
