"""Safety boundaries for the shared local MC runner, independent of installed MC."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import local_mc_runtime as runtime
import local_mc_debug as debug


class OwnershipTest(unittest.TestCase):
    def test_powershell_utf8_bom_input_is_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'command.json';path.write_text('{"type":"observe"}',encoding='utf-8-sig')
            self.assertEqual(runtime.read_json(path),{'type':'observe'})
    def test_existing_or_production_instance_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);source=project/'installed-server';source.mkdir()
            with self.assertRaises(ValueError):runtime.new_lab(project,source,[source])
            lab=project/'build/local-mc-debug/round-one'
            runtime.new_lab(project,lab,[source])
            with self.assertRaises(ValueError):runtime.new_lab(project,lab,[source])
            with self.assertRaises(ValueError):runtime.owned_lab(lab)

    def test_foreign_owner_marker_cannot_be_used_for_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);lab=project/'build/local-mc-debug/round';lab.mkdir(parents=True)
            runtime.write_json(lab/'local-mc-owner.json',{'schema':1,'runId':'fixture','projectRoot':str(project),'instanceRoot':str(project/'foreign')})
            with self.assertRaises(ValueError):runtime.owned_lab(lab)
            self.assertFalse((lab/'stop-request.json').exists())

    def test_role_path_traversal_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);lab=project/'build/local-mc-debug/round';lab.mkdir(parents=True)
            runtime.write_json(lab/'local-mc-owner.json',{'schema':1,'runId':'fixture','projectRoot':str(project),'instanceRoot':str(lab),'roles':['server','../foreign']})
            with self.assertRaises(ValueError):runtime.owned_lab(lab)

    def test_stop_request_only_targets_declared_private_roles(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);lab=project/'build/local-mc-debug/round';lab.mkdir(parents=True)
            marker={'schema':1,'runId':'fixture','projectRoot':str(project),'instanceRoot':str(lab),'roles':['server','host','guest']}
            runtime.write_json(lab/'local-mc-owner.json',marker)
            root,owner=runtime.owned_lab(lab);debug.stop_files(root,owner)
            self.assertEqual(sorted(p.name for p in (lab/'coordinator').iterdir()),['command-guest.json','command-host.json','command-server.json'])
            self.assertTrue(runtime.read_json(lab/'stop-request.json')['normalStopOnly'])

    def test_metadata_inheritance_and_cycle_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            game=Path(temp)
            for version,data in [('base',{'libraries':[],'arguments':{'jvm':['-Xfoo'],'game':['--base']}}),('child',{'inheritsFrom':'base','arguments':{'game':['--child']}})]:
                path=game/'versions'/version;path.mkdir(parents=True);runtime.write_json(path/(version+'.json'),data)
            self.assertEqual(runtime.client_metadata(game,'child')['arguments']['game'],['--base','--child'])
            runtime.write_json(game/'versions/base/base.json',{'inheritsFrom':'child'})
            with self.assertRaises(ValueError):runtime.client_metadata(game,'child')


if __name__=='__main__':unittest.main()
