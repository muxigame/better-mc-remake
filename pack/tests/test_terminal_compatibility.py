import importlib.util,tempfile,unittest,zipfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('compat',Path(__file__).resolve().parents[1]/'verify-terminal-compatibility.py')
compat=importlib.util.module_from_spec(spec);spec.loader.exec_module(compat)
class TerminalCompatibilityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.entries={'net/muxigame/terminal/MuxiTerminal.class':b'registry','net/muxigame/terminal/item/PlayerTerminalItem.class':b'item',
   'META-INF/neoforge.mods.toml':b'modId="muxi_terminal"\nversion="0.2.0"\nversionRange="[2.1.6,)"\n',
   'META-INF/LICENSE':b'MIT\r\n','data/muxi_terminal/tags/items.json':b'{}',
   'net/muxigame/terminal/client/TerminalClient.class':b'old','assets/muxi_terminal/html/terminal/app.js':b'old'}
  self.old=self.jar('old.jar',self.entries)
 def tearDown(self):self.tmp.cleanup()
 def jar(self,name,entries):
  path=self.root/name
  with zipfile.ZipFile(path,'w') as z:
   for key,value in entries.items():z.writestr(key,value)
  return path
 def check_change(self,key,value,rejected=True):
  entries={**self.entries,key:value};new=self.jar('new.jar',entries)
  if rejected:
   with self.assertRaises(ValueError):compat.verify(self.old,new)
  else:self.assertTrue(compat.verify(self.old,new)['identical_server_contract'])
 def test_client_code_allowed(self):self.check_change('net/muxigame/terminal/client/TerminalClient.class',b'fixed',False)
 def test_html_allowed(self):self.check_change('assets/muxi_terminal/html/terminal/app.js',b'fixed',False)
 def test_version_allowed(self):self.check_change('META-INF/neoforge.mods.toml',self.entries['META-INF/neoforge.mods.toml'].replace(b'0.2.0',b'0.2.1'),False)
 def test_copyright_line_endings_allowed(self):self.check_change('META-INF/LICENSE',b'MIT\n',False)
 def test_copyright_text_protected(self):self.check_change('META-INF/LICENSE',b'GPL\n')
 def test_registry_rejected(self):self.check_change('net/muxigame/terminal/MuxiTerminal.class',b'changed')
 def test_item_rejected(self):self.check_change('net/muxigame/terminal/item/PlayerTerminalItem.class',b'changed')
 def test_data_rejected(self):self.check_change('data/muxi_terminal/tags/items.json',b'{"new":true}')
 def test_dependency_rejected(self):self.check_change('META-INF/neoforge.mods.toml',self.entries['META-INF/neoforge.mods.toml'].replace(b'[2.1.6,)',b'[9,)'))
 def test_added_common_code_rejected(self):self.check_change('net/muxigame/terminal/network/NewPayload.class',b'new')
if __name__=='__main__':unittest.main()
