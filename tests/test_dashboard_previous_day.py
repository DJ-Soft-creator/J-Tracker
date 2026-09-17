"""Continuous scrolling in the Heute tab: /api/dashboard/previous."""

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
    os.environ.setdefault("SECRET_KEY", "dashboard-previous-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-dashboard-previous")
    sys.path.insert(0, str(ROOT / "app"))
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class DashboardPreviousDayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        main.USERS_PATH.write_text(
            json.dumps({"users": [{"id": "u1", "username": "Alex", "password": "x"}]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": []}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "u1"
            session["csrf_token"] = "csrf"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    def _journal(self, date, body):
        year, month, day = date.split("-")
        directory = self.data / "u1" / year / month / day
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"Journal_{date}.md").write_text(
            f"# Journal {date}\n\n___\n\n{body}\n\n___\n", encoding="utf-8")

    def test_previous_day_skips_gaps_and_crosses_month_and_year(self):
        self._journal("2025-12-30", "Silvesterwoche")
        self._journal("2026-07-04", "Sommertag")
        self._journal("2026-08-20", "Vorletzter")

        first = self.client.get("/api/dashboard/previous?before=2026-08-23").get_json()
        self.assertEqual(first["date"], "2026-08-20")
        self.assertEqual([entry["content"] for entry in first["entries"]], ["Vorletzter"])

        second = self.client.get("/api/dashboard/previous?before=" + first["date"]).get_json()
        self.assertEqual(second["date"], "2026-07-04")

        third = self.client.get("/api/dashboard/previous?before=" + second["date"]).get_json()
        self.assertEqual(third["date"], "2025-12-30")

    def test_previous_day_reports_nothing_older_instead_of_an_empty_day(self):
        self._journal("2026-08-20", "Einziger Tag")

        exhausted = self.client.get("/api/dashboard/previous?before=2026-08-20").get_json()
        self.assertIsNone(exhausted["date"])
        self.assertEqual(exhausted["entries"], [])

    def test_previous_day_ignores_days_without_a_journal_file(self):
        (self.data / "u1" / "2026" / "08" / "21").mkdir(parents=True)
        (self.data / "u1" / "2026" / "08" / "21" / "notiz.md").write_text("kein Journal", encoding="utf-8")
        self._journal("2026-08-19", "Echter Eintrag")

        result = self.client.get("/api/dashboard/previous?before=2026-08-23").get_json()
        self.assertEqual(result["date"], "2026-08-19")

    def test_previous_day_rejects_a_malformed_date(self):
        response = self.client.get("/api/dashboard/previous?before=2026-8-1")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
