import tempfile
import unittest
from pathlib import Path

from app.main import GameNameRequest, player_profile, update_player_profile
from app.oidc import WebsiteAccount, WebsiteAuthStore


class PlayerProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bmc-player-")
        self.store = WebsiteAuthStore(Path(self.tmp.name) / "bmc.db")
        self.account = WebsiteAccount(
            subject="sub-10000",
            uid=10000,
            username="MuxiUser",
            nickname="洛可",
            game_name="MuxiUser",
            email="user@example.com",
            email_verified=True,
            role="player",
            created_at="2026-09-21T00:00:00+00:00",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_profile_bootstraps_from_muxi_game_name(self):
        profile = self.store.player_profile(self.account)
        self.assertEqual("MuxiUser", profile.game_name)
        self.assertEqual(10000, profile.uid)
        self.assertEqual(0, profile.points)
        self.assertEqual(0, profile.balance_cents)

    def test_game_name_change_does_not_change_muxi_identity(self):
        updated = self.store.update_game_name(self.account, "BetterRoc")
        self.assertEqual("BetterRoc", updated.game_name)
        self.assertEqual("MuxiUser", self.account.username)
        self.assertEqual("洛可", self.account.nickname)
        self.assertEqual("MuxiUser", self.account.game_name)

    def test_game_name_is_unique_case_insensitive(self):
        self.store.player_profile(self.account)
        other = WebsiteAccount(
            subject="sub-10001",
            uid=10001,
            username="OtherUser",
            nickname="另一个玩家",
            game_name="OtherUser",
            email=None,
            email_verified=False,
            role="player",
        )
        self.store.player_profile(other)
        self.store.update_game_name(self.account, "UniqueName")
        with self.assertRaises(ValueError):
            self.store.update_game_name(other, "uniquename")


if __name__ == "__main__":
    unittest.main()