import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "local-time-note-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-local-time-note-import")
    sys.path.insert(0, str(ROOT / "app"))
    import local_time_note
    import main
    from zoneinfo import ZoneInfo


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class LocalTimeNoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "u1", "username": "Alex", "password": "x"},
        ]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": [
            {"id": "schnell", "label": "Schnell", "type": "simple"},
            {"id": "idee", "label": "Idee", "type": "simple"},
        ]}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "u1"
            session["csrf_token"] = "csrf"
        # Dienstag 03:12 in Berlin = Montag 21:12 in New York.
        self.now = datetime(2026, 10, 13, 3, 12, 40, tzinfo=ZoneInfo("Europe/Berlin"))

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    def submit(self, **payload):
        with mock.patch.object(main, "get_tz_aware_now", return_value=(self.now, self.now.tzinfo)):
            response = self.client.post("/api/submit", headers={"X-CSRF-Token": "csrf"}, json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        return (self.data / "u1/2026/10/13/Journal_2026-10-13.md").read_text(encoding="utf-8")

    def test_note_formats_offset_and_place(self):
        self.assertEqual(
            local_time_note.note(self.now, "America/New_York"),
            "*Ortszeit: Mo 12.10. 21:12 · New York (−6 h)*",
        )
        self.assertEqual(
            local_time_note.note(self.now, "Asia/Kolkata"),
            "*Ortszeit: Di 13.10. 06:42 · Kolkata (+3:30 h)*",
        )

    def test_same_offset_unknown_or_missing_zone_adds_nothing(self):
        for zone in ("Europe/Berlin", "Europe/Paris", "Mars/Olympus", "", None, 5, "x" * 65):
            self.assertEqual(local_time_note.note(self.now, zone), "", zone)

    def test_quick_note_keeps_header_and_gets_trailing_line(self):
        journal = self.submit(template_id="schnell", content="Abendessen", client_timezone="America/New_York")
        self.assertIn(
            "- 13.10. 03:12:40\nAbendessen\n~~13.10. 03:12:40~~\n"
            "*Ortszeit: Mo 12.10. 21:12 · New York (−6 h)*\n\n___",
            journal,
        )
        blocks = main.tagging_module.journal_blocks(journal, "2026-10-13")
        self.assertEqual(len(blocks), 1)

    def test_template_entry_gets_line_inside_its_separator(self):
        journal = self.submit(template_id="idee", content="Gedanke", client_timezone="America/New_York")
        self.assertIn(
            "## Idee | Datum & Uhrzeit: 2026-10-13 03:12:40\nGedanke\n"
            "*Ortszeit: Mo 12.10. 21:12 · New York (−6 h)*\n\n___",
            journal,
        )

    def test_home_zone_leaves_entry_unchanged(self):
        journal = self.submit(template_id="schnell", content="Zuhause", client_timezone="Europe/Berlin")
        self.assertNotIn("Ortszeit", journal)
        self.assertIn("~~13.10. 03:12:40~~\n\n___", journal)


if __name__ == "__main__":
    unittest.main()
