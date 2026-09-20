import json
import tempfile
import unittest
from pathlib import Path

from app.manifest_builder import build_manifest, glob_to_regex


class ManifestBuilderTests(unittest.TestCase):
    def test_minecraft_globs(self):
        self.assertIsNotNone(glob_to_regex("mods/**").match("mods/a/b.jar"))
        self.assertIsNotNone(glob_to_regex("**/*.log").match("latest.log"))
        self.assertIsNotNone(glob_to_regex("**/*.log").match("logs/latest.log"))
        self.assertIsNotNone(glob_to_regex("versions/*/*.jar").match("versions/1.21.1/client.jar"))
        self.assertIsNone(glob_to_regex("mods/**").match("config/a.toml"))

    def test_build_rewrite_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="battermc-server-") as temporary:
            base = Path(temporary)
            root = base / "game"
            publish = base / "publish"
            (root / "mods").mkdir(parents=True)
            (root / "logs").mkdir()
            (root / "mods" / "example.jar").write_text("jar")
            (root / "options.txt").write_text("skipMultiplayerWarning:false\n")
            (root / "version.json").write_text(json.dumps({"id": "old", "mainClass": "example.Main"}))
            (root / "logs" / "latest.log").write_text("ignore")
            publish.mkdir()
            (publish / "obsolete.txt").write_text("remove")
            spec = {
                "root": str(root),
                "pack": {"id": "test", "name": "Test", "version": "1.0"},
                "minecraft": {"versionId": "Remake"}, "java": {}, "servers": [],
                "include": [{"glob": "mods/**", "policy": "Managed"}, {"glob": "options.txt", "policy": "Managed"}],
                "exclude": ["**/*.log"],
                "map": [{"from": "version.json", "to": "versions/Remake/Remake.json", "policy": "Managed", "rewriteVersionId": True}],
                "prune": ["mods"],
                "overlays": [{"path": "options.txt", "format": "Properties", "enforce": {"skipMultiplayerWarning": True}}],
            }
            manifest, _ = build_manifest(spec, base / "packspec.json", publish, log=lambda _: None)
            self.assertEqual(3, len(manifest["files"]))
            self.assertFalse(any(item["path"].endswith(".log") for item in manifest["files"]))
            self.assertEqual("Seed", next(item for item in manifest["files"] if item["path"] == "options.txt")["policy"])
            self.assertEqual("Remake", json.loads((publish / "versions/Remake/Remake.json").read_text())["id"])
            self.assertFalse((publish / "obsolete.txt").exists())


if __name__ == "__main__":
    unittest.main()
