"""Role-profile CLI preparation tests: no Java, process discovery, sockets or MC."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
try:
    import tomllib
except ImportError:
    tomllib = None  # Run TOML checks with Python 3.11+; runner itself supports older Python.
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import local_mc_debug as debug


class ShaderProfileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.inputs = self.project / 'inputs'
        self.inputs.mkdir()
        self.server = self.inputs / 'runtime'
        self.game = self.inputs / 'game'
        self.jdk = self.inputs / 'jdk'
        self.natives = self.inputs / 'natives'
        for path in (self.server, self.game, self.jdk / 'bin', self.natives):
            path.mkdir(parents=True)
        (self.jdk / 'bin' / ('java.exe' if os.name == 'nt' else 'java')).write_bytes(b'never executed')
        self.agent = self.jar('agent', 'qa-agent.jar')
        self.lab = self.project / 'build/local-mc-debug/profile'

    def jar(self, folder, name, data=None):
        path = self.inputs / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data or (folder + '/' + name).encode())
        return path

    def packs(self, name='Better MC - Low'):
        packs = self.inputs / 'shaderpacks'
        (packs / name).mkdir(parents=True)
        (packs / name / 'shaders').mkdir()
        return packs

    def invoke(self, *extra):
        argv = ['run', '--project-root', str(self.project), '--instance-root', str(self.lab),
                '--server-runtime', str(self.server), '--client-game', str(self.game),
                '--java-home', str(self.jdk), '--natives-dir', str(self.natives),
                '--port', '25931', '--accept-eula', '--mode', 'hold', *map(str, extra)]
        output = io.StringIO()
        with ExitStack() as stack:
            # Real parsing, filesystem preparation, artifact copying, hashes and marker writes.
            stack.enter_context(patch.object(debug.subprocess, 'run', return_value=type('Version', (), {'stdout': '', 'stderr': 'openjdk version "21.0.12"'})()))
            popen = stack.enter_context(patch.object(debug.subprocess, 'Popen', side_effect=RuntimeError('TEST_STOP_BEFORE_JVM')))
            stack.enter_context(patch.object(debug.rt, 'resources', return_value={'availableMemoryMiB': 999999, 'gpuTelemetry': []}))
            stack.enter_context(patch.object(debug.rt, 'port_free'))
            stack.enter_context(patch.object(debug.rt, 'client_metadata', return_value={}))
            stack.enter_context(patch.object(debug.rt, 'client_libraries', return_value=[]))
            stack.enter_context(patch.object(debug.rt, 'javac_classpath', return_value=[]))
            stack.enter_context(patch.object(debug.rt, 'compile_agent', return_value=self.agent))
            stack.enter_context(patch.object(debug.rt, 'server_arguments', return_value=[]))
            stack.enter_context(patch.object(debug.rt, 'client_arguments', return_value=[]))
            with redirect_stdout(output):
                result = debug.main(argv)
        self.assertEqual(result, 1)  # Deliberate pre-JVM stop or rejected preparation, never native success.
        return popen.call_count, output.getvalue()

    def marker(self):
        return json.loads((self.lab / 'local-mc-owner.json').read_text(encoding='utf-8'))

    def test_cli_routes_mods_to_both_clients_and_server_with_scoped_hashes(self):
        shared = self.jar('common', 'common.jar')
        client = self.jar('client', 'render.jar')
        server = self.jar('server', 'server.jar')
        calls, output = self.invoke('--mod', shared, '--client-mod', client, '--server-mod', server)
        self.assertEqual(calls, 1, output)
        for role, expected in [('server', [shared, server, self.agent]), ('host', [shared, client, self.agent]), ('guest', [shared, client, self.agent])]:
            folder = self.lab / role / 'mods'
            self.assertEqual(set(p.name for p in folder.iterdir()), {p.name for p in expected})
            for source in expected:
                self.assertEqual((folder / source.name).read_bytes(), source.read_bytes())
        self.assertEqual(self.marker()['inputArtifacts'], [
            {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'scope': scope}
            for path, scope in [(shared, 'both'), (client, 'client'), (server, 'server')]])

    def test_defaults_remain_without_shader_configuration(self):
        calls, output = self.invoke()
        self.assertEqual(calls, 1, output)
        self.assertEqual(self.marker()['clientSettings'], {'renderDistance': 3, 'simulationDistance': 3, 'maxFps': 30, 'shaderPack': None, 'graphicsMode': 0})
        for role in ['host', 'guest']:
            options = (self.lab / role / 'options.txt').read_text()
            for setting in ['renderDistance:3', 'simulationDistance:3', 'maxFps:30', 'graphicsMode:0']:
                self.assertIn(setting + '\n', options)
        for role in ['server', 'host', 'guest']:
            self.assertFalse((self.lab / role / 'config/iris.properties').exists())
            self.assertFalse((self.lab / role / 'config/PasterDream-Client.toml').exists())

    def check_pasterdream_ui(self, enabled):
        calls, output = self.invoke('--client-pasterdream-ui', 'true' if enabled else 'false')
        self.assertEqual(calls, 1, output)
        self.assertIs(self.marker()['clientSettings']['pasterdreamUi'], enabled)
        for role in ['host', 'guest']:
            config = tomllib.loads((self.lab / role / 'config/PasterDream-Client.toml').read_text(encoding='utf-8'))
            self.assertEqual(config, {'HUD': {'enable mod ui': enabled}})
        self.assertFalse((self.lab / 'server/config/PasterDream-Client.toml').exists())

    @unittest.skipUnless(tomllib is not None, 'TOML validation requires Python 3.11+')
    def test_explicit_pasterdream_ui_disabled_is_recorded_and_client_only(self):
        self.check_pasterdream_ui(False)

    @unittest.skipUnless(tomllib is not None, 'TOML validation requires Python 3.11+')
    def test_explicit_pasterdream_ui_enabled_is_recorded_and_client_only(self):
        self.check_pasterdream_ui(True)

    def test_shader_and_explicit_settings_are_client_only(self):
        packs = self.packs()
        calls, output = self.invoke('--data-dir', packs, '--shader-pack', 'Better MC - Low',
                                    '--client-render-distance', 12, '--client-simulation-distance', 10, '--client-max-fps', 60, '--client-graphics-mode', 1)
        self.assertEqual(calls, 1, output)
        self.assertEqual(self.marker()['clientSettings'], {'renderDistance': 12, 'simulationDistance': 10, 'maxFps': 60, 'shaderPack': 'Better MC - Low', 'graphicsMode': 1})
        for role in ['host', 'guest']:
            properties = dict(line.split('=', 1) for line in (self.lab / role / 'config/iris.properties').read_text().splitlines())
            self.assertEqual(properties, {'enableShaders': 'true', 'allowUnknownShaders': 'false', 'disableUpdateMessage': 'true', 'maxShadowRenderDistance': '16', 'shaderPack': 'Better MC - Low'})
            options = (self.lab / role / 'options.txt').read_text()
            for setting in ['renderDistance:12', 'simulationDistance:10', 'maxFps:60', 'graphicsMode:1']:
                self.assertIn(setting + '\n', options)
        self.assertFalse((self.lab / 'server/config/iris.properties').exists())
        self.assertFalse((self.lab / 'server/options.txt').exists())
        self.assertIn('view-distance=3\n', (self.lab / 'server/server.properties').read_text())

    def test_cli_rejects_shader_paths_and_property_injection_before_preparation(self):
        for name in ['', '.', '..', '../outside', 'folder/pack', 'folder\\pack', 'C:pack', 'pack=bad', 'pack\nextra=true', 'pack\rnext', 'pack\0name', ' leading', 'trailing ']:
            with self.subTest(name=repr(name)):
                calls, output = self.invoke('--shader-pack', name)
                self.assertEqual(calls, 0, output)
                self.assertIn('plain copied filename', output)
                self.assertFalse(self.lab.exists())

    def test_cli_rejects_missing_selected_pack_before_any_launch(self):
        calls, output = self.invoke('--data-dir', self.packs(), '--shader-pack', 'Missing')
        self.assertEqual(calls, 0, output)
        self.assertFalse((self.lab / 'host/config/iris.properties').exists())

    def test_cli_rejects_invalid_settings_before_preparation(self):
        for flag, values in [('--client-render-distance', [1, 33]), ('--client-simulation-distance', [1, 33]), ('--client-max-fps', [0, 261]), ('--client-graphics-mode', [-1, 3])]:
            for value in values:
                with self.subTest(flag=flag, value=value):
                    calls, output = self.invoke(flag, value)
                    self.assertEqual(calls, 0, output)
                    self.assertIn('outside', output)
                    self.assertFalse(self.lab.exists())

    def test_same_side_duplicate_between_shared_and_client_is_rejected(self):
        shared = self.jar('common', 'duplicate.jar')
        client = self.jar('client', 'duplicate.jar')
        calls, output = self.invoke('--mod', shared, '--client-mod', client)
        self.assertEqual(calls, 0, output)
        self.assertIn('Duplicate mod jar filename', output)

    def test_same_side_duplicate_between_shared_and_server_is_rejected(self):
        shared = self.jar('common', 'duplicate.jar')
        server = self.jar('server', 'duplicate.jar')
        calls, output = self.invoke('--mod', shared, '--server-mod', server)
        self.assertEqual(calls, 0, output)
        self.assertIn('Duplicate mod jar filename', output)

    def test_disjoint_sides_may_use_same_filename_with_distinct_bytes(self):
        client = self.jar('client', 'variant.jar')
        server = self.jar('server', 'variant.jar')
        calls, output = self.invoke('--client-mod', client, '--server-mod', server)
        self.assertEqual(calls, 1, output)
        self.assertEqual((self.lab / 'host/mods/variant.jar').read_bytes(), client.read_bytes())
        self.assertEqual((self.lab / 'server/mods/variant.jar').read_bytes(), server.read_bytes())

    @unittest.skipUnless(tomllib is not None, 'TOML validation requires Python 3.11+')
    def test_dependency_override_generates_both_role_configs_and_marker(self):
        calls, output = self.invoke('--dependency-override', 'sable=-scalablelux')
        self.assertEqual(calls, 1, output)
        self.assertEqual(self.marker()['dependencyOverrides'], ['sable=-scalablelux'])
        for role in ['server', 'host', 'guest']:
            config = tomllib.loads((self.lab / role / 'config/fml.toml').read_text(encoding='utf-8'))
            self.assertEqual(config['dependencyOverrides'], {'sable': ['-scalablelux']})
            self.assertFalse(config['earlyWindowControl'])
            self.assertFalse(config['versionCheck'])

    @unittest.skipUnless(tomllib is not None, 'TOML validation requires Python 3.11+')
    def test_no_dependency_override_by_default(self):
        calls, output = self.invoke()
        self.assertEqual(calls, 1, output)
        self.assertEqual(self.marker()['dependencyOverrides'], [])
        for role in ['server', 'host', 'guest']:
            config = tomllib.loads((self.lab / role / 'config/fml.toml').read_text(encoding='utf-8'))
            self.assertNotIn('dependencyOverrides', config)

    def test_dependency_override_rejects_injection_and_excess_before_preparation(self):
        invalid = ['', 'sable=scalablelux', 'sable=-scalablelux\nnext=true',
                   'sable=-scalablelux\r', 'sable=["-scalablelux"]', 'sable=-../outside',
                   'sable=-scalablelux;other=true', '[section]=-dependency']
        for entry in invalid:
            with self.subTest(entry=repr(entry)):
                calls, output = self.invoke('--dependency-override', entry)
                self.assertEqual(calls, 0, output)
                self.assertIn('Dependency override must be', output)
                self.assertFalse(self.lab.exists())
        args = [arg for _ in range(33) for arg in ['--dependency-override', 'sable=-scalablelux']]
        calls, output = self.invoke(*args)
        self.assertEqual(calls, 0, output)
        self.assertIn('Too many dependency overrides', output)
        self.assertFalse(self.lab.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows filename equivalence')
    def test_windows_same_side_case_variant_duplicate_is_rejected(self):
        shared = self.jar('common', 'Variant.jar')
        client = self.jar('client', 'variant.jar')
        calls, output = self.invoke('--mod', shared, '--client-mod', client)
        self.assertEqual(calls, 0, output)
        self.assertIn('Duplicate mod jar filename', output)


if __name__ == '__main__':
    unittest.main()
