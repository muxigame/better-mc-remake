import unittest

from app.main import health, launcher_release, load_manifest, site, site_config


class SiteApiTests(unittest.TestCase):
    def test_release_points_to_oss(self):
        release = launcher_release(site_config())
        self.assertTrue(release["url"].startswith("https://"))
        self.assertTrue(release["url"].endswith("BatterMC5Remake-setup.exe"))

    def test_site_matches_manifest(self):
        manifest = load_manifest()
        payload = site()
        self.assertEqual(manifest["pack"]["version"], payload["pack"]["version"])
        self.assertEqual(len(manifest["files"]), payload["pack"]["fileCount"])
        self.assertTrue(health()["ok"])


if __name__ == "__main__":
    unittest.main()
