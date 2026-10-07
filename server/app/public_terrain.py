"""Validate terrain bindings before publishing a launcher manifest."""
import hashlib,json,re,zipfile
from pathlib import Path

def validate_public_terrain(root, files, spec):
    caches=spec.get('publicTerrainCaches',[])
    if not caches:return
    prefix='distant_horizons_server_data'
    def cache_path(p):return p.replace('\\','/').strip('/').casefold()
    if any(cache_path(f['path'])==prefix or cache_path(f['path']).startswith(prefix+'/') for f in files):
        raise ValueError('Player DH databases cannot be distribution files')
    if any(cache_path(p) in ['', '.', prefix] or cache_path(p).startswith(prefix+'/') for p in spec.get('prune',[])):
        raise ValueError('Player DH databases cannot be pruned')
    if any(cache_path(o['path'])==prefix or cache_path(o['path']).startswith(prefix+'/') for o in spec.get('overlays',[])):
        raise ValueError('Player DH databases cannot receive overlays')
    bypath={f['path']:f for f in files}
    for c in caches:
        if c.get('dimension')!='muxi_minigames:flight_iwo_half' or c.get('metresPerBlock')!=2 or c.get('roomSpacing')!=49152 or c.get('roomSlot')!=0:
            raise ValueError('Unsupported public terrain identity')
        key=c.get('serverFolderKey','')
        if not key.strip() or len(key)>180 or key in ['.','..'] or key.endswith(('.', ' ')) or any(ord(c)<32 or c in '<>:"/\\|?*' for c in key) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',key,re.I):
            raise ValueError('Explicit verified DH server folder key required')
        paths={}
        for pathkey,hashkey in [('packagePath','packageSha256'),('generatorPath','generatorSha256'),('dhPath','dhSha256')]:
            relative=c.get(pathkey,'');item=bypath.get(relative)
            if not item or item.get('policy')!='Managed' or item.get('optionalDefaultOn'):
                raise ValueError('Terrain artifacts must be nonoptional Managed files: '+relative)
            path=(root/relative).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError('Terrain artifact path escaped root')
            with path.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
            if actual!=c.get(hashkey):raise ValueError('Terrain artifact digest mismatch: '+relative)
            paths[pathkey]=path
        with zipfile.ZipFile(paths['generatorPath']) as z:
            if hashlib.sha256(z.read('assets/muxi_flight/terrain/iwo-elevation-centimetres-i32le.bin.gz')).hexdigest()!=c.get('demSha256'):
                raise ValueError('Generator DEM digest mismatch')
        with zipfile.ZipFile(paths['packagePath']) as z:
            if sorted(z.namelist())!=['manifest.json','terrain.sqlite'] or z.getinfo('manifest.json').file_size>65536 or z.getinfo('terrain.sqlite').file_size>512*1024**2:
                raise ValueError('Invalid public terrain archive')
            m=json.loads(z.read('manifest.json'))
            for key in ['generatorSha256','demSha256','dhSha256','dimension','hashedSeed','metresPerBlock','roomSpacing']:
                if m.get(key)!=c.get(key):raise ValueError('Public terrain deployment mismatch: '+key)
            if m.get('format')!=1 or m.get('supportedSlots')!=[0] or m.get('terrainOnly') is not True or m.get('coordinateFrame')!='absolute-world-blocks':raise ValueError('Terrain scope mismatch')
            with z.open('terrain.sqlite') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
            if actual!=m.get('databaseSha256') or z.getinfo('terrain.sqlite').file_size!=m.get('databaseBytes'):raise ValueError('Database binding mismatch')
