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
 def input_entries(self):
  import json
  entries=dict(self.entries)
  entries['META-INF/neoforge.mods.toml'] += b'\n[[mixins]]\nconfig="muxi_terminal.input.mixins.json"\n'
  entries[compat.INPUT_CONFIG]=json.dumps(compat.INPUT_MANIFEST).encode()
  entries[compat.INPUT_CLASS]=b'client keyboard fixture'
  return entries
 def input_rejected(self,entries):
  with self.assertRaises(ValueError):compat.verify(self.old,self.jar('input.jar',entries))
 def test_exact_client_keyboard_manifest_allowed(self):
  self.assertTrue(compat.verify(self.old,self.jar('input.jar',self.input_entries()))['identical_server_contract'])
 def test_client_manifest_plus_changed_server_rejected(self):
  entries=self.input_entries();entries['net/muxigame/terminal/MuxiTerminal.class']=b'new registration';self.input_rejected(entries)
 def test_client_manifest_plus_network_change_rejected(self):
  entries=self.input_entries();entries['net/muxigame/terminal/network/Protocol.class']=b'v2';self.input_rejected(entries)
 def test_client_manifest_plus_dependency_change_rejected(self):
  entries=self.input_entries();entries['META-INF/neoforge.mods.toml']=entries['META-INF/neoforge.mods.toml'].replace(b'[2.1.6,)',b'[3,)');self.input_rejected(entries)
 def test_client_manifest_plus_other_toml_change_rejected(self):
  entries=self.input_entries();entries['META-INF/neoforge.mods.toml']=b'displayTest="IGNORE_ALL_VERSION"\n'+entries['META-INF/neoforge.mods.toml'];self.input_rejected(entries)
 def test_client_manifest_server_list_rejected(self):
  import json
  entries=self.input_entries();value={**compat.INPUT_MANIFEST,'server':['ServerMixin']};entries[compat.INPUT_CONFIG]=json.dumps(value).encode();self.input_rejected(entries)
 def test_client_manifest_common_list_rejected(self):
  import json
  entries=self.input_entries();entries[compat.INPUT_CONFIG]=json.dumps({**compat.INPUT_MANIFEST,'mixins':['CommonMixin']}).encode();self.input_rejected(entries)
 def test_client_manifest_plugin_rejected(self):
  import json
  entries=self.input_entries();entries[compat.INPUT_CONFIG]=json.dumps({**compat.INPUT_MANIFEST,'plugin':'UnsafePlugin'}).encode();self.input_rejected(entries)
 def test_client_manifest_other_class_rejected(self):
  import json
  entries=self.input_entries();entries[compat.INPUT_CONFIG]=json.dumps({**compat.INPUT_MANIFEST,'client':['OtherMixin']}).encode();self.input_rejected(entries)
 def test_client_manifest_require_zero_rejected(self):
  import json
  entries=self.input_entries();entries[compat.INPUT_CONFIG]=json.dumps({**compat.INPUT_MANIFEST,'injectors':{'defaultRequire':0}}).encode();self.input_rejected(entries)
 def test_client_manifest_require_boolean_rejected(self):
  import json
  entries=self.input_entries();entries[compat.INPUT_CONFIG]=json.dumps({**compat.INPUT_MANIFEST,'injectors':{'defaultRequire':True}}).encode();self.input_rejected(entries)
 def test_client_manifest_duplicate_json_key_rejected(self):
  entries=self.input_entries();entries[compat.INPUT_CONFIG]=entries[compat.INPUT_CONFIG][:-1]+b',"server":[],"server":["Bad"]}';self.input_rejected(entries)
 def test_client_manifest_duplicate_registration_rejected(self):
  entries=self.input_entries();entries['META-INF/neoforge.mods.toml']+=b'\n[[mixins]]\nconfig="muxi_terminal.input.mixins.json"\n';self.input_rejected(entries)
 def test_client_manifest_extra_registration_attribute_rejected(self):
  entries=self.input_entries();entries['META-INF/neoforge.mods.toml']+=b'side="BOTH"\n';self.input_rejected(entries)
 def test_client_manifest_orphan_rejected(self):
  entries=self.input_entries();entries['META-INF/neoforge.mods.toml']=self.entries['META-INF/neoforge.mods.toml'];self.input_rejected(entries)
 def test_client_manifest_missing_config_rejected(self):
  entries=self.input_entries();del entries[compat.INPUT_CONFIG];self.input_rejected(entries)
 def test_client_manifest_missing_class_rejected(self):
  entries=self.input_entries();del entries[compat.INPUT_CLASS];self.input_rejected(entries)

if __name__=='__main__':unittest.main()
