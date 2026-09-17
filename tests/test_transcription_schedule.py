"""Der Zeitplan des Whisper-Sidecars muss ohne Neustart aenderbar sein.

Bis 4.7.2 lag die Stunde als Umgebungsvariable im Deployment: wer mehrmals am
Tag diktiert, musste bis zum naechsten Morgen warten. Der Zeitplan liegt jetzt
als Datei unter ``DATA_DIR``; die App schreibt sie, der Sidecar liest sie bei
jedem Durchlauf.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("PIL") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "transcription-schedule-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-transcription-schedule-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import transcription_schedule  # noqa: F401  (registers the route under test)

_spec = importlib.util.spec_from_file_location(
    "whisper_scheduler_schedule", ROOT / "scripts" / "whisper-scheduler.py")
whisper_scheduler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(whisper_scheduler)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class ScheduleApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "u1", "username": "Alex", "password": "x", "admin": True},
            {"id": "u2", "username": "Kim", "password": "x"},
        ]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": []}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        self.login("u1")

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf"

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf"}

    def test_default_is_the_deployed_daily_run(self):
        response = self.client.get("/api/write-sessions/transcriptions/schedule", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["mode"], "daily")
        self.assertEqual(response.get_json()["hour"], 11)
        self.assertTrue(response.get_json()["can_edit"])

    def test_an_admin_can_switch_to_hourly_and_the_sidecar_reads_it(self):
        saved = self.client.post(
            "/api/write-sessions/transcriptions/schedule", headers=self.headers, json={"mode": "hourly"},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        stored = json.loads((self.data / "whisper_jobs" / "schedule.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["mode"], "hourly")
        self.assertEqual(stored["updated_by"], "u1")
        whisper_scheduler.DATA_DIR = self.data
        self.assertEqual(whisper_scheduler._schedule(), ("hourly", 11))

    def test_a_daily_run_keeps_its_hour(self):
        self.client.post(
            "/api/write-sessions/transcriptions/schedule", headers=self.headers, json={"mode": "daily", "hour": 6},
        )
        whisper_scheduler.DATA_DIR = self.data
        self.assertEqual(whisper_scheduler._schedule(), ("daily", 6))

    def test_a_junk_hour_is_rejected_instead_of_silently_clamped(self):
        for hour in (24, -1, "elf", True):
            response = self.client.post(
                "/api/write-sessions/transcriptions/schedule", headers=self.headers,
                json={"mode": "daily", "hour": hour},
            )
            self.assertEqual(response.status_code, 400, hour)
        self.assertFalse((self.data / "whisper_jobs" / "schedule.json").exists())

    def test_a_non_admin_sees_the_schedule_but_cannot_change_it(self):
        self.login("u2")
        self.assertFalse(self.client.get(
            "/api/write-sessions/transcriptions/schedule", headers=self.headers).get_json()["can_edit"])
        denied = self.client.post(
            "/api/write-sessions/transcriptions/schedule", headers=self.headers, json={"mode": "off"})
        self.assertEqual(denied.status_code, 403)


class DueSlotTests(unittest.TestCase):
    """Jeder Zeitplan darf pro Fenster genau einen Lauf ausloesen."""

    def slot(self, mode, hour, moment):
        return whisper_scheduler._due_slot(mode, hour, datetime.fromisoformat(moment))

    def test_hourly_starts_a_new_run_every_hour(self):
        self.assertEqual(self.slot("hourly", 11, "2026-09-14T08:05:00"), "2026-09-14T08")
        self.assertNotEqual(
            self.slot("hourly", 11, "2026-09-14T09:05:00"),
            self.slot("hourly", 11, "2026-09-14T08:59:00"),
        )

    def test_daily_waits_for_its_hour_and_then_runs_once(self):
        self.assertIsNone(self.slot("daily", 11, "2026-09-14T10:59:00"))
        self.assertEqual(self.slot("daily", 11, "2026-09-14T11:00:00"), "2026-09-14")
        self.assertEqual(self.slot("daily", 11, "2026-09-14T22:00:00"), "2026-09-14")

    def test_manual_only_never_schedules_a_run(self):
        self.assertIsNone(self.slot("off", 11, "2026-09-14T11:00:00"))
