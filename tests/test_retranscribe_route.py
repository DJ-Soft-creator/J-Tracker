"""Re-transcription of an already submitted voice message is a queued job."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("PIL") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "retranscribe-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-retranscribe-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import transcription_jobs  # noqa: F401  (registers the route under test)


MEDIA_ID = "e4f71c48-e5d3-4797-ab39-6fd64cef113e"


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class RetranscribeRouteTests(unittest.TestCase):
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
        self.sidecar_dir = self.data / "u1" / "2026" / "08" / "24" / "media" / "Sprachi"
        self.sidecar_dir.mkdir(parents=True)
        self.sidecar = self.sidecar_dir / f"2026-08-24_10-34-36_{MEDIA_ID}_metadata.json"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf"}

    def write_sidecar(self, **overrides):
        item = {
            "id": MEDIA_ID, "user_id": "u1", "type": "audio", "status": "ready",
            "media_path": "2026/08/24/media/Sprachi/x_audio.m4a",
            "journal_path": "2026/08/24/Journal_2026-08-24.md",
            "transcription_status": "completed", "transcript_text": "Zu schwach.",
        }
        item.update(overrides)
        self.sidecar.write_text(json.dumps(item), encoding="utf-8")
        return item

    def post(self, media_id=MEDIA_ID, **payload):
        return self.client.post(
            f"/api/write-sessions/media/{media_id}/retranscribe", headers=self.headers, json=payload,
        )

    def test_queues_the_worker_and_reopens_the_recording(self):
        self.write_sidecar()
        response = self.post(model="medium")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["media_id"], MEDIA_ID)
        self.assertEqual(body["model"], "medium")
        self.assertEqual(body["status"], "queued")

        trigger = json.loads((self.data / "whisper_jobs" / "manual" / "u1.json").read_text(encoding="utf-8"))
        self.assertEqual(trigger["media_id"], MEDIA_ID)
        self.assertEqual(trigger["model"], "medium")
        status = json.loads((self.data / "whisper_jobs" / "status" / "u1.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "queued")

        sidecar = json.loads(self.sidecar.read_text(encoding="utf-8"))
        self.assertEqual(sidecar["transcription_status"], "pending")
        # The old transcript survives until a better one actually arrives.
        self.assertEqual(sidecar["transcript_text"], "Zu schwach.")

    def test_unknown_model_falls_back_to_the_configured_default(self):
        self.write_sidecar()
        response = self.post(model="../../etc/passwd")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["model"], os.environ.get("WHISPER_MODEL", "base"))

    def test_a_running_transcription_is_not_queued_twice(self):
        self.write_sidecar(transcription_status="running")
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertFalse((self.data / "whisper_jobs" / "manual" / "u1.json").exists())

    def test_a_recording_still_inside_a_session_is_rejected(self):
        self.write_sidecar(journal_path=None)
        self.assertEqual(self.post().status_code, 409)

    def test_a_photo_cannot_be_transcribed(self):
        self.write_sidecar(type="image")
        self.assertEqual(self.post().status_code, 404)

    def test_another_users_recording_is_invisible(self):
        self.write_sidecar(user_id="u2")
        self.assertEqual(self.post().status_code, 404)

    def test_unknown_media_is_a_not_found(self):
        self.assertEqual(self.post("11111111-2222-3333-4444-555555555555").status_code, 404)

    def test_csrf_token_is_required(self):
        self.write_sidecar()
        response = self.client.post(f"/api/write-sessions/media/{MEDIA_ID}/retranscribe", json={})
        self.assertEqual(response.status_code, 403)

    def test_authentication_is_required(self):
        self.write_sidecar()
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.post().status_code, 401)


if __name__ == "__main__":
    unittest.main()
