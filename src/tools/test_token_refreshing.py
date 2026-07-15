"""
test_token_refreshing.py — Test suite for Refresh_token.py

Tests every function without needing a real browser or Workday connection.
All HTTP calls are mocked. Temp files are used instead of real .env / token files.

Run:
    python src/tools/test_token_refreshing.py
"""

import json
import os
import sys
import time
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# ── Make sure src/tools is importable ────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import src.tools.Refresh_token as rt

def make_token_data(expires_in: int = 3600, include_refresh: bool = True) -> dict:
    """Build a fake token dict as Workday would return."""
    data = {
        "access_token":  "fake_access_token_abc123",
        "expires_in":    expires_in,
        "saved_at":      time.time(),
        "expires_at":    time.time() + expires_in,
    }
    if include_refresh:
        data["refresh_token"] = "fake_refresh_token_xyz789"
    return data

class TestUpdateEnvToken(unittest.TestCase):

    def test_appends_when_key_missing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("SOME_OTHER_KEY=value\n")
            path = f.name
        try:
            rt.update_env_token("NEW_TOKEN_123", env_file=path)
            content = open(path).read()
            self.assertIn('WORKDAY_API_TOKEN="NEW_TOKEN_123"', content)
            self.assertIn("SOME_OTHER_KEY=value", content)
        finally:
            os.unlink(path)

    def test_replaces_existing_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write('WORKDAY_API_TOKEN="OLD_TOKEN"\n')
            f.write("OTHER=stuff\n")
            path = f.name
        try:
            rt.update_env_token("NEW_TOKEN_456", env_file=path)
            lines = open(path).readlines()
            token_lines = [l for l in lines if "WORKDAY_API_TOKEN" in l]
            self.assertEqual(len(token_lines), 1)
            self.assertIn("NEW_TOKEN_456", token_lines[0])
        finally:
            os.unlink(path)

class TestSaveLoadTokens(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self._orig_token_file = rt.TOKEN_FILE
        rt.TOKEN_FILE = self.tmp.name

    def tearDown(self):
        rt.TOKEN_FILE = self._orig_token_file
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_save_and_reload(self):
        data = {"access_token": "tok123", "refresh_token": "ref456", "expires_in": 3600}
        with patch.object(rt, "update_env_token"):
            rt.save_tokens(data)
        loaded = rt.load_tokens()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["access_token"], "tok123")
        self.assertEqual(loaded["refresh_token"], "ref456")

class TestIsTokenExpired(unittest.TestCase):

    def test_valid_token_not_expired(self):
        tokens = {"expires_at": time.time() + 9999}
        self.assertFalse(rt.is_token_expired(tokens))

    def test_expired_token(self):
        tokens = {"expires_at": time.time() - 100}
        self.assertTrue(rt.is_token_expired(tokens))

class TestGetValidToken(unittest.TestCase):

    def test_returns_token_when_valid(self):
        tokens = make_token_data(expires_in=9999)
        with patch.object(rt, "load_tokens", return_value=tokens):
            result = rt.get_valid_token()
        self.assertEqual(result, "fake_access_token_abc123")

    def test_auto_refreshes_when_expired(self):
        expired_tokens = make_token_data(expires_in=-100)
        new_token_data = {"access_token": "refreshed_token", "expires_in": 3600}

        with patch.object(rt, "load_tokens", return_value=expired_tokens), \
             patch.object(rt, "refresh_access_token", return_value=new_token_data), \
             patch.object(rt, "save_tokens"):
            result = rt.get_valid_token()

        self.assertEqual(result, "refreshed_token")

    @patch("src.tools.Refresh_token.login")
    def test_triggers_login_when_no_tokens_on_disk(self, mock_login):
        """get_valid_token triggers login() if no tokens are found on disk."""
        mock_login.return_value = "new_login_token"
        with patch.object(rt, "load_tokens", return_value=None):
            result = rt.get_valid_token()
        self.assertEqual(result, "new_login_token")
        mock_login.assert_called_once()

    @patch("src.tools.Refresh_token.login")
    def test_triggers_login_when_refresh_fails(self, mock_login):
        """get_valid_token triggers login() if the silent refresh fails."""
        expired_tokens = make_token_data(expires_in=-100)
        mock_login.return_value = "fallback_login_token"
        
        with patch.object(rt, "load_tokens", return_value=expired_tokens), \
             patch.object(rt, "refresh_access_token", side_effect=Exception("Refresh failed")):
            result = rt.get_valid_token()
            
        self.assertEqual(result, "fallback_login_token")
        mock_login.assert_called_once()

    @patch("src.tools.Refresh_token.login")
    def test_non_interactive_raises_runtime_error_on_get_valid_token(self, mock_login):
        with patch.dict(os.environ, {"WORKDAY_NON_INTERACTIVE": "true"}), \
             patch.object(rt, "load_tokens", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                rt.get_valid_token()
            self.assertIn("No Workday tokens found on disk", str(ctx.exception))
        mock_login.assert_not_called()

    @patch("src.tools.Refresh_token.login")
    def test_non_interactive_raises_runtime_error_on_get_valid_token_refresh_fail(self, mock_login):
        expired_tokens = make_token_data(expires_in=-100)
        with patch.dict(os.environ, {"WORKDAY_NON_INTERACTIVE": "true"}), \
             patch.object(rt, "load_tokens", return_value=expired_tokens), \
             patch.object(rt, "refresh_access_token", side_effect=Exception("Refresh failed")):
            with self.assertRaises(RuntimeError) as ctx:
                rt.get_valid_token()
            self.assertIn("Workday silent refresh failed", str(ctx.exception))
        mock_login.assert_not_called()


class TestForceRefresh(unittest.TestCase):

    @patch("src.tools.Refresh_token.login")
    def test_force_refresh_success(self, mock_login):
        tokens = make_token_data(expires_in=3600)
        new_token_data = {"access_token": "forced_refreshed_token", "expires_in": 3600}

        with patch.object(rt, "load_tokens", return_value=tokens), \
             patch.object(rt, "refresh_access_token", return_value=new_token_data), \
             patch.object(rt, "save_tokens") as mock_save:
            result = rt.force_refresh()

        self.assertEqual(result, "forced_refreshed_token")
        mock_save.assert_called_once()
        mock_login.assert_not_called()

    @patch("src.tools.Refresh_token.login")
    def test_force_refresh_no_tokens_on_disk(self, mock_login):
        mock_login.return_value = "new_login_token"
        with patch.object(rt, "load_tokens", return_value=None):
            result = rt.force_refresh()
        self.assertEqual(result, "new_login_token")
        mock_login.assert_called_once()

    @patch("src.tools.Refresh_token.login")
    def test_force_refresh_no_refresh_token(self, mock_login):
        tokens = make_token_data(expires_in=3600, include_refresh=False)
        mock_login.return_value = "new_login_token"
        with patch.object(rt, "load_tokens", return_value=tokens):
            result = rt.force_refresh()
        self.assertEqual(result, "new_login_token")
        mock_login.assert_called_once()

    @patch("src.tools.Refresh_token.login")
    def test_force_refresh_refresh_fails(self, mock_login):
        tokens = make_token_data(expires_in=3600)
        mock_login.return_value = "fallback_login_token"

        with patch.object(rt, "load_tokens", return_value=tokens), \
             patch.object(rt, "refresh_access_token", side_effect=Exception("Network error")):
            result = rt.force_refresh()

        self.assertEqual(result, "fallback_login_token")
        mock_login.assert_called_once()

    @patch("src.tools.Refresh_token.login")
    def test_non_interactive_raises_runtime_error_on_force_refresh(self, mock_login):
        with patch.dict(os.environ, {"WORKDAY_NON_INTERACTIVE": "true"}), \
             patch.object(rt, "load_tokens", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                rt.force_refresh()
            self.assertIn("No Workday tokens found on disk", str(ctx.exception))
        mock_login.assert_not_called()

    @patch("src.tools.Refresh_token.login")
    def test_non_interactive_raises_runtime_error_on_force_refresh_fail(self, mock_login):
        tokens = make_token_data(expires_in=3600)
        with patch.dict(os.environ, {"WORKDAY_NON_INTERACTIVE": "true"}), \
             patch.object(rt, "load_tokens", return_value=tokens), \
             patch.object(rt, "refresh_access_token", side_effect=Exception("Network error")):
            with self.assertRaises(RuntimeError) as ctx:
                rt.force_refresh()
            self.assertIn("Forced refresh failed: Network error", str(ctx.exception))
        mock_login.assert_not_called()


if __name__ == "__main__":
    unittest.main()
