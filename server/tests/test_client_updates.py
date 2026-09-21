import unittest

from app.client_updates import update_required, validate_policy, version_key


class ClientUpdatePolicyTests(unittest.TestCase):
    def test_default_update_is_optional(self):
        self.assertFalse(update_required({"version": "1.2.0"}, "1.1.4"))

    def test_support_floor_only_retires_older_versions(self):
        release = {"version": "1.2.0", "minSupportedVersion": "1.1.5"}
        self.assertTrue(update_required(release, "1.1.4"))
        self.assertFalse(update_required(release, "1.1.5"))
        self.assertFalse(update_required(release, "1.1.9"))
        self.assertFalse(update_required(release, "1.2.0"))
        self.assertFalse(update_required(release, "1.3.0"))

    def test_one_bad_version_can_be_retired_without_all_older_versions(self):
        release = {"version": "1.2.0", "blockedVersions": ["1.1.8"]}
        self.assertFalse(update_required(release, "1.1.7"))
        self.assertTrue(update_required(release, "1.1.8+build.1"))
        self.assertFalse(update_required(release, "1.1.9"))

    def test_legacy_mandatory_and_no_downgrade(self):
        release = {"version": "1.2.0", "mandatory": True}
        self.assertTrue(update_required(release, "1.1.4"))
        self.assertFalse(update_required(release, "1.2.0"))
        self.assertFalse(update_required(release, "1.3.0"))

    def test_semver(self):
        values = ["1.1.5-alpha", "1.1.5-alpha.1", "1.1.5-beta.2", "1.1.5-beta.10", "1.1.5-rc.1", "1.1.5", "1.10.0"]
        self.assertEqual(values, sorted(reversed(values), key=version_key))
        self.assertEqual(version_key("1.1.5+foo"), version_key("v1.1.5+bar"))
        self.assertEqual(version_key("1.0"), version_key("1.0.0"))
        with self.assertRaises(ValueError):
            version_key("1.1.5-01")

    def test_invalid_policy_cannot_publish_or_promote(self):
        for policy in [
            {"minSupportedVersion": "1.2.0"}, {"blockedVersions": ["1.1.5"]},
            {"blockedVersions": ["1.3.0"]}, {"blockedVersions": "1.1.4"},
            {"minSupportedVersion": "not-a-version"},
        ]:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate_policy(policy, "1.1.5")

    def test_policy_is_normalized(self):
        p = validate_policy({"minSupportedVersion": "1.1.2", "blockedVersions": ["1.1.3", "1.1.3+hotfix"], "reason": "需要更新"}, "1.1.5")
        self.assertEqual(["1.1.3"], p["blockedVersions"])
        self.assertEqual("需要更新", p["updateReason"])
