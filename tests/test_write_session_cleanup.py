"""Abgeschlossene Schreibsessions duerfen nicht ewig liegen bleiben.

In PROD standen nach fuenf Wochen ueber 500 Ordner unter `write_sessions/`,
weil eine gesendete oder verworfene Session ihren Ordner behielt. Aufgeraeumt
wird nur, was nachweislich erledigt ist - an aktiven, archivierten und an
gesendeten Sessions mit uebrig gebliebenen Medien darf nichts verloren gehen.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import write_session_cleanup  # noqa: E402

FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("PIL") is not None
if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "write-session-cleanup-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-write-session-cleanup-import")
    import main


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "u1" / "write_sessions"
        self.root.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def session(self, session_id, status, age_days, media=None):
        directory = self.root / session_id
        directory.mkdir()
        closed = (NOW - timedelta(days=age_days)).isoformat(timespec="seconds")
        (directory / "session.json").write_text(json.dumps({
            "id": session_id, "user_id": "u1", "status": status, "content": "Text",
            "created_at": closed, "updated_at": closed, "submitted_at": closed,
        }), encoding="utf-8")
        if media:
            target = directory / "media" / "m1"
            target.mkdir(parents=True)
            (target / media).write_bytes(b"audio")
        return directory

    def purge(self, **kwargs):
        return write_session_cleanup.purge_finished_sessions(self.root, NOW, **kwargs)

    def test_finished_sessions_beyond_the_retention_are_removed(self):
        old = self.session("11111111-1111-1111-1111-111111111111", "submitted", 40)
        discarded = self.session("22222222-2222-2222-2222-222222222222", "discarded", 31)
        result = self.purge()
        self.assertEqual(sorted(result["removed"]), [old.name, discarded.name])
        self.assertFalse(old.exists())
        self.assertFalse(discarded.exists())

    def test_recent_active_and_archived_sessions_survive(self):
        recent = self.session("33333333-3333-3333-3333-333333333333", "submitted", 3)
        active = self.session("44444444-4444-4444-4444-444444444444", "active", 90)
        archived = self.session("55555555-5555-5555-5555-555555555555", "archived", 90)
        self.assertEqual(self.purge()["removed"], [])
        for directory in (recent, active, archived):
            self.assertTrue(directory.exists())

    def test_a_submitted_session_that_still_holds_media_is_reported_not_deleted(self):
        # Das heisst, der Submit ist nicht sauber durchgelaufen: die Aufnahme
        # existiert vielleicht nur hier und nicht im Journalbaum.
        stuck = self.session("66666666-6666-6666-6666-666666666666", "submitted", 90, media="original.m4a")
        result = self.purge()
        self.assertEqual(result["kept"], [stuck.name])
        self.assertTrue((stuck / "media" / "m1" / "original.m4a").exists())

    def test_a_discarded_session_is_removed_with_its_media(self):
        # Verworfen heisst verworfen; die Inbox-Referenzen hat der Verwerfen-Weg
        # bereits freigegeben.
        discarded = self.session("77777777-7777-7777-7777-777777777777", "discarded", 90, media="original.m4a")
        self.assertEqual(self.purge()["removed"], [discarded.name])
        self.assertFalse(discarded.exists())

    def test_a_dry_run_reports_without_deleting(self):
        old = self.session("88888888-8888-8888-8888-888888888888", "submitted", 90)
        self.assertEqual(self.purge(dry_run=True)["removed"], [old.name])
        self.assertTrue(old.exists())

    def test_a_foreign_folder_name_is_never_touched(self):
        stray = self.root / "nicht-eine-session"
        stray.mkdir()
        (stray / "session.json").write_text(json.dumps({"id": "x", "status": "submitted"}), encoding="utf-8")
        self.purge()
        self.assertTrue(stray.exists())

    def test_the_daily_run_happens_once_per_day(self):
        self.session("99999999-9999-9999-9999-999999999999", "submitted", 90)
        first = write_session_cleanup.purge_daily(self.root, NOW)
        self.assertEqual(len(first["removed"]), 1)
        self.session("aaaaaaaa-9999-9999-9999-999999999999", "submitted", 90)
        self.assertIsNone(write_session_cleanup.purge_daily(self.root, NOW))
        self.assertTrue((self.root / "aaaaaaaa-9999-9999-9999-999999999999").exists())


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class SessionListCleanupTests(unittest.TestCase):
    """Der Abruf der Sessionliste ist der Anlass zum Aufraeumen."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": "u1", "username": "Alex", "password": "x"}]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": []}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "u1"
            session["csrf_token"] = "csrf"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    def test_listing_sessions_clears_the_finished_ones(self):
        root = self.data / "u1" / "write_sessions"
        root.mkdir(parents=True)
        stale = root / "12121212-1212-1212-1212-121212121212"
        stale.mkdir()
        closed = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
        (stale / "session.json").write_text(json.dumps({
            "id": stale.name, "user_id": "u1", "status": "submitted",
            "created_at": closed, "updated_at": closed, "submitted_at": closed,
        }), encoding="utf-8")
        response = self.client.get("/api/write-sessions", headers={"X-CSRF-Token": "csrf"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(stale.exists())
        self.assertTrue((root / ".cleanup.json").is_file())


if __name__ == "__main__":
    unittest.main()
