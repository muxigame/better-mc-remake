import tempfile
import unittest
from pathlib import Path

from app.auth import AuthStore


class AuthStoreTests(unittest.TestCase):
    def test_register_verify_login_and_admin(self):
        with tempfile.TemporaryDirectory(prefix="battermc-auth-") as temporary:
            store = AuthStore(Path(temporary) / "auth.db")
            account, verify_token = store.register("player@example.com", "Player_1", "correct-horse-battery")
            self.assertFalse(account.verified)

            with self.assertRaises(PermissionError):
                store.login("player@example.com", "correct-horse-battery", "web")

            verified = store.verify_email(verify_token)
            self.assertIsNotNone(verified)
            self.assertTrue(verified.verified)

            logged_in = store.login("Player_1", "correct-horse-battery", "launcher")
            self.assertIsNotNone(logged_in)
            user, session_token = logged_in
            self.assertEqual("Player_1", user.username)
            self.assertEqual("Player_1", store.session(session_token, "launcher").username)

            promoted = store.promote_admin("player@example.com")
            self.assertEqual("admin", promoted.role)
            self.assertEqual(1, len(store.list_accounts()))

            store.logout(session_token)
            self.assertIsNone(store.session(session_token))


if __name__ == "__main__":
    unittest.main()
