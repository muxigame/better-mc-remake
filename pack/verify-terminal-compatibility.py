"""Allow an explicitly requested terminal client update only with an identical server contract."""
from __future__ import annotations
import argparse,hashlib,json,re,zipfile
from pathlib import Path

def protected_entries(jar: Path) -> dict[str,str]:
    with zipfile.ZipFile(jar) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate JAR entries')
        result={}
        for name in names:
            if name.endswith('/') or name.startswith('net/muxigame/terminal/client/') or name.startswith('assets/muxi_terminal/html/terminal/'):
                continue
            data=archive.read(name)
            if name=='META-INF/neoforge.mods.toml':
                text=data.decode('utf-8').replace('\r\n','\n')
                text,count=re.subn(r'(?m)^version="[^"]+"$', 'version="CLIENT_PATCH"',text)
                if count!=1:raise ValueError('Expected exactly one terminal version')
                data=text.encode('utf-8')
            elif name in {'META-INF/LICENSE','META-INF/THIRD_PARTY_NOTICES.md'}:
                data=data.decode('utf-8-sig').replace('\r\n','\n').encode('utf-8')
            result[name]=hashlib.sha256(data).hexdigest()
        required={'net/muxigame/terminal/MuxiTerminal.class','net/muxigame/terminal/item/PlayerTerminalItem.class','META-INF/neoforge.mods.toml'}
        if not required.issubset(result):raise ValueError('Terminal server contract is incomplete')
        return result

def verify(server: Path,client: Path) -> dict:
    old,new=protected_entries(server),protected_entries(client)
    differences=sorted(name for name in old.keys()|new.keys() if old.get(name)!=new.get(name))
    if differences:raise ValueError('Terminal server contract changed: '+', '.join(differences))
    return {'server':str(server),'client':str(client),'protected_entries':old,
            'identical_server_contract':True,'server_sha256':hashlib.sha256(server.read_bytes()).hexdigest(),
            'client_sha256':hashlib.sha256(client.read_bytes()).hexdigest(),
            'scope':'Only terminal client classes, local HTML assets and module version may differ; copyright text line endings normalized; all other bytes protected'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',type=Path,required=True);parser.add_argument('--client',type=Path,required=True)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    try:report=verify(args.server,args.client)
    except (ValueError,OSError,zipfile.BadZipFile) as error:parser.exit(1,str(error)+'\n')
    if args.out:args.out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Terminal client update: '+str(len(report['protected_entries']))+' protected server entries are byte-identical.')
