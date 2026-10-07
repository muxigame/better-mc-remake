import hashlib,json,shutil,tempfile,unittest
from pathlib import Path
from app.manifest_builder import build_manifest

class PublicTerrainTests(unittest.TestCase):
    def test_real_formal_artifacts_roundtrip_and_rejection(self):
        workspace=Path(__file__).resolve().parents[3];flight=workspace/'muxi-flight'
        with tempfile.TemporaryDirectory(prefix='flight-manifest-test-') as raw:
            root=Path(raw);files={
                'public.zip':flight/'build/single-room-release-20261007/public-half-slot0-0.1.2-iwo-half.1.zip',
                'flight.jar':flight/'build/libs/muxi-flight-0.1.2-iwo-half.1.jar',
                'dh.jar':flight/'build/dh-isolated-20261004/DistantHorizons-3.3.3-1.21.1-fabric-neoforge.jar'}
            for name,source in files.items():shutil.copyfile(source,root/name)
            c={'packagePath':'public.zip','generatorPath':'flight.jar','dhPath':'dh.jar','dimension':'muxi_minigames:flight_iwo_half','metresPerBlock':2,'roomSpacing':49152,'roomSlot':0,'hashedSeed':'h1qnkdsfn63tg','serverFolderKey':'OfflineReleaseTest','demSha256':'62a0f366475ed22641b05826ee3f5167f6015a42ec2ee3abeaf347a9620b1682'}
            for pathkey,hashkey in [('packagePath','packageSha256'),('generatorPath','generatorSha256'),('dhPath','dhSha256')]:c[hashkey]=hashlib.sha256((root/c[pathkey]).read_bytes()).hexdigest()
            spec={'root':str(root),'pack':{'id':'flight-test','version':'1'},'include':[{'glob':'*.jar','policy':'Managed'},{'glob':'*.zip','policy':'Managed'}],'publicTerrainCaches':[c]}
            manifest,_=build_manifest(spec,root/'spec.json',None,log=lambda _:None)
            self.assertEqual([c],manifest['publicTerrainCaches']);self.assertEqual(3,len(manifest['files']))
            for key,value in [('serverFolderKey',''),('serverFolderKey','../escape'),('hashedSeed','wrong'),('packageSha256','0'*64),('generatorSha256','0'*64),('dhSha256','0'*64),('demSha256','0'*64),('roomSlot',1),('metresPerBlock',1)]:
                with self.subTest(key=key,value=value):
                    old=c[key];c[key]=value
                    with self.assertRaises(ValueError):build_manifest(spec,root/'spec.json',None,log=lambda _:None)
                    c[key]=old
            spec['prune']=['Distant_Horizons_server_data']
            with self.assertRaises(ValueError):build_manifest(spec,root/'spec.json',None,log=lambda _:None)
            spec['prune']=[];spec['include'][1]['policy']='Optional'
            with self.assertRaises(ValueError):build_manifest(spec,root/'spec.json',None,log=lambda _:None)

    def test_neutral_first_connection_artifacts(self):
        flight=Path(__file__).resolve().parents[3]/'muxi-flight'
        with tempfile.TemporaryDirectory(prefix='flight-neutral-test-') as raw:
            root=Path(raw)
            sources={'public.zip':flight/'build/first-connection-cache-20261007/public-half-slot0-0.1.2-iwo-half.2.zip','flight.jar':flight/'build/libs/muxi-flight-0.1.2-iwo-half.2.jar','dh.jar':flight/'build/dh-isolated-20261004/DistantHorizons-3.3.3-1.21.1-fabric-neoforge.jar'}
            for name,source in sources.items():shutil.copyfile(source,root/name)
            c={'bindOnConnect':True,'serverIdentity':'https://mc.muxigame.com/default','packagePath':'public.zip','generatorPath':'flight.jar','dhPath':'dh.jar','dimension':'muxi_minigames:flight_iwo_half','metresPerBlock':2,'roomSpacing':49152,'roomSlot':0,'demSha256':'62a0f366475ed22641b05826ee3f5167f6015a42ec2ee3abeaf347a9620b1682'}
            for pathkey,hashkey in [('packagePath','packageSha256'),('generatorPath','generatorSha256'),('dhPath','dhSha256')]:c[hashkey]=hashlib.sha256((root/c[pathkey]).read_bytes()).hexdigest()
            spec={'root':str(root),'pack':{'id':'flight-test','version':'2'},'include':[{'glob':'*.jar','policy':'Managed'},{'glob':'*.zip','policy':'Managed'}],'publicTerrainCaches':[c]}
            manifest,_=build_manifest(spec,root/'spec.json',None,log=lambda _:None)
            self.assertEqual([c],manifest['publicTerrainCaches'])
            for key,value in [('hashedSeed','h1qnkdsfn63tg'),('serverFolderKey','old-qa'),('serverIdentity','https://wrong.invalid/default'),('generatorSha256','0'*64)]:
                with self.subTest(key=key):
                    old=c.get(key);c[key]=value
                    with self.assertRaises(ValueError):build_manifest(spec,root/'spec.json',None,log=lambda _:None)
                    if old is None:c.pop(key)
                    else:c[key]=old

if __name__=='__main__':unittest.main()
