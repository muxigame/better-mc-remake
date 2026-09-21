import unittest
from unittest.mock import patch

from fastapi import Response

from app.main import health, launcher_release, manifest, site, site_config, tauri_updater


class SiteApiTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
