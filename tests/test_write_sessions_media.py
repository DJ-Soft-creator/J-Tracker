import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("PIL") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "write-session-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-write-session-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import write_sessions


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class WriteSessionMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": "u1", "username": "Alex", "password": "x"}]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": [{"id": "schnell", "label": "Schnell", "type": "simple"}]}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "u1"
            session["csrf_token"] = "csrf"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf"}

    def create(self, content="Gedanke"):
        response = self.client.post("/api/write-sessions", headers=self.headers, json={"content": content, "template_id": "schnell"})
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def test_sessions_are_revision_safe_and_start_with_fixed_seven_day_expiry(self):
        created = self.create()
        start = datetime.fromisoformat(created["created_at"])
        expiry = datetime.fromisoformat(created["expires_at"])
        self.assertEqual(expiry - start, timedelta(days=7))
        updated = self.client.put(f"/api/write-sessions/{created['id']}", headers=self.headers, json={
            "content": "Neuer Gedanke", "revision": created["revision"], "template_id": "schnell",
        })
        self.assertEqual(updated.status_code, 200)
        conflict = self.client.put(f"/api/write-sessions/{created['id']}", headers=self.headers, json={
            "content": "Parallel", "revision": created["revision"], "template_id": "schnell",
        })
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["content"], "Neuer Gedanke")

    def test_active_session_can_be_deleted_but_archived_session_is_preserved(self):
        created = self.create("Nur ein Entwurf")
        session_dir = self.data / "u1" / "write_sessions" / created["id"]
        deleted = self.client.delete(f"/api/write-sessions/{created['id']}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json(), {"ok": True, "id": created["id"]})
        self.assertFalse(session_dir.exists())

        archived = self.create("Aufbewahren")
        self.assertEqual(self.client.post(
            f"/api/write-sessions/{archived['id']}/decision", headers=self.headers, json={"action": "archive"},
        ).status_code, 200)
        preserved = self.client.delete(f"/api/write-sessions/{archived['id']}", headers=self.headers)
        self.assertEqual(preserved.status_code, 409)
        self.assertTrue((self.data / "u1" / "write_sessions" / archived["id"] / "session.json").is_file())

        recording = self.create("Aufnahme läuft")
        self.assertEqual(self.client.post(
            f"/api/write-sessions/{recording['id']}/audio", headers=self.headers, json={"mime_type": "audio/webm"},
        ).status_code, 201)
        blocked = self.client.delete(f"/api/write-sessions/{recording['id']}", headers=self.headers)
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue((self.data / "u1" / "write_sessions" / recording["id"] / "session.json").is_file())

    def test_expired_session_requires_decision_and_extends_exactly_one_day(self):
        created = self.create()
        path = self.data / "u1" / "write_sessions" / created["id"] / "session.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        path.write_text(json.dumps(data), encoding="utf-8")
        blocked = self.client.put(f"/api/write-sessions/{created['id']}", headers=self.headers, json={
            "content": "Zu spät", "revision": created["revision"], "template_id": "schnell",
        })
        self.assertEqual(blocked.status_code, 409)
        before = datetime.now(timezone.utc).astimezone()
        extended = self.client.post(f"/api/write-sessions/{created['id']}/decision", headers=self.headers, json={"action": "extend"})
        self.assertEqual(extended.status_code, 200)
        new_expiry = datetime.fromisoformat(extended.get_json()["expires_at"])
        self.assertGreaterEqual(new_expiry, before + timedelta(hours=23, minutes=59))
        self.assertLessEqual(new_expiry, before + timedelta(days=1, minutes=1))

    def test_photo_and_chunked_audio_can_be_submitted_without_text(self):
        created = self.create("")
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        photo = self.client.post(
            f"/api/write-sessions/{created['id']}/images", headers=self.headers,
            data={"captured_at": "2026-08-10T12:00:00+02:00", "file": (io.BytesIO(png), "foto.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(photo.status_code, 201)
        audio = self.client.post(f"/api/write-sessions/{created['id']}/audio", headers=self.headers, json={
            "mime_type": "audio/webm", "captured_at": "2026-08-10T12:01:00+02:00",
        })
        self.assertEqual(audio.status_code, 201)
        media_id = audio.get_json()["id"]
        for index, chunk in enumerate((b"audio-", b"data")):
            uploaded = self.client.put(
                f"/api/write-sessions/media/{media_id}/chunks/{index}", headers={**self.headers, "Content-Type": "application/octet-stream"}, data=chunk,
            )
            self.assertEqual(uploaded.status_code, 200)
        completed = self.client.post(f"/api/write-sessions/media/{media_id}/complete", headers=self.headers, json={"chunk_count": 2})
        self.assertEqual(completed.status_code, 200)
        session_path = self.data / "u1" / "write_sessions" / created["id"] / "session.json"
        session_data = json.loads(session_path.read_text(encoding="utf-8"))
        session_data["created_at"] = "2026-08-09T23:58:00+02:00"
        session_path.write_text(json.dumps(session_data), encoding="utf-8")
        submitted_at = datetime(2026, 8, 10, 0, 2, 30, tzinfo=timezone(timedelta(hours=2)))
        with mock.patch.object(main, "get_tz_aware_now", return_value=(submitted_at, submitted_at.tzinfo)):
            submitted = self.client.post("/api/submit", headers=self.headers, json={
                "template_id": "schnell", "content": "", "write_session_id": created["id"],
            })
        self.assertEqual(submitted.status_code, 200)
        journal = next((self.data / "u1").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertIn("- 09.08. 23:58:00", journal)
        self.assertIn("~~10.08. 00:02:30~~", journal)
        self.assertIn("Foto | Aufnahmezeit: 2026-08-10T12:00:00+02:00", journal)
        self.assertIn("Sprachnachricht | Aufnahmezeit: 2026-08-10T12:01:00+02:00", journal)
        dashboard = self.client.get("/api/dashboard/refresh").get_json()
        self.assertEqual({item["type"] for item in dashboard[0]["media"]}, {"image", "audio"})
        document = main.brain_module._build_document("personal", self.data / "u1", next((self.data / "u1").rglob("Journal_*.md")), "journal")
        media_block = next(block for block in document["blocks"] if "jt:media" in block["text"])
        brain_result = main.brain_module._serialise_result(document, media_block, "u1")
        self.assertEqual({item["type"] for item in brain_result["media"]}, {"image", "audio"})
        self.assertEqual(media_block["stable_anchor"], "timestamp:08-09T23:58:00")

    def test_submitted_document_uses_final_day_path_and_cleans_session_media(self):
        created = self.create("")
        uploaded = self.client.post(
            f"/api/write-sessions/{created['id']}/documents", headers=self.headers,
            data={"captured_at": "2026-08-10T14:35:01+02:00", "file": (io.BytesIO(b"private document"), "Notizen.pdf", "application/pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        media_id = uploaded.get_json()["id"]
        submitted = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "", "write_session_id": created["id"],
        })
        self.assertEqual(submitted.status_code, 200)
        journal = next((self.data / "u1").rglob("Journal_*.md"))
        final = journal.parent / "media" / f"2026-08-10_14-35-01_{media_id}.pdf"
        self.assertEqual(final.read_bytes(), b"private document")
        self.assertFalse((self.data / "u1" / "write_sessions" / created["id"] / "media").exists())
        self.assertIn(f'"media_path":"2026/08/10/media/2026-08-10_14-35-01_{media_id}.pdf"', journal.read_text(encoding="utf-8"))
        final_url = f"/api/write-sessions/media/final?path=2026%2F08%2F10%2Fmedia%2F2026-08-10_14-35-01_{media_id}.pdf"
        self.assertEqual(self.client.get(final_url).data, b"private document")
        self.assertEqual(self.client.get(f"/api/write-sessions/media/{media_id}/original").data, b"private document")

    def test_journal_failure_rolls_media_back_to_its_session(self):
        created = self.create("")
        uploaded = self.client.post(
            f"/api/write-sessions/{created['id']}/documents", headers=self.headers,
            data={"file": (io.BytesIO(b"keep me"), "Notizen.pdf", "application/pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        original = self.data / "u1" / "write_sessions" / created["id"] / "media" / uploaded.get_json()["id"] / "original.pdf"
        with mock.patch.object(main, "_append_journal_entry", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.client.post("/api/submit", headers=self.headers, json={
                    "template_id": "schnell", "content": "", "write_session_id": created["id"],
                })
        self.assertTrue(original.is_file())
        self.assertFalse(any((self.data / "u1").glob("*/*/*/media/*.pdf")))

    def test_submitted_audios_get_dated_sidecars_and_are_transcribed_in_place(self):
        created = self.create("Zwei Sprachnachrichten")
        media_ids = []
        for second in (1, 2):
            audio = self.client.post(f"/api/write-sessions/{created['id']}/audio", headers=self.headers, json={
                "mime_type": "audio/webm", "captured_at": f"2026-08-10T12:01:0{second}+02:00",
            })
            self.assertEqual(audio.status_code, 201)
            media_id = audio.get_json()["id"]
            media_ids.append(media_id)
            self.assertEqual(self.client.put(
                f"/api/write-sessions/media/{media_id}/chunks/0",
                headers={**self.headers, "Content-Type": "application/octet-stream"}, data=f"audio-{second}".encode(),
            ).status_code, 200)
            self.assertEqual(self.client.post(
                f"/api/write-sessions/media/{media_id}/complete", headers=self.headers, json={"chunk_count": 1},
            ).status_code, 200)

        # Preserve a transcript produced by the former session-scanning scheduler.
        first_session_media = self.data / "u1" / "write_sessions" / created["id"] / "media" / media_ids[0]
        first_metadata_path = first_session_media / "metadata.json"
        first_metadata = json.loads(first_metadata_path.read_text(encoding="utf-8"))
        first_metadata.update(transcription_status="completed", transcript_text="Bereits transkribiert")
        first_metadata_path.write_text(json.dumps(first_metadata), encoding="utf-8")
        (first_session_media / "transcript.json").write_text(json.dumps({
            "text": "Bereits transkribiert", "segments": [{"start": 0.0, "end": 1.0, "text": "Bereits transkribiert"}],
        }), encoding="utf-8")

        submitted = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "Zwei Sprachnachrichten", "write_session_id": created["id"],
        })
        self.assertEqual(submitted.status_code, 200)
        journal = next((self.data / "u1").rglob("Journal_*.md"))
        speech_dir = journal.parent / "media" / "Sprachi"
        for index, (second, media_id) in enumerate(zip((1, 2), media_ids)):
            basename = f"2026-08-10_12-01-0{second}_{media_id}"
            self.assertEqual((speech_dir / f"{basename}_audio.webm").read_bytes(), f"audio-{second}".encode())
            metadata = json.loads((speech_dir / f"{basename}_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["transcription_status"], "completed" if index == 0 else "pending")
            self.assertEqual((speech_dir / f"{basename}_transcript.json").exists(), index == 0)

        spec = importlib.util.spec_from_file_location("whisper_scheduler_test", ROOT / "scripts" / "whisper-scheduler.py")
        scheduler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scheduler)
        scheduler.DATA_DIR = self.data
        with mock.patch.object(scheduler, "_call_whisper", side_effect=lambda path: {
            "text": f"Transkript für {path.name}",
            "segments": [{"start": 0.0, "end": 1.5, "text": "Segment"}],
        }):
            self.assertEqual(scheduler.run(), {"completed": 1, "errors": 0})

        updated_journal = journal.read_text(encoding="utf-8")
        self.assertEqual(updated_journal.count("<!-- jt:transcript"), 2)
        for index, (second, media_id) in enumerate(zip((1, 2), media_ids)):
            basename = f"2026-08-10_12-01-0{second}_{media_id}"
            transcript = json.loads((speech_dir / f"{basename}_transcript.json").read_text(encoding="utf-8"))
            self.assertEqual(transcript["segments"][0]["end"], 1.0 if index == 0 else 1.5)
            metadata = json.loads((speech_dir / f"{basename}_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["transcription_status"], "completed")
            expected_text = "Bereits transkribiert" if index == 0 else f"Transkript für {basename}_audio.webm"
            self.assertIn(expected_text, updated_journal)

    def test_document_can_be_removed_before_submit_and_incomplete_audio_is_rejected(self):
        created = self.create("")
        uploaded = self.client.post(
            f"/api/write-sessions/{created['id']}/documents", headers=self.headers,
            data={"captured_at": "2026-08-10T12:00:00+02:00", "file": (io.BytesIO(b"private document"), "Notizen.pdf", "application/pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        document = uploaded.get_json()
        original = self.data / "u1" / "write_sessions" / created["id"] / "media" / document["id"] / "original.pdf"
        self.assertTrue(original.is_file())
        removed = self.client.delete(f"/api/write-sessions/{created['id']}/media/{document['id']}", headers=self.headers)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.get_json()["media"], [])
        self.assertFalse(original.exists())

        audio = self.client.post(f"/api/write-sessions/{created['id']}/audio", headers=self.headers, json={"mime_type": "audio/webm"})
        self.assertEqual(audio.status_code, 201)
        audio_id = audio.get_json()["id"]
        self.assertEqual(self.client.put(
            f"/api/write-sessions/media/{audio_id}/chunks/0", headers={**self.headers, "Content-Type": "application/octet-stream"}, data=b"first",
        ).status_code, 200)
        incomplete = self.client.post(f"/api/write-sessions/media/{audio_id}/complete", headers=self.headers, json={"chunk_count": 2})
        self.assertEqual(incomplete.status_code, 409)
        metadata = self.data / "u1" / "write_sessions" / created["id"] / "media" / audio_id / "metadata.json"
        self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["status"], "failed")
        self.assertEqual(self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "", "write_session_id": created["id"],
        }).status_code, 409)
    def test_manual_transcription_trigger_and_timestamped_journal_text(self):
        triggered = self.client.post("/api/write-sessions/transcriptions/run", headers=self.headers, json={})
        self.assertEqual(triggered.status_code, 200)
        trigger = self.data / "whisper_jobs" / "manual" / "u1.json"
        self.assertEqual(json.loads(trigger.read_text(encoding="utf-8"))["user_id"], "u1")

        journal = self.data / "u1" / "2026" / "08" / "10" / "Journal_2026-08-10.md"
        journal.parent.mkdir(parents=True)
        media_id = "11111111-1111-1111-1111-111111111111"
        journal.write_text(
            f'# Journal 2026-08-10\n\n<!-- jt:media {{"id":"{media_id}","type":"audio"}} -->\nSprachnachricht | Aufnahmezeit: 2026-08-10T12:00:00+02:00\n',
            encoding="utf-8",
        )
        spec = importlib.util.spec_from_file_location("whisper_scheduler_test", ROOT / "scripts" / "whisper-scheduler.py")
        scheduler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scheduler)
        scheduler.DATA_DIR = self.data
        scheduler._apply_to_journal("u1", {
            "id": media_id, "journal_path": "2026/08/10/Journal_2026-08-10.md",
        }, {"text": "Das ist der lesbare Fließtext.", "segments": [{"start": 0, "end": 2, "text": "Das ist der lesbare Fließtext."}]})
        updated = journal.read_text(encoding="utf-8")
        self.assertIn("### Transkript #Sprachnachricht #Transkription\nDas ist der lesbare Fließtext.", updated)
        self.assertTrue(any((journal.parent / "_Backup").glob("*.bak")))
        self.assertTrue(any((self.data / "indexes" / "brain_rebuild_requests").glob("*.json")))

    def test_manual_transcription_ignores_unreadable_trigger_directory(self):
        spec = importlib.util.spec_from_file_location("whisper_scheduler_test", ROOT / "scripts" / "whisper-scheduler.py")
        scheduler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scheduler)
        scheduler.DATA_DIR = self.data
        with mock.patch.object(Path, "is_dir", side_effect=PermissionError("denied")):
            self.assertEqual(scheduler._manual_triggers(), [])


if __name__ == "__main__":
    unittest.main()
