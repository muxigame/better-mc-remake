"""Compile the real JSON/options overlay test offline, without reading NuGet credentials."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / 'tests/skin-overlay-smoke'
out = TEST / 'offline-build'
out.mkdir(exist_ok=True)
dotnet = Path('C:/Program Files/dotnet')
sdk = sorted((dotnet / 'sdk').iterdir(), key=lambda p: tuple(map(int, p.name.split('.'))))[-1]
ref_version = sorted((dotnet / 'packs/Microsoft.NETCore.App.Ref').glob('9.*'),
                     key=lambda p: tuple(map(int, p.name.split('.'))))[-1]
refs = ref_version / 'ref/net9.0'
usings = out / 'GlobalUsings.cs'
usings.write_text('global using System;\nglobal using System.IO;\nglobal using System.Linq;\nglobal using System.Collections.Generic;\n')
assembly = out / 'SkinOverlaySmoke.dll'
args = ['-nostdlib+', '-langversion:latest', '-nullable:enable', '-target:exe', f'-out:{assembly}',
        f'-analyzer:{refs.parents[1] / "analyzers/dotnet/cs/System.Text.Json.SourceGeneration.dll"}',
        *[f'-r:{p}' for p in refs.glob('*.dll')],
        str(ROOT / 'core/ConfigOverlay.cs'), str(ROOT / 'protocol/Manifest.cs'), str(ROOT / 'protocol/ManifestJsonContext.cs'), str(TEST / 'Program.cs'), str(usings)]
argfile = out / 'compile.args'
argfile.write_text('\n'.join('"' + a.replace('\\', '/') + '"' for a in args), encoding='utf-8')
subprocess.run([str(dotnet / 'dotnet.exe'), str(sdk / 'Roslyn/bincore/csc.dll'), '@' + str(argfile)], check=True, timeout=30)
assembly.with_suffix('.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {'tfm': 'net9.0', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '9.0.0'}}}))
subprocess.run([str(dotnet / 'dotnet.exe'), str(assembly), str(ROOT.parent / 'pack/packspec.json')], check=True, timeout=30)
