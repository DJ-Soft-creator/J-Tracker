"""Startzeit-Messung: Anzeige je Geraet und eine reine Zahlen-Logzeile je Start."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "startup-timing-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-startup-timing-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class StartupTimingEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        data = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH)
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = data, data / "users.json", data / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": "u1", "username": "u", "password": "x"}]}),
                                   encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": []}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "u1"
            session["csrf_token"] = "csrf-test"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    def test_one_log_line_with_numbers_and_paths_only(self):
        payload = {
            "ready_ms": 4321, "hidden_ms": 680000, "response_ms": 120.7, "scripts": 52, "scripts_cached": 50, "network_kb": 31,
            "device": "iPhone (PWA)<script>", "service_worker": True,
            "api": [{"path": "/api/dashboard", "ms": 950}, {"path": "/evil?q=geheim", "ms": 10}],
            "journal_text": "darf nicht ins Log",
        }
        with self.assertLogs(level="INFO") as logs:
            response = self.client.post("/api/diagnostics/startup", json=payload,
                                        headers={"X-CSRF-Token": "csrf-test"})
        self.assertEqual(response.status_code, 200)
        line = next(entry for entry in logs.output if "startup-timing" in entry)
        self.assertIn("ready_ms=4321", line)
        self.assertIn("hidden_ms=680000", line)
        self.assertIn("/api/dashboard=950ms", line)
        self.assertIn("device=iPhone (PWA)", line)
        for forbidden in ("evil", "geheim", "darf nicht", "<script>"):
            self.assertNotIn(forbidden, line)

    def test_needs_login_and_csrf(self):
        self.assertEqual(self.client.post("/api/diagnostics/startup", json={}).status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.post("/api/diagnostics/startup", json={},
                                          headers={"X-CSRF-Token": "csrf-test"}).status_code, 401)


class StartupTimingWiringTests(unittest.TestCase):
    def test_both_shells_show_and_load_the_measurement(self):
        for name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertIn('id="settings-startup-timing"', template, name)
            self.assertIn("filename='startup-timing.js'", template, name)
        source = (ROOT / "app" / "static" / "startup-timing.js").read_text(encoding="utf-8")
        self.assertIn("window.config", source)
        self.assertIn("'/api/diagnostics/startup'", source)

    def test_background_time_is_not_counted_as_waiting(self):
        # iOS friert eine PWA im Hintergrund ein; diese Zeit darf nicht als
        # „bedienbar nach 680 s“ erscheinen.
        source = (ROOT / "app" / "static" / "startup-timing.js").read_text(encoding="utf-8")
        self.assertIn("'visibilitychange'", source)
        self.assertIn("ready_ms: round(readyAt - hidden)", source)


if __name__ == "__main__":
    unittest.main()
