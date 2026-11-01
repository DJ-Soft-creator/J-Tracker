"""Pushover: Einstellungen, neutrale Nachrichten, Ruhezeit, Buendelung und Routen."""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))
import pushover  # noqa: E402

FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
TZ = timezone(timedelta(hours=2))
USER = "user-a"
KEY = "u" * 30
TOKEN = "a" * 30


class FakePushover:
    """Ersetzt ``urlopen``: merkt sich die Felder und antwortet wie Pushover."""

    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body or {"status": 1}

    def __call__(self, request, timeout=None):
        self.calls.append(dict(urllib.parse.parse_qsl(request.data.decode("utf-8"))))
        if self.status != 200:
            raise urllib.error.HTTPError(request.full_url, self.status, "x", {},
                                         io.BytesIO(json.dumps(self.body).encode()))
        return _Ctx(io.BytesIO(json.dumps(self.body).encode()))


class _Ctx:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, *args):
        return False


class PushoverDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        (self.data / USER).mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _configure(self, **patch):
        now = patch.pop("now", datetime(2026, 10, 4, 8, 0, tzinfo=TZ))
        return pushover.save_settings(self.data, USER, {"user_key": KEY, "app_token": TOKEN, **patch},
                                      base_url="https://journl.example", now=now)

    def test_credentials_are_validated_and_only_shown_masked(self):
        with self.assertRaises(pushover.PushoverError):
            pushover.save_settings(self.data, USER, {"user_key": "kurz"})
        self._configure()
        view = pushover.public_view(pushover.load_settings(self.data, USER),
                                    pushover.load_state(self.data, USER))
        self.assertTrue(view["configured"])
        self.assertEqual(view["user_key_masked"], "••••uuu")
        self.assertNotIn(KEY, json.dumps(view))
        # Leere Felder behalten die gespeicherten Werte.
        pushover.save_settings(self.data, USER, {"user_key": "", "quiet_start": "23:00"})
        settings = pushover.load_settings(self.data, USER)
        self.assertEqual((settings["user_key"], settings["quiet_start"]), (KEY, "23:00"))
        pushover.save_settings(self.data, USER, {"clear_credentials": True})
        self.assertFalse(pushover.configured(pushover.load_settings(self.data, USER)))

    def test_quiet_hours_span_midnight(self):
        settings = {"quiet_start": "22:00", "quiet_end": "07:00"}
        at = lambda hour: datetime(2026, 10, 4, hour, 30, tzinfo=TZ)  # noqa: E731
        self.assertTrue(pushover.in_quiet_hours(settings, at(23)))
        self.assertTrue(pushover.in_quiet_hours(settings, at(6)))
        self.assertFalse(pushover.in_quiet_hours(settings, at(7)))
        self.assertFalse(pushover.in_quiet_hours({"quiet_start": "", "quiet_end": ""}, at(23)))

    def test_messages_are_neutral(self):
        title, message, priority = pushover.message_for("reminders", 3)
        self.assertEqual((title, message, priority),
                         ("Journl · Erinnerung", "3 neue Erinnerungen aus der Journal-Analyse.", 0))
        self.assertEqual(pushover.message_for("errors")[2], 1)
        self.assertEqual(pushover.message_for("family", 2, 1)[1], "Heute 2 Aufgaben fällig, 1 überfällig.")

    def test_reminder_messages_name_the_time_but_never_the_text(self):
        now = datetime(2026, 10, 4, 10, 1, tzinfo=TZ)
        one = pushover.message_for("reminders", 1, now=now, moments=[
            (datetime(2026, 10, 4, 10, 0, tzinfo=TZ), date(2026, 10, 3))])
        self.assertEqual(one, ("Journl · Erinnerung", "Erinnerung für 10:00 (Journal vom 03.10.).", 0))
        two = pushover.message_for("reminders", 2, now=now, moments=[
            (datetime(2026, 10, 4, 13, 0, tzinfo=TZ), None),
            (datetime(2026, 10, 3, 9, 0, tzinfo=TZ), None)])
        self.assertEqual(two[1], "2 Erinnerungen für 03.10. 09:00 und 13:00.")

    def test_dispatched_reminders_carry_their_due_time(self):
        self._configure()
        fake = FakePushover()
        now = datetime(2026, 10, 4, 10, 2, tzinfo=TZ)
        notes = [{"id": "n1", "kind": "reminder", "run_date": "2026-10-03",
                  "notify_at": "2026-10-04T08:00:00+00:00", "created_at": "2026-10-03T20:00:00+00:00",
                  "body": "Thomas anrufen"}]
        self.assertEqual(pushover.dispatch_user(self.data, USER, now, notifications=notes, opener=fake), 1)
        self.assertEqual(fake.calls[0]["message"], "Erinnerung für 10:00 (Journal vom 03.10.).")
        self.assertNotIn("Thomas", fake.calls[0]["message"])

    def test_analysis_hints_are_bundled_sent_once_and_link_back(self):
        self._configure()
        fake = FakePushover()
        now = datetime(2026, 10, 4, 9, 0, tzinfo=TZ)
        notes = [
            {"id": "n1", "kind": "reminder", "created_at": "2026-10-04T06:30:00+00:00"},
            {"id": "n2", "kind": "reminder", "created_at": "2026-10-04T06:40:00+00:00"},
            {"id": "n3", "kind": "error", "created_at": "2026-10-04T06:45:00+00:00"},
            # Vor dem Einrichten entstanden: keine nachtraegliche Flut.
            {"id": "old", "kind": "reminder", "created_at": "2026-10-01T06:00:00+00:00"},
        ]
        sent = pushover.dispatch_user(self.data, USER, now, notifications=notes, opener=fake)
        self.assertEqual(sent, 2)
        self.assertEqual([call["title"] for call in fake.calls], ["Journl · Fehler", "Journl · Erinnerung"])
        self.assertEqual(fake.calls[0]["priority"], "1")
        self.assertIn("2 neue Erinnerungen", fake.calls[1]["message"])
        self.assertEqual(fake.calls[1]["url"], "https://journl.example/?tab=write&open=notifications")
        # Zweiter Durchlauf: nichts doppelt.
        self.assertEqual(pushover.dispatch_user(self.data, USER, now, notifications=notes, opener=fake), 0)

    def test_quiet_hours_hold_back_and_disabled_triggers_stay_silent(self):
        self._configure(triggers={"results": False})
        fake = FakePushover()
        notes = [{"id": "n1", "kind": "analysis", "created_at": "2026-10-04T06:30:00+00:00"},
                 {"id": "n2", "kind": "reminder", "created_at": "2026-10-04T20:30:00+00:00"}]
        night = datetime(2026, 10, 4, 23, 0, tzinfo=TZ)
        self.assertEqual(pushover.dispatch_user(self.data, USER, night, notifications=notes, opener=fake), 0)
        morning = datetime(2026, 10, 5, 7, 5, tzinfo=TZ)
        self.assertEqual(pushover.dispatch_user(self.data, USER, morning, notifications=notes, opener=fake), 1)
        self.assertEqual([call["title"] for call in fake.calls], ["Journl · Erinnerung"])

    def test_tagging_notices_are_bundled_per_kind_and_sent_once(self):
        self._configure()
        fake = FakePushover()
        events = [
            {"id": "e1", "kind": "tagging_error", "count": 1, "created_at": "2026-10-04T09:00:00+02:00"},
            {"id": "e2", "kind": "tagging_proposals", "count": 2, "created_at": "2026-10-04T09:00:00+02:00"},
            {"id": "e3", "kind": "tagging_proposals", "count": 1, "created_at": "2026-10-04T09:05:00+02:00"},
        ]
        now = datetime(2026, 10, 4, 9, 10, tzinfo=TZ)

        self.assertEqual(pushover.dispatch_user(self.data, USER, now, tagging_events=events, opener=fake), 2)
        self.assertEqual(pushover.dispatch_user(self.data, USER, now, tagging_events=events, opener=fake), 0)
        self.assertEqual([call["message"] for call in fake.calls], [
            "Hashtags nachtragen meldet einen Fehler.",
            "3 neue Hashtag-Vorschläge warten auf Freigabe.",
        ])
        self.assertEqual(fake.calls[0]["priority"], "1")
        self.assertEqual(fake.calls[0]["url"], "https://journl.example/?open=tagging")

    def test_the_tagging_switch_silences_the_job(self):
        self._configure(triggers={"tagging": False})
        fake = FakePushover()
        events = [{"id": "e1", "kind": "tagging_error", "count": 1, "created_at": "2026-10-04T09:00:00+02:00"}]
        now = datetime(2026, 10, 4, 9, 10, tzinfo=TZ)
        self.assertEqual(pushover.dispatch_user(self.data, USER, now, tagging_events=events, opener=fake), 0)

    def test_rejected_credentials_halt_until_they_change(self):
        self._configure()
        fake = FakePushover(status=400, body={"status": 0, "errors": ["user key is invalid"]})
        now = datetime(2026, 10, 4, 9, 0, tzinfo=TZ)
        notes = [{"id": "n1", "kind": "reminder", "created_at": "2026-10-04T06:30:00+00:00"}]
        pushover.dispatch_user(self.data, USER, now, notifications=notes, opener=fake)
        state = pushover.load_state(self.data, USER)
        self.assertEqual(state["halted"], "user key is invalid")
        pushover.dispatch_user(self.data, USER, now, notifications=notes, opener=fake)
        self.assertEqual(len(fake.calls), 1)
        self._configure()
        self.assertEqual(pushover.load_state(self.data, USER)["halted"], "")

    def test_family_digest_once_per_day_after_its_time(self):
        self._configure(family_time="07:30")
        fake = FakePushover()
        early = datetime(2026, 10, 4, 7, 0, tzinfo=TZ)
        self.assertEqual(pushover.dispatch_user(self.data, USER, early, family_counts=(2, 1), opener=fake), 0)
        later = datetime(2026, 10, 4, 7, 35, tzinfo=TZ)
        self.assertEqual(pushover.dispatch_user(self.data, USER, later, family_counts=(2, 1), opener=fake), 1)
        self.assertEqual(fake.calls[0]["url"], "https://journl.example/?tab=family")
        self.assertEqual(pushover.dispatch_user(self.data, USER, later, family_counts=(2, 1), opener=fake), 0)

    def test_family_counts_follow_board_visibility(self):
        today = date(2026, 10, 4)
        task = lambda due, user="", done=False: {"target_date": due, "user": user, "completed": done}  # noqa: E731
        documents = [
            ({"tasks": [task("2026-10-04"), task("2026-10-01"), task("2026-10-04", done=True)]}, True),
            ({"tasks": [task("2026-10-04", user=USER), task("2026-10-04", user="other")]}, False),
            ({"exclude_from_personal": True, "tasks": [task("2026-10-04")]}, True),
        ]
        self.assertEqual(pushover.family_counts(documents, USER, today), (2, 1))


if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "pushover-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/opencode/journl-pushover-import")
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class PushoverRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.original = main.DATA_DIR
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": USER, "username": "Alex", "password": "x"}]}),
                                   encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = USER
            session["csrf_token"] = "csrf-test"
        self.headers = {"X-CSRF-Token": "csrf-test"}

    def tearDown(self):
        main.DATA_DIR = self.original
        self.temp.cleanup()

    def test_requires_login(self):
        anonymous = main.app.test_client()
        self.assertEqual(anonymous.get("/api/pushover/settings").status_code, 401)

    def test_settings_round_trip_never_returns_the_secrets(self):
        response = self.client.put("/api/pushover/settings", headers=self.headers, json={
            "user_key": KEY, "app_token": TOKEN, "triggers": {"family": False},
            "base_url": "https://journl.example/irgendwas?x=1",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertTrue(body["configured"])
        self.assertFalse(body["triggers"]["family"])
        self.assertNotIn(KEY, json.dumps(body))
        self.assertEqual(pushover.load_settings(self.data, USER)["base_url"], "https://journl.example")
        bad = self.client.put("/api/pushover/settings", headers=self.headers, json={"app_token": "zu-kurz"})
        self.assertEqual(bad.status_code, 400)

    def test_test_message_reports_pushover_errors(self):
        self.client.put("/api/pushover/settings", headers=self.headers, json={"user_key": KEY, "app_token": TOKEN})
        with mock.patch.object(pushover, "send", return_value=(False, "application token is invalid", True)):
            response = self.client.post("/api/pushover/test", headers=self.headers, json={})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "application token is invalid")
        self.assertTrue(response.get_json()["halted"])


class PushoverFrontendTests(unittest.TestCase):
    def test_menu_entry_section_and_scripts_in_both_shells(self):
        for name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertIn('data-settings-open="notifications"', template, name)
            self.assertIn('<div id="settings-pushover"></div>', template, name)
            self.assertIn("filename='pushover-settings.js'", template, name)
            self.assertIn("filename='deep-link.js'", template, name)
        source = (ROOT_DIR / "app" / "static" / "pushover-settings.js").read_text(encoding="utf-8")
        self.assertIn("/api/pushover/settings", source)
        self.assertIn("window.location.origin", source)

    def test_scheduler_dispatches_after_the_analysis(self):
        source = (ROOT_DIR / "scripts" / "journal-analysis-scheduler.py").read_text(encoding="utf-8")
        self.assertIn("dispatch_pushover()", source)
        self.assertIn("pushover.dispatch_all(", source)


if __name__ == "__main__":
    unittest.main()
