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
    import ai_attachments
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

    def test_session_media_is_ordered_by_capture_time_not_by_media_uuid(self):
        created = self.create("Mit Anhängen")
        directory = self.data / "u1" / "write_sessions" / created["id"] / "media"
        recordings = {
            "ffffffff-0000-0000-0000-000000000001": "2026-08-23T07:04:57+00:00",
            "00000000-0000-0000-0000-000000000002": "2026-08-23T09:07:00+02:00",
            "aaaaaaaa-0000-0000-0000-000000000003": "2026-08-23T07:06:01+00:00",
        }
        for media_id, captured_at in recordings.items():
            (directory / media_id).mkdir(parents=True)
            (directory / media_id / "metadata.json").write_text(json.dumps({
                "schema": 1, "id": media_id, "session_id": created["id"], "user_id": "u1",
                "type": "audio", "mime_type": "audio/mp4", "captured_at": captured_at, "status": "ready",
            }), encoding="utf-8")

        items = write_sessions._media_items(directory.parent)

        # Mixed UTC offsets must compare as instants, not as strings.
        self.assertEqual([item["captured_at"] for item in items], [
            "2026-08-23T07:04:57+00:00", "2026-08-23T07:06:01+00:00", "2026-08-23T09:07:00+02:00",
        ])

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

    def test_session_list_keeps_the_first_line_as_title_and_exposes_a_short_continuation(self):
        created = self.create("Titel der Session\nZweite Zeile mit etwas mehr Kontext\nDritte Zeile")
        listed = next(item for item in self.client.get("/api/write-sessions").get_json()["sessions"] if item["id"] == created["id"])
        self.assertEqual(listed["title"], "Titel der Session")
        self.assertEqual(listed["preview"], "Zweite Zeile mit etwas mehr Kontext Dritte Zeile")
        self.assertNotIn("content", listed)

    def test_empty_text_session_is_recoverably_discarded_but_media_session_stays_open(self):
        created = self.create("Nur vorübergehend")
        discarded = self.client.put(f"/api/write-sessions/{created['id']}", headers=self.headers, json={
            "content": "", "revision": created["revision"], "template_id": "schnell", "discard_if_empty": True,
        })
        self.assertEqual(discarded.status_code, 200)
        self.assertEqual(discarded.get_json()["status"], "discarded")
        stored = json.loads((self.data / "u1" / "write_sessions" / created["id"] / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["discard_reason"], "empty")
        self.assertNotIn(created["id"], {item["id"] for item in self.client.get("/api/write-sessions").get_json()["sessions"]})

        media_session = self.create("")
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        uploaded = self.client.post(
            f"/api/write-sessions/{media_session['id']}/images", headers=self.headers,
            data={"file": (io.BytesIO(png), "foto.png", "image/png")}, content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        preserved = self.client.put(f"/api/write-sessions/{media_session['id']}", headers=self.headers, json={
            "content": "", "revision": media_session["revision"], "template_id": "schnell", "discard_if_empty": True,
        })
        self.assertEqual(preserved.status_code, 200)
        self.assertEqual(preserved.get_json()["status"], "active")
        self.assertEqual(preserved.get_json()["media_count"], 1)

    def test_successful_non_journal_template_completes_its_write_session(self):
        main.CONFIG_PATH.write_text(json.dumps({"templates": [{
            "id": "einkaufsliste", "label": "Einkaufsliste", "type": "simple",
            "write_to_journal": False, "target_file": "einkaufsliste.md",
        }]}), encoding="utf-8")
        created_response = self.client.post("/api/write-sessions", headers=self.headers, json={
            "content": "Ein Testpunkt", "template_id": "einkaufsliste",
        })
        self.assertEqual(created_response.status_code, 201)
        created = created_response.get_json()
        with mock.patch.object(main.family_module, "append_task_to_target_file") as append:
            submitted = self.client.post("/api/submit", headers=self.headers, json={
                "template_id": "einkaufsliste", "content": "Ein Testpunkt", "write_session_id": created["id"],
            })
        self.assertEqual(submitted.status_code, 200)
        append.assert_called_once()
        stored = json.loads((self.data / "u1" / "write_sessions" / created["id"] / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["status"], "submitted")
        self.assertEqual(stored["submission_target"], "einkaufsliste.md")
        self.assertNotIn(created["id"], {item["id"] for item in self.client.get("/api/write-sessions").get_json()["sessions"]})

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

    def test_all_active_sessions_can_be_deleted_without_touching_archived_or_other_users(self):
        first = self.create("Erster kaputter Gedanke")
        recording = self.create("Festhängende Aufnahme")
        self.assertEqual(self.client.post(
            f"/api/write-sessions/{recording['id']}/audio", headers=self.headers, json={"mime_type": "audio/webm"},
        ).status_code, 201)
        archived = self.create("Archiv bleibt")
        self.assertEqual(self.client.post(
            f"/api/write-sessions/{archived['id']}/decision", headers=self.headers, json={"action": "archive"},
        ).status_code, 200)
        other, other_directory = write_sessions._new_session("u2", "Andere Person")

        deleted = self.client.delete("/api/write-sessions", headers=self.headers)

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["deleted"], 2)
        self.assertCountEqual(deleted.get_json()["ids"], [first["id"], recording["id"]])
        self.assertFalse((self.data / "u1" / "write_sessions" / first["id"]).exists())
        self.assertFalse((self.data / "u1" / "write_sessions" / recording["id"]).exists())
        self.assertTrue((self.data / "u1" / "write_sessions" / archived["id"] / "session.json").is_file())
        self.assertEqual(other["user_id"], "u2")
        self.assertTrue((other_directory / "session.json").is_file())

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
        photo_id = photo.get_json()["id"]
        audio = self.client.post(f"/api/write-sessions/{created['id']}/audio", headers=self.headers, json={
            "mime_type": "audio/webm", "captured_at": "2026-08-10T12:01:00+02:00",
        })
        self.assertEqual(audio.status_code, 201)
        media_id = audio.get_json()["id"]
        titled = self.client.put(
            f"/api/write-sessions/{created['id']}/media/{media_id}", headers=self.headers,
            json={"title": "  Morgenidee\nfür den Spaziergang  "},
        )
        self.assertEqual(titled.status_code, 200)
        self.assertEqual(next(item["title"] for item in titled.get_json()["media"] if item["id"] == media_id), "Morgenidee für den Spaziergang")
        photo_titled = self.client.put(
            f"/api/write-sessions/{created['id']}/media/{photo_id}", headers=self.headers,
            json={"title": "Urlaub #reise"},
        )
        self.assertEqual(photo_titled.status_code, 200)
        self.assertEqual(next(item["title"] for item in photo_titled.get_json()["media"] if item["id"] == photo_id), "Urlaub #reise")
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
        self.assertIn("Foto: Urlaub #reise | Aufnahmezeit: 2026-08-10T12:00:00+02:00", journal)
        self.assertIn("Sprachnachricht: Morgenidee für den Spaziergang | Aufnahmezeit: 2026-08-10T12:01:00+02:00", journal)
        self.assertIn('"title":"Morgenidee für den Spaziergang"', journal)
        dashboard = self.client.get("/api/dashboard/refresh?date=2026-08-10").get_json()
        self.assertEqual({item["type"] for item in dashboard[0]["media"]}, {"image", "audio"})
        self.assertEqual(next(item["title"] for item in dashboard[0]["media"] if item["type"] == "audio"), "Morgenidee für den Spaziergang")
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
        submitted_at = datetime(2026, 8, 10, 14, 36, tzinfo=timezone(timedelta(hours=2)))
        with mock.patch.object(main, "get_tz_aware_now", return_value=(submitted_at, submitted_at.tzinfo)):
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

    def test_final_m4a_media_is_served_as_audio_mp4(self):
        relative = "2026/08/10/media/Sprachi/2026-08-10_14-35-01_11111111-1111-1111-1111-111111111111_audio.m4a"
        target = self.data / "u1" / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"m4a audio")

        response = self.client.get("/api/write-sessions/media/final", query_string={"path": relative})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "audio/mp4")
        self.assertEqual(response.data, b"m4a audio")

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
        with mock.patch.object(scheduler, "_call_whisper", side_effect=lambda path, model=None: {
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

    def test_several_recordings_get_headings_that_name_them(self):
        """Mehrere Sprachnachrichten in einem Eintrag muessen zuordenbar sein.

        Ohne Titel nummeriert der Eintrag sie, mit Titel steht der Titel in der
        Ueberschrift - vorher hiessen alle Bloecke gleich.
        """
        created = self.create("Zwei Sprachnachrichten")
        media_ids = []
        for second in (1, 2):
            audio = self.client.post(f"/api/write-sessions/{created['id']}/audio", headers=self.headers, json={
                "mime_type": "audio/webm", "captured_at": f"2026-08-10T12:01:0{second}+02:00",
            })
            media_id = audio.get_json()["id"]
            media_ids.append(media_id)
            self.client.put(
                f"/api/write-sessions/media/{media_id}/chunks/0",
                headers={**self.headers, "Content-Type": "application/octet-stream"}, data=f"audio-{second}".encode(),
            )
            self.client.post(f"/api/write-sessions/media/{media_id}/complete", headers=self.headers, json={"chunk_count": 1})
        self.client.put(
            f"/api/write-sessions/{created['id']}/media/{media_ids[1]}",
            headers=self.headers, json={"title": "Arzttermin"},
        )
        transcript = {"text": "Gesprochener Text", "segments": []}
        with mock.patch.object(main.ai_attachments_module, "_transcribe", return_value=(transcript, transcript["text"])):
            for media_id in media_ids:
                self.assertEqual(self.client.post(
                    f"/api/write-sessions/media/{media_id}/transcribe", headers=self.headers, json={},
                ).status_code, 200)

        self.assertEqual(self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "Zwei Sprachnachrichten", "write_session_id": created["id"],
        }).status_code, 200)
        journal = next((self.data / "u1").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertIn("### Transkript 1 ", journal)
        self.assertIn("### Transkript: Arzttermin ", journal)

    def test_an_immediately_transcribed_recording_is_written_exactly_once(self):
        """Der Editor darf das Transkript nicht zusaetzlich als Text aufnehmen.

        Sonst stand derselbe Satz zweimal im Eintrag: einmal als Fliesstext aus
        dem Editor und einmal als markierter Block unter der Aufnahme.
        """
        created = self.create("Sprachnotiz")
        media_id = self._record_audio(created["id"])
        transcript = {"text": "Nur einmal im Eintrag", "segments": []}
        with mock.patch.object(main.ai_attachments_module, "_transcribe", return_value=(transcript, transcript["text"])):
            self.client.post(f"/api/write-sessions/media/{media_id}/transcribe", headers=self.headers, json={})
        self.assertEqual(self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "Sprachnotiz", "write_session_id": created["id"],
        }).status_code, 200)
        journal = next((self.data / "u1").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertEqual(journal.count("Nur einmal im Eintrag"), 1)
        # Der Sidecar laesst eine fertige Aufnahme liegen, statt sie am naechsten
        # Tag noch einmal zu transkribieren.
        spec = importlib.util.spec_from_file_location("whisper_scheduler_once", ROOT / "scripts" / "whisper-scheduler.py")
        scheduler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scheduler)
        scheduler.DATA_DIR = self.data
        self.assertEqual(scheduler.run(), {"completed": 0, "errors": 0})
        self.assertEqual(
            next((self.data / "u1").rglob("Journal_*.md")).read_text(encoding="utf-8").count("Nur einmal im Eintrag"), 1,
        )

        # Der Schreiben-Tab kopiert das Ergebnis nicht mehr selbst in den Editor.
        source = (ROOT / "app" / "static" / "write-session.js").read_text(encoding="utf-8")
        self.assertNotIn("appendTranscriptToEditor(updated.transcript_text)", source)

    def _age_chunks(self, session_id, media_id, minutes=30):
        """Pretend the recorder fell silent -- a killed app stops writing chunks."""
        stale = datetime.now(timezone.utc).timestamp() - minutes * 60
        chunks = self.data / "u1" / "write_sessions" / session_id / "media" / media_id / "chunks"
        for chunk in chunks.glob("*.part"):
            os.utime(chunk, (stale, stale))

    def test_recording_interrupted_by_a_killed_app_is_finished_by_the_server(self):
        # The phone app is killed mid-recording: the chunks arrived, /complete
        # never did. Without recovery the session could neither be sent nor
        # discarded, and the recording was lost.
        created = self.create("")
        audio = self.client.post(f"/api/write-sessions/{created['id']}/audio", headers=self.headers, json={
            "mime_type": "audio/mp4", "captured_at": "2026-08-10T12:01:00+02:00",
        })
        self.assertEqual(audio.status_code, 201)
        media_id = audio.get_json()["id"]
        for index, chunk in enumerate((b"audio-", b"data")):
            self.assertEqual(self.client.put(
                f"/api/write-sessions/media/{media_id}/chunks/{index}",
                headers={**self.headers, "Content-Type": "application/octet-stream"}, data=chunk,
            ).status_code, 200)

        # While the recording may still be running, blocking stays correct.
        blocked = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "", "write_session_id": created["id"],
        })
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(blocked.get_json()["write_session_conflict"])

        self._age_chunks(created["id"], media_id)
        resumed = self.client.get(f"/api/write-sessions/{created['id']}", headers=self.headers)
        self.assertEqual(resumed.status_code, 200)
        recovered = next(item for item in resumed.get_json()["media"] if item["id"] == media_id)
        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(recovered["chunk_count"], 2)
        original = self.data / "u1" / "write_sessions" / created["id"] / "media" / media_id / "original.m4a"
        self.assertEqual(original.read_bytes(), b"audio-data")

        submitted_at = datetime(2026, 8, 10, 12, 2, 30, tzinfo=timezone(timedelta(hours=2)))
        with mock.patch.object(main, "get_tz_aware_now", return_value=(submitted_at, submitted_at.tzinfo)):
            submitted = self.client.post("/api/submit", headers=self.headers, json={
                "template_id": "schnell", "content": "", "write_session_id": created["id"],
            })
        self.assertEqual(submitted.status_code, 200)
        journal = next((self.data / "u1").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertIn("Aufnahmezeit: 2026-08-10T12:01:00+02:00", journal)

    def test_recording_without_any_chunk_never_blocks_discarding_the_session(self):
        created = self.create("")
        audio = self.client.post(f"/api/write-sessions/{created['id']}/audio", headers=self.headers, json={"mime_type": "audio/mp4"})
        media_id = audio.get_json()["id"]
        metadata_path = self.data / "u1" / "write_sessions" / created["id"] / "media" / media_id / "metadata.json"
        item = json.loads(metadata_path.read_text(encoding="utf-8"))
        item["received_at"] = write_sessions._iso(write_sessions._now() - timedelta(hours=1))
        metadata_path.write_text(json.dumps(item), encoding="utf-8")

        listed = self.client.get("/api/write-sessions", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["status"], "failed")

        discarded = self.client.delete(f"/api/write-sessions/{created['id']}", headers=self.headers)
        self.assertEqual(discarded.status_code, 200)
        self.assertFalse((self.data / "u1" / "write_sessions" / created["id"]).exists())

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

    def _record_audio(self, session_id, data=b"fake-audio"):
        started = self.client.post(f"/api/write-sessions/{session_id}/audio", headers=self.headers, json={"mime_type": "audio/webm"}).get_json()
        self.assertEqual(self.client.put(
            f"/api/write-sessions/media/{started['id']}/chunks/0", headers={**self.headers, "Content-Type": "application/octet-stream"}, data=data,
        ).status_code, 200)
        self.assertEqual(self.client.post(f"/api/write-sessions/media/{started['id']}/complete", headers=self.headers, json={"chunk_count": 1}).status_code, 200)
        return started["id"]

    def test_unsent_recording_can_be_transcribed_immediately(self):
        created = self.create("Sprachnotiz")
        media_id = self._record_audio(created["id"])

        transcript = {"text": "Direkt transkribiert", "segments": []}
        with mock.patch.object(main.ai_attachments_module, "_transcribe", return_value=(transcript, transcript["text"])) as transcribe:
            response = self.client.post(f"/api/write-sessions/media/{media_id}/transcribe", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["transcription_status"], "completed")
        self.assertEqual(response.get_json()["transcript_text"], "Direkt transkribiert")
        transcribe.assert_called_once()
        self.assertEqual(transcribe.call_args.args[0].name, "original.webm")

        metadata_path = self.data / "u1" / "write_sessions" / created["id"] / "media" / media_id / "metadata.json"
        self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["transcription_status"], "completed")

        # A second request is idempotent and never calls whisper again.
        with mock.patch.object(main.ai_attachments_module, "_transcribe") as transcribe_again:
            repeat = self.client.post(f"/api/write-sessions/media/{media_id}/transcribe", headers=self.headers, json={})
        self.assertEqual(repeat.status_code, 200)
        transcribe_again.assert_not_called()

    def test_chosen_quality_step_reaches_whisper_and_the_scheduled_run(self):
        created = self.create("Sprachnotiz")
        media_id = self._record_audio(created["id"])

        transcript = {"text": "Mit small transkribiert", "segments": []}
        with mock.patch.object(main.ai_attachments_module, "_transcribe", return_value=(transcript, transcript["text"])) as transcribe:
            response = self.client.post(
                f"/api/write-sessions/media/{media_id}/transcribe", headers=self.headers, json={"model": "small"},
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(transcribe.call_args.args[3], "small")

        # A junk model never reaches whisper; the configured default is used.
        second_id = self._record_audio(created["id"])
        with mock.patch.object(main.ai_attachments_module, "_transcribe", return_value=(transcript, transcript["text"])) as fallback:
            self.client.post(
                f"/api/write-sessions/media/{second_id}/transcribe", headers=self.headers, json={"model": "small; rm -rf /"},
            )
        self.assertEqual(fallback.call_args.args[3], os.environ.get("WHISPER_MODEL", "base"))

        triggered = self.client.post("/api/write-sessions/transcriptions/run", headers=self.headers, json={"model": "medium"})
        self.assertEqual(triggered.get_json()["model"], "medium")
        trigger = json.loads((self.data / "whisper_jobs" / "manual" / "u1.json").read_text(encoding="utf-8"))
        self.assertEqual(trigger["model"], "medium")

    def test_transcription_model_catalogue_survives_an_unreachable_whisper_service(self):
        ai_attachments._MODEL_CATALOG_CACHE.update(fetched_at=0.0, value=None)
        with mock.patch.object(ai_attachments.urllib.request, "urlopen", side_effect=OSError("down")):
            response = self.client.get("/api/write-sessions/transcriptions/models")
        ai_attachments._MODEL_CATALOG_CACHE.update(fetched_at=0.0, value=None)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["available"])
        self.assertEqual([item["id"] for item in payload["models"]], [payload["default"]])

    def test_transcribe_now_rejects_a_recording_still_being_saved(self):
        created = self.create("Sprachnotiz")
        started = self.client.post(f"/api/write-sessions/{created['id']}/audio", headers=self.headers, json={"mime_type": "audio/webm"}).get_json()
        response = self.client.post(f"/api/write-sessions/media/{started['id']}/transcribe", headers=self.headers, json={})
        self.assertEqual(response.status_code, 409)

    def test_transcribe_now_reports_whisper_failure_and_stays_retryable(self):
        created = self.create("Sprachnotiz")
        media_id = self._record_audio(created["id"])

        with mock.patch.object(main.ai_attachments_module, "_transcribe", side_effect=ai_attachments.AiAttachmentError("Whisper nicht erreichbar")):
            response = self.client.post(f"/api/write-sessions/media/{media_id}/transcribe", headers=self.headers, json={})
        self.assertEqual(response.status_code, 502)
        metadata_path = self.data / "u1" / "write_sessions" / created["id"] / "media" / media_id / "metadata.json"
        self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["transcription_status"], "error")

        transcript = {"text": "Beim zweiten Versuch geklappt", "segments": []}
        with mock.patch.object(main.ai_attachments_module, "_transcribe", return_value=(transcript, transcript["text"])):
            retry = self.client.post(f"/api/write-sessions/media/{media_id}/transcribe", headers=self.headers, json={})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json()["transcription_status"], "completed")

    def test_manual_transcription_trigger_and_timestamped_journal_text(self):
        triggered = self.client.post("/api/write-sessions/transcriptions/run", headers=self.headers, json={})
        self.assertEqual(triggered.status_code, 200)
        trigger = self.data / "whisper_jobs" / "manual" / "u1.json"
        self.assertEqual(json.loads(trigger.read_text(encoding="utf-8"))["user_id"], "u1")
        self.assertEqual(triggered.get_json()["status"], "queued")
        status = self.client.get("/api/write-sessions/transcriptions/status", headers=self.headers)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["status"], "queued")

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
        requests = scheduler._manual_triggers()
        self.assertEqual(requests[0]["user_id"], "u1")
        scheduler._manual_status(requests[0], "running", started_at="2026-08-10T12:01:00+02:00")
        running = self.client.get("/api/write-sessions/transcriptions/status", headers=self.headers)
        self.assertEqual(running.get_json()["status"], "running")
        scheduler._apply_to_journal("u1", {
            "id": media_id, "journal_path": "2026/08/10/Journal_2026-08-10.md",
        }, {"text": "Das ist der lesbare Fließtext.", "segments": [{"start": 0, "end": 2, "text": "Das ist der lesbare Fließtext."}]})
        updated = journal.read_text(encoding="utf-8")
        # Both writers now emit the same blank-line layout, and the block sits
        # behind the media label instead of between marker and label.
        self.assertIn(
            f'<!-- jt:transcript {{"media_id":"{media_id}"}} -->\n\n'
            "### Transkript #Sprachnachricht #Transkription #Audio-Transkription\n\n"
            "Das ist der lesbare Fließtext.",
            updated,
        )
        self.assertLess(updated.index("Aufnahmezeit:"), updated.index("jt:transcript"))
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
