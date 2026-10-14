"""Read-only contract checks against verified runtime IDs and latest approved metadata."""
from pathlib import Path
import hashlib,json,unittest
ROOT=Path(__file__).resolve().parents[3]
RELEASE=ROOT/'muxi-ysm-tools/build/release-six-approved-20261007'
class RegisteredCatalog(unittest.TestCase):
 def test_runtime_ids_match_actual_connected_server_evidence(self):
  proof=json.loads((RELEASE/'ysm-directory-id-verification.json').read_text(encoding='utf-8'))
  catalog=json.loads((ROOT/'muxi-horse-racing/src/main/resources/umamusume_catalog_v1.json').read_text(encoding='utf-8'))
  self.assertTrue(proof['normalExit']);self.assertEqual(proof['testTopology']['networkClients'],1)
  self.assertEqual({b['modelId'] for b in catalog['catalog']},{v['runtime_model_id'] for v in proof['confirmedRuntimeModelIds'] if v['server_set_confirmed']})
 def test_names_and_model_hashes_match_latest_chinese_candidate(self):
  candidate=json.loads((RELEASE/'display-metadata-update-handoff.json').read_text(encoding='utf-8'))
  native=ROOT/'muxi-horse-racing/src/main/resources/umamusume_catalog_v1.json';backend=ROOT/'better-mc-remake/server/app/umamusume_catalog_v1.json'
  self.assertEqual(native.read_bytes(),backend.read_bytes());catalog=json.loads(native.read_text(encoding='utf-8'))
  self.assertEqual(catalog['runtimeArchiveSha256'],candidate['runtimeArchive']['sha256'])
  for b in catalog['catalog']:
   model=next(m for m in candidate['models'] if m['runtimeModelId']==b['modelId'])
   self.assertEqual(b['name'],model['displayName']);self.assertEqual(set(b['modelFilesSha256']),{'ysm.json','models/main.json','models/arm.json','textures/skin.png','animations/main.animation.json'});self.assertEqual(b['modelFilesSha256'],{k:model['sha256'][k] for k in b['modelFilesSha256']})
  self.assertEqual({b['name'] for b in catalog['catalog']},{'无声铃鹿','小栗帽','特别周','黄金船','东海帝王','目白麦昆'})
 def test_chinese_builtin_horse_catalogs_match(self):
  native=ROOT/'muxi-horse-racing/src/main/resources/horse_trade_catalog_v1.json';backend=ROOT/'better-mc-remake/server/app/horse_trade_catalog_v1.json'
  self.assertEqual(json.loads(native.read_text(encoding='utf-8')),json.loads(backend.read_text(encoding='utf-8')))
  for b in json.loads(native.read_text(encoding='utf-8'))['catalog']:self.assertRegex(b['name'],r'[\u4e00-\u9fff]')
if __name__=='__main__':unittest.main()
