import importlib.util, tempfile, unittest, zipfile
from pathlib import Path
module=Path(__file__).resolve().parents[1]/'pack/verify-tacz-runtime-mirror.py'
spec=importlib.util.spec_from_file_location('tacz_mirror',module);mirror=importlib.util.module_from_spec(spec);spec.loader.exec_module(mirror)

class RuntimeMirrorTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.entries={'data/phoenix/guns/gun.json':b'{"damage":10}', 'assets/phoenix/textures/gun.png':b'png-data'}
        self.client=self.make('client.zip',self.entries,zipfile.ZIP_STORED)
    def make(self,name,entries,compression=zipfile.ZIP_DEFLATED):
        file=self.root/name
        with zipfile.ZipFile(file,'w',compression=compression) as archive:
            for key,value in entries.items():archive.writestr(key,value)
        return file
    def test_rewrapped_archive_keeps_exact_resources(self):
        server=self.make('client+1.21.1.zip',self.entries)
        self.assertNotEqual(server.read_bytes(),self.client.read_bytes())
        self.assertEqual(mirror.verify(server,self.client),2)
    def test_changed_gun_damage_rejected(self):
        entries=dict(self.entries);entries['data/phoenix/guns/gun.json']=b'{"damage":11}'
        with self.assertRaises(ValueError):mirror.verify(self.make('server.zip',entries),self.client)
    def test_missing_resource_rejected(self):
        with self.assertRaises(ValueError):mirror.verify(self.make('server.zip',{'data/phoenix/guns/gun.json':self.entries['data/phoenix/guns/gun.json']}),self.client)
    def test_added_resource_rejected(self):
        entries=dict(self.entries);entries['data/phoenix/guns/unreviewed.json']=b'{}'
        with self.assertRaises(ValueError):mirror.verify(self.make('server.zip',entries),self.client)
    def test_duplicate_resource_rejected(self):
        server=self.make('server.zip',self.entries)
        with zipfile.ZipFile(server,'a') as archive:archive.writestr('data/phoenix/guns/gun.json',b'{"damage":99}')
        with self.assertRaises(ValueError):mirror.verify(server,self.client)

if __name__=='__main__':unittest.main()
