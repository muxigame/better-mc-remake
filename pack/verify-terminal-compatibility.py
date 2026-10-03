"""Allow an explicitly requested terminal client update only with an identical server contract."""
from __future__ import annotations
import argparse,hashlib,json,re,tomllib,zipfile
from pathlib import Path

INPUT_CONFIG = 'muxi_terminal.input.mixins.json'
INPUT_CLASS = 'net/muxigame/terminal/client/input/mixin/HeldTerminalKeyboardMixin.class'
INPUT_MANIFEST = {
    'required': True, 'minVersion': '0.8',
    'package': 'net.muxigame.terminal.client.input.mixin',
    'compatibilityLevel': 'JAVA_21', 'client': ['HeldTerminalKeyboardMixin'],
    'injectors': {'defaultRequire': 1},
}
INPUT_REGISTRATION = re.compile(r'\n\[\[mixins\]\]\nconfig="muxi_terminal\.input\.mixins\.json"\n\Z')

def client_input_manifest(archive: zipfile.ZipFile) -> bool:
    """The single audited KeyboardHandler config; never a general TOML/Mixin exclusion."""
    document = tomllib.loads(archive.read('META-INF/neoforge.mods.toml').decode('utf-8'))
    registrations = document.get('mixins', [])
    if not isinstance(registrations, list):
        raise ValueError('Invalid Mixin registrations')
    matches = [row for row in registrations if isinstance(row, dict) and row.get('config') == INPUT_CONFIG]
    present = INPUT_CONFIG in archive.namelist()
    if not matches and not present:
        return False
    if len(matches) != 1 or matches[0] != {'config': INPUT_CONFIG} or not present:
        raise ValueError('Expected exactly one plain client input registration and its manifest')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate input manifest key: ' + key)
            result[key] = value
        return result
    manifest = json.loads(archive.read(INPUT_CONFIG).decode('utf-8'), object_pairs_hook=unique_object)
    if manifest != INPUT_MANIFEST or type(manifest.get('required')) is not bool or type(manifest.get('injectors', {}).get('defaultRequire')) is not int:
        raise ValueError('Unrecognized input Mixin manifest; server/common/plugin changes are protected')
    if INPUT_CLASS not in archive.namelist():
        raise ValueError('Client input Mixin class is missing')
    return True

def protected_entries(jar: Path) -> dict[str,str]:
    with zipfile.ZipFile(jar) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate JAR entries')
        input_manifest=client_input_manifest(archive)
        result={}
        for name in names:
            if (name==INPUT_CONFIG and input_manifest) or name.endswith('/') or name.startswith('net/muxigame/terminal/client/') or name.startswith('assets/muxi_terminal/html/terminal/'):
                continue
            data=archive.read(name)
            if name=='META-INF/neoforge.mods.toml':
                text=data.decode('utf-8').replace('\r\n','\n')
                text,count=re.subn(r'(?m)^version="[^"]+"$', 'version="CLIENT_PATCH"',text)
                if count!=1:raise ValueError('Expected exactly one terminal version')
                if input_manifest:
                    text,registrations=INPUT_REGISTRATION.subn('',text)
                    if registrations!=1:raise ValueError('Unexpected client input registration placement/text')
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
            'allowed_client_mixin_configs':[INPUT_CONFIG],
            'scope':'Only terminal client classes, local HTML assets, module version and the exact audited client-only KeyboardHandler manifest/registration may differ; all other TOML and server/common/network entries protected'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',type=Path,required=True);parser.add_argument('--client',type=Path,required=True)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    try:report=verify(args.server,args.client)
    except (ValueError,OSError,zipfile.BadZipFile) as error:parser.exit(1,str(error)+'\n')
    if args.out:args.out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Terminal client update: '+str(len(report['protected_entries']))+' protected server entries are byte-identical.')
