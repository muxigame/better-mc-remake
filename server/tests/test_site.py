import io
import json
import unittest
from unittest.mock import patch

from fastapi import Response

from app.main import health, launcher_release, manifest, site, site_config, tauri_updater


class SiteApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {
            "version": "1.2.0", "installer": "BatterMC5Remake-setup.exe",
            "url": "https://fixture.example/releases/1.2.0/BatterMC5Remake-setup.exe",
            "size": 123, "sha256": "a" * 64, "signature": "fixture-signature",
        }
        network = patch("app.main.urlopen", side_effect=lambda *a, **kw: io.BytesIO(json.dumps(self.fixture).encode()))
        network.start()
        self.addCleanup(network.stop)

    def test_release_points_to_oss(self):
        release = launcher_release(site_config())
        self.assertTrue(release["url"].startswith("https://"))
        self.assertTrue(release["url"].endswith("BatterMC5Remake-setup.exe"))

    def test_site_matches_manifest(self):
        upstream = {
            "pack": {"name": "BatterMC5Remake", "version": "9.9.9"},
            "minecraft": {"version": "1.21.1", "loader": "neoforge", "loaderVersion": "21.1.250"},
            "files": [{"path": "mods/a.jar", "size": 123}],
        }
        with patch("app.main.load_manifest", return_value=upstream):
            payload = site()
        self.assertEqual("9.9.9", payload["pack"]["version"])
        self.assertEqual(1, payload["pack"]["fileCount"])
        self.assertTrue(health()["ok"])

    def test_manifest_endpoint_only_returns_storage_locations_and_launcher(self):
        response = manifest()
        payload = response.body.decode("utf-8")
        self.assertIn('"manifestUrl"', payload)
        self.assertIn('"filesBaseUrl"', payload)
        self.assertIn('"launcher"', payload)
        self.assertNotIn('"files"', payload)

    def test_tauri_updater_returns_signed_release_for_older_windows_client(self):
        release = launcher_release(site_config())
        self.assertTrue(release.get("signature"))
        update = tauri_updater("windows", "x86_64", "0.0.0")
        self.assertIsInstance(update, dict)
        self.assertEqual(release["version"], update["version"])
        self.assertEqual(release["signature"], update["signature"])
        self.assertEqual(release["url"], update["url"])

    def test_tauri_updater_returns_204_when_current(self):
        release = launcher_release(site_config())
        response = tauri_updater("windows", "x86_64", release["version"])
        self.assertIsInstance(response, Response)
        self.assertEqual(204, response.status_code)

    def test_policy_is_evaluated_per_requesting_client(self):
        self.fixture.update(minSupportedVersion="1.1.5", blockedVersions=["1.1.8"])
        self.assertTrue(tauri_updater("windows", "x86_64", "1.1.4")["mandatory"])
        self.assertFalse(tauri_updater("windows", "x86_64", "1.1.5")["mandatory"])
        self.assertTrue(tauri_updater("windows", "x86_64", "1.1.8")["mandatory"])
        self.assertFalse(tauri_updater("windows", "x86_64", "1.1.9")["mandatory"])
        self.assertEqual(204, tauri_updater("windows", "x86_64", "1.2.0").status_code)

    def test_generic_control_preserves_policy_without_forcing_supported_users(self):
        self.fixture.update(minSupportedVersion="1.1.5", notes="更新说明")
        payload = json.loads(manifest().body)
        self.assertEqual("1.1.5", payload["launcher"]["minSupportedVersion"])
        self.assertFalse(payload["launcher"]["mandatory"])
        self.assertEqual("更新说明", payload["launcher"]["notes"])


if __name__ == "__main__":
    unittest.main()
