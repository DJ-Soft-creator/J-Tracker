"""HTTP-Hülle der Journal-Analyse-Verwaltung: Registrierung und Guard.

Die Fachlogik wird in ``tests/db_tests/test_journal_analysis.py`` geprüft. Hier
geht es um die zwei Eigenschaften der Hülle, die ohne Datenbank sichtbar sind:
kein Admin-Gate (jeder angemeldete Nutzer kommt durch) und ein klarer 503,
solange die Datenbank nicht aktiviert ist.
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "journal-analysis-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-journal-analysis-import")
    os.environ["JOURNL_DB_ENABLED"] = "false"
    sys.path.insert(0, str(ROOT / "app"))
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class JournalAnalysisApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_data = main.DATA_DIR
        main.DATA_DIR = Path(self.temp.name)
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        self.previous_user = main._current_user
        self.user = {"id": "user-1", "username": "david", "admin": False}
        main._current_user = lambda: self.user

    def tearDown(self):
        main.DATA_DIR = self.previous_data
        main._current_user = self.previous_user
        self.temp.cleanup()

    def test_unauthenticated_request_is_rejected(self):
        main._current_user = lambda: None
        response = self.client.get("/api/journal-analysis/state")
        self.assertEqual(response.status_code, 401)

    def test_non_admin_user_reaches_the_endpoint(self):
        """Bewusst kein Admin-Gate — die Antwort hängt nur an der Datenbank."""
        response = self.client.get("/api/journal-analysis/state")
        self.assertEqual(response.status_code, 503)

    def test_disabled_database_explains_itself(self):
        response = self.client.get("/api/journal-analysis/state")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["db_enabled"], False)

    def test_provider_models_uses_live_discovery(self):
        import db
        import journal_analysis_providers as providers

        original_enabled, original_load = db.is_enabled, main.load_config
        original_discover = providers.discover_models
        db.is_enabled = lambda: True
        main.load_config = lambda: {"ai_providers": [{
            "id": "lm_x", "api_url": "http://127.0.0.1:1234/v1/chat/completions", "model": ""}]}
        providers.discover_models = lambda provider: ["m1", "m2"]
        try:
            response = self.client.get("/api/journal-analysis/providers/lm_x/models")
        finally:
            db.is_enabled, main.load_config = original_enabled, original_load
            providers.discover_models = original_discover
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["models"], ["m1", "m2"])

    def test_provider_models_rejects_unknown_or_public_provider(self):
        import db

        original_enabled, original_load = db.is_enabled, main.load_config
        db.is_enabled = lambda: True
        main.load_config = lambda: {"ai_providers": [{
            "id": "lm_public", "api_url": "http://8.8.8.8/v1/chat/completions", "model": ""}]}
        try:
            public = self.client.get("/api/journal-analysis/providers/lm_public/models")
            unknown = self.client.get("/api/journal-analysis/providers/gibt-es-nicht/models")
        finally:
            db.is_enabled, main.load_config = original_enabled, original_load
        self.assertEqual(public.status_code, 404)
        self.assertEqual(unknown.status_code, 404)

    def test_manual_run_handler_is_registered(self):
        import background_runs
        import journal_analysis_manual

        self.assertIn(journal_analysis_manual.RUN_KIND, background_runs._handlers)

    def test_manual_run_requires_auth_and_csrf(self):
        main._current_user = lambda: None
        self.assertEqual(self.client.post("/api/journal-analysis/jobs/x/run").status_code, 401)
        main._current_user = lambda: self.user
        self.assertIn(self.client.post("/api/journal-analysis/jobs/x/run").status_code, (400, 403))

    def test_write_without_csrf_token_is_rejected(self):
        response = self.client.post("/api/journal-analysis/jobs", json={"name": "X"})
        self.assertIn(response.status_code, (400, 403))


if __name__ == "__main__":
    unittest.main()