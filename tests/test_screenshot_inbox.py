import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("PIL") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "screenshot-inbox-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-screenshot-inbox-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import screenshot_inbox


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class ScreenshotInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "u1", "username": "Alex", "password": "x"},
            {"id": "u2", "username": "Sam", "password": "x"},
        ]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": [
            {"id": "schnell", "label": "Schnell", "type": "simple"},
        ]}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "u1"
            session["csrf_token"] = "csrf"
        self.png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    @property
    def csrf(self):
        return {"X-CSRF-Token": "csrf"}

    def create_token(self):
        response = self.client.post("/api/screenshot-inbox/token", headers=self.csrf, json={})
        self.assertEqual(response.status_code, 201)
        return response.get_json()["token"]

    def upload(self, token, now=None, filename="iPhone Screenshot.png"):
        context = mock.patch.object(screenshot_inbox, "_now", return_value=now) if now else mock.patch.object(
            screenshot_inbox, "_now", wraps=screenshot_inbox._now
        )
        with context:
            return main.app.test_client().post(
                "/api/screenshot-inbox/upload",
                headers={"Authorization": f"Bearer {token}"},
                data={"file": (io.BytesIO(self.png), filename, "image/png")},
                content_type="multipart/form-data",
            )

    def create_session(self, content=""):
        response = self.client.post("/api/write-sessions", headers=self.csrf, json={
            "content": content, "template_id": "schnell",
        })
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def test_token_is_stored_only_as_hash_and_can_be_revoked(self):
        self.assertEqual(self.client.post("/api/screenshot-inbox/upload", data=self.png).status_code, 401)
        token = self.create_token()
        config_path = self.data / "u1" / ".config" / "screenshot-upload.json"
        config_text = config_path.read_text(encoding="utf-8")
        self.assertNotIn(token, config_text)
        self.assertIn("token_sha256", config_text)

        uploaded = self.upload(token)
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(self.client.delete("/api/screenshot-inbox/token", headers=self.csrf).status_code, 200)
        self.assertEqual(self.upload(token).status_code, 401)

    def test_upload_is_kept_in_today_media_inbox_and_is_private(self):
        token = self.create_token()
        now = datetime(2026, 8, 15, 9, 30, tzinfo=timezone(timedelta(hours=2)))
        uploaded = self.upload(token, now=now)
        self.assertEqual(uploaded.status_code, 201)
        item = uploaded.get_json()
        inbox = self.data / "u1" / "2026" / "08" / "15" / "media" / "Screenshot-Inbox"
        self.assertEqual(len(list(inbox.glob("*.png"))), 1)
        self.assertEqual(len(list(inbox.glob("*.json"))), 1)
        preview = self.client.get(f"/api/screenshot-inbox/items/{item['id']}/preview")
        self.assertEqual(preview.status_code, 200)
        preview.close()

        other = main.app.test_client()
        self.assertEqual(other.get("/api/screenshot-inbox/items").status_code, 401)
        self.assertEqual(other.get(f"/api/screenshot-inbox/items/{item['id']}/preview").status_code, 401)

    def test_today_is_default_but_an_older_date_and_all_can_be_selected(self):
        token = self.create_token()
        current = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
        older = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(self.upload(token, now=current, filename="heute.png").status_code, 201)
        self.assertEqual(self.upload(token, now=older, filename="alt.png").status_code, 201)
        with mock.patch.object(screenshot_inbox, "_now", return_value=current):
            today = self.client.get("/api/screenshot-inbox/items").get_json()
            old = self.client.get("/api/screenshot-inbox/items?date=2026-08-12").get_json()
            all_items = self.client.get("/api/screenshot-inbox/items?date=all").get_json()
        self.assertEqual([item["original_filename"] for item in today["items"]], ["heute.png"])
        self.assertEqual([item["original_filename"] for item in old["items"]], ["alt.png"])
        self.assertEqual(len(all_items["items"]), 2)
        self.assertEqual(all_items["today_count"], 1)

    def test_pending_upload_can_be_deleted_but_attached_upload_cannot(self):
        token = self.create_token()
        uploaded = self.upload(token).get_json()
        deleted = self.client.delete(
            f"/api/screenshot-inbox/items/{uploaded['id']}", headers=self.csrf,
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/screenshot-inbox/items?date=all").get_json()["total_count"], 0)
        self.assertEqual(self.client.delete(
            f"/api/screenshot-inbox/items/{uploaded['id']}", headers=self.csrf,
        ).status_code, 404)

        uploaded = self.upload(token).get_json()
        session = self.create_session("")
        self.assertEqual(self.client.post(
            f"/api/screenshot-inbox/items/{uploaded['id']}/attach", headers=self.csrf,
            json={"session_id": session["id"]},
        ).status_code, 200)
        self.assertEqual(self.client.delete(
            f"/api/screenshot-inbox/items/{uploaded['id']}", headers=self.csrf,
        ).status_code, 409)

    def test_attach_can_be_released_then_committed_to_the_journal(self):
        token = self.create_token()
        uploaded = self.upload(token).get_json()
        session = self.create_session("")
        attached = self.client.post(
            f"/api/screenshot-inbox/items/{uploaded['id']}/attach", headers=self.csrf,
            json={"session_id": session["id"]},
        )
        self.assertEqual(attached.status_code, 200)
        self.assertEqual(attached.get_json()["media_count"], 1)
        pending = self.client.get("/api/screenshot-inbox/items?date=all").get_json()
        self.assertEqual(pending["total_count"], 0)

        released = self.client.delete(
            f"/api/write-sessions/{session['id']}/media/{uploaded['id']}", headers=self.csrf,
        )
        self.assertEqual(released.status_code, 200)
        self.assertEqual(self.client.get("/api/screenshot-inbox/items?date=all").get_json()["total_count"], 1)

        attached_again = self.client.post(
            f"/api/screenshot-inbox/items/{uploaded['id']}/attach", headers=self.csrf,
            json={"session_id": session["id"]},
        )
        self.assertEqual(attached_again.status_code, 200)
        submitted_at = datetime(2026, 8, 15, 11, 0, tzinfo=timezone(timedelta(hours=2)))
        with mock.patch.object(main, "get_tz_aware_now", return_value=(submitted_at, submitted_at.tzinfo)):
            submitted = self.client.post("/api/submit", headers=self.csrf, json={
                "template_id": "schnell", "content": "", "write_session_id": session["id"],
            })
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(self.client.get("/api/screenshot-inbox/items?date=all").get_json()["total_count"], 0)
        journal = next((self.data / "u1").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertIn(f'"id":"{uploaded["id"]}"', journal)
        self.assertIn('"media_path":"2026/08/15/media/', journal)

    def test_raw_body_is_supported_but_non_images_are_rejected(self):
        token = self.create_token()
        valid = self.client.post("/api/screenshot-inbox/upload", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "image/png", "X-Filename": "raw.png",
        }, data=self.png)
        self.assertEqual(valid.status_code, 201)
        invalid = self.client.post("/api/screenshot-inbox/upload", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "image/png",
        }, data=b"not an image")
        self.assertEqual(invalid.status_code, 400)


if __name__ == "__main__":
    unittest.main()
