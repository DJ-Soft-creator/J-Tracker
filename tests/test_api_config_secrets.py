"""/api/config darf keine API-Schluessel an den Browser geben (D-021)."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "config-secret-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-config-secret-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class ApiConfigSecretTests(unittest.TestCase):
    def setUp(self):
        import tempfile as _tempfile
        self.temp = _tempfile.TemporaryDirectory()
        data = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH)
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = data, data / "users.json", data / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": "u1", "username": "u", "password": "x"}]}),
                                   encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({
            "templates": [],
            "ai_providers": [
                {"id": "openrouter", "api_url": "https://openrouter.ai/api/v1/chat/completions", "model": "m"},
                {"id": "lokal", "api_url": "http://10.0.0.2/v1/chat/completions", "model": "m",
                 "api_key": "sk-GEHEIM", "max_tokens": 4000},
            ],
            "shopping_ai": {"api_key": "sk-AUCH-GEHEIM", "model": "x"},
        }), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "u1"
            session["csrf_token"] = "csrf-test"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    def test_provider_keys_never_reach_the_browser(self):
        with mock.patch.dict(os.environ, {"AI_KEY_OPENROUTER": "sk-AUS-ENV"}):
            body = self.client.get("/api/config").get_data(as_text=True)
        self.assertIn("openrouter.ai", body)  # die Konfiguration kam wirklich an
        self.assertNotIn("sk-GEHEIM", body)
        self.assertNotIn("sk-AUS-ENV", body)
        self.assertNotIn("sk-AUCH-GEHEIM", body)
        self.assertIn('"max_tokens":4000', body)  # kein Geheimnis, bleibt

if __name__ == "__main__":
    unittest.main()
