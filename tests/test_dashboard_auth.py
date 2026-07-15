import os
import time
import unittest
from unittest.mock import patch

import bcrypt

from backend import dashboard_auth as auth


class DashboardAuthTests(unittest.TestCase):
    def setUp(self):
        with auth._attempt_lock:
            auth._attempts.clear()
            auth._lockouts.clear()

    def _env(self):
        password_hash = bcrypt.hashpw(b"correct horse battery staple", bcrypt.gensalt(rounds=4)).decode("utf-8")
        return {
            "DASHBOARD_AUTH_USERNAME": "admin",
            "DASHBOARD_AUTH_PASSWORD_HASH": password_hash,
            "DASHBOARD_SESSION_SECRET": "test-session-secret-with-sufficient-entropy",
        }

    def test_bcrypt_hash_verifies_without_plaintext_comparison(self):
        env = self._env()
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(bcrypt.checkpw(b"correct horse battery staple", auth._password_hash().encode("utf-8")))
            self.assertFalse(bcrypt.checkpw(b"wrong", auth._password_hash().encode("utf-8")))

    def test_signed_session_success_and_expiry(self):
        with patch.dict(os.environ, self._env(), clear=True):
            now = int(time.time())
            token = auth._sign({"u": "admin", "iat": now, "exp": now + 60, "csrf": "csrf-value"})
            payload = auth._decode(token)
            self.assertEqual(payload["u"], "admin")
            self.assertEqual(payload["csrf"], "csrf-value")
            expired = auth._sign({"u": "admin", "iat": now - 120, "exp": now - 1, "csrf": "old"})
            self.assertIsNone(auth._decode(expired))

    def test_tampered_session_is_rejected(self):
        with patch.dict(os.environ, self._env(), clear=True):
            now = int(time.time())
            token = auth._sign({"u": "admin", "iat": now, "exp": now + 60, "csrf": "csrf"})
            body, signature = token.split(".", 1)
            changed = ("A" if body[0] != "A" else "B") + body[1:] + "." + signature
            self.assertIsNone(auth._decode(changed))

    def test_brute_force_temporary_cooldown(self):
        ip = "192.0.2.10"
        now = int(time.time())
        for _ in range(auth.MAX_ATTEMPTS - 1):
            self.assertEqual(auth._record_failure(ip, now), 0)
        self.assertEqual(auth._record_failure(ip, now), auth.LOCKOUT_SEC)
        self.assertGreater(auth._is_locked(ip, now), 0)
        self.assertEqual(auth._is_locked(ip, now + auth.LOCKOUT_SEC + 1), 0)

    def test_safe_destination_rejects_external_redirects(self):
        self.assertEqual(auth._safe_next("/api/topology"), "/api/topology")
        self.assertEqual(auth._safe_next("https://example.com"), "/")
        self.assertEqual(auth._safe_next("//example.com"), "/")
        self.assertEqual(auth._safe_next("/\\example.com"), "/")

    def test_missing_configuration_never_allows_access(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(auth.configured())


if __name__ == "__main__":
    unittest.main()
