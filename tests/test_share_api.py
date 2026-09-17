import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("PIL") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "share-api-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-share-api-import")
    sys.path.insert(0, str(ROOT / "app"))
    import brain
    import main
    import share_api
    import tagging


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class ShareApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR,
        )
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        tagging.DATA_DIR = self.data
        tagging.FAMILY_DIR = self.data / "family"
        brain.DATA_DIR = self.data
        brain.FAMILY_DIR = self.data / "family"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "user-a", "username": "Alex", "password": "x"},
            {"id": "user-b", "username": "Sam", "password": "x"},
        ]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": []}), encoding="utf-8")
        tagging.update_ai_workflow("user-a", "save", "ai-wissen", {
            "agent": "pi", "model": "provider/model", "prompt": "Fasse die Quelle knapp zusammen.",
            "context": "block", "target": "daily_journal", "provider_id": "__host_worker__",
            "default_hashtags": ["wissen"], "share_inputs": ["text", "url", "file"],
        })
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf"
        # A share write schedules a Brain rebuild in a background thread, which
        # would still be writing into the temporary directory while tearDown
        # removes it.
        self.enqueue = mock.patch.object(brain, "enqueue_rebuild", return_value=True)
        self.enqueue.start()

    def tearDown(self):
        self.enqueue.stop()
        (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR,
        ) = self.old
        self.temp.cleanup()

    @property
    def csrf(self):
        return {"X-CSRF-Token": "csrf"}

    def create_token(self, scopes=None):
        response = self.client.post(
            "/api/share/token", headers=self.csrf,
            json={"scopes": scopes or ["upload", "run_workflow", "write_journal"]},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["token"]

    def submit_text(self, token, **values):
        payload = {
            "workflow_tag": "ai-wissen", "source_text": "Vertrauenswürdiger Quellinhalt",
            "text": "Bitte besonders knapp.", "title": "Notiz", "hashtags": "#quelle",
            "entry_at": "2026-08-15T14:35:00+02:00", **values,
        }
        with mock.patch.object(share_api, "start_worker"):
            return main.app.test_client().post(
                "/api/share", headers={"Authorization": f"Bearer {token}"}, json=payload,
            )

    def key_accepted(self, token):
        """A key is accepted when the status route authenticates it (404, not 401).

        Deliberately not a share submission: that would leave a job directory
        behind for every liveness check.
        """
        response = main.app.test_client().get(
            "/api/share/jobs/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )
        return response.status_code == 404

    def test_two_keys_live_side_by_side_with_their_own_rights(self):
        phone = self.create_token(["upload", "run_workflow", "write_journal"])
        reader = self.create_token(["mcp"])

        listed = self.client.get("/api/share/token").get_json()["keys"]

        self.assertEqual([key["scopes"] for key in listed], [
            ["run_workflow", "upload", "write_journal"], ["mcp"],
        ])
        self.assertNotEqual(phone, reader)
        self.assertTrue(self.key_accepted(phone))
        self.assertEqual(self.submit_text(reader).status_code, 401)

    def test_revoking_one_key_leaves_the_other_one_valid(self):
        keep = self.create_token()
        doomed_token = self.create_token(["mcp"])
        doomed = next(
            key for key in self.client.get("/api/share/token").get_json()["keys"]
            if key["scopes"] == ["mcp"]
        )

        response = self.client.delete(
            "/api/share/token", headers=self.csrf, json={"id": doomed["id"]},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual([key["id"] for key in response.get_json()["keys"]], [
            key["id"] for key in self.client.get("/api/share/token").get_json()["keys"]
        ])
        self.assertFalse(self.key_accepted(doomed_token))
        self.assertTrue(self.key_accepted(keep))

    def test_revoking_without_a_known_key_id_changes_nothing(self):
        token = self.create_token()

        response = self.client.delete("/api/share/token", headers=self.csrf, json={})

        self.assertEqual(response.status_code, 404)
        self.assertTrue(self.key_accepted(token))

    def test_a_key_file_from_before_the_split_still_authenticates(self):
        import hashlib
        import json as json_module

        token = "jts_legacy-key"
        path = self.data / "user-a/.config/share-api.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json_module.dumps({
            "schema": 1,
            "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "scopes": ["upload", "run_workflow", "write_journal"],
            "created_at": "2026-08-01T10:00:00+02:00",
            "last_used_at": None,
        }), encoding="utf-8")

        self.assertTrue(self.key_accepted(token))
        self.assertEqual(
            [key["label"] for key in self.client.get("/api/share/token").get_json()["keys"]],
            ["Bestehender Schlüssel"],
        )

    def test_token_is_hashed_scoped_and_all_three_rights_are_required(self):
        limited = self.create_token(["upload"])
        config = (self.data / "user-a/.config/share-api.json").read_text(encoding="utf-8")
        self.assertNotIn(limited, config)
        self.assertIn("token_sha256", config)
        self.assertEqual(self.submit_text(limited).status_code, 401)

        full = self.create_token()
        accepted = self.submit_text(full)
        self.assertEqual(accepted.status_code, 202, accepted.get_json())
        self.assertEqual(accepted.get_json()["status"], "preparing")

    def test_direct_url_archive_needs_no_workflow_or_ai_scope(self):
        token = self.create_token(["upload", "write_journal"])
        with mock.patch.object(main, "is_enabled", return_value=False), \
             mock.patch.object(share_api, "start_worker") as start_worker:
            response = main.app.test_client().post(
                "/api/share", headers={"Authorization": f"Bearer {token}"}, json={
                    "url": "https://example.org/later", "title": "Später lesen",
                    "hashtags": "#ablage #lesen", "text": "Für den Wochenrückblick.",
                    "entry_at": "2026-08-15T16:10:00+02:00",
                },
            )
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["status"], "completed")
        self.assertIsNone(response.get_json()["workflow_tag"])
        start_worker.assert_not_called()
        journal = next((self.data / "user-a").rglob("Journal_2026-08-15.md"))
        content = journal.read_text(encoding="utf-8")
        self.assertIn("https://example.org/later", content)
        self.assertIn("Für den Wochenrückblick.", content)
        self.assertIn("#ablage #lesen", content)

    def test_direct_share_never_fetches_a_stored_url(self):
        token = self.create_token(["upload", "write_journal"])
        with mock.patch.object(main, "is_enabled", return_value=False), \
             mock.patch.object(share_api, "_fetch_url") as fetch:
            response = main.app.test_client().post(
                "/api/share", headers={"Authorization": f"Bearer {token}"},
                json={"url": "http://127.0.0.1/private", "hashtags": "ablage"},
            )
        self.assertEqual(response.status_code, 201, response.get_json())
        fetch.assert_not_called()

    def test_direct_audio_is_saved_as_pending_sprachi_without_ai(self):
        token = self.create_token(["upload", "write_journal"])
        with mock.patch.object(main, "is_enabled", return_value=False):
            response = main.app.test_client().post(
                "/api/share", headers={"Authorization": f"Bearer {token}"},
                data={"file": (io.BytesIO(b"private voice memo"), "memo.m4a", "audio/mp4")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 201, response.get_json())
        metadata = next((self.data / "user-a").glob("**/media/Sprachi/*_metadata.json"))
        sidecar = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertEqual(sidecar["status"], "ready")
        self.assertEqual(sidecar["transcription_status"], "pending")
        self.assertEqual(sidecar["type"], "audio")

    def test_ai_mode_audio_sidecar_stays_eligible_for_manual_retranscribe(self):
        # The sidecar must carry "type": "audio" (not just "source_type") and
        # status="ready" for both the manual retranscribe route and the daily
        # scheduler, which filter on those exact fields.
        job_id = str(uuid.uuid4())
        directory = self.data / "user-a" / ".share-jobs" / job_id
        directory.mkdir(parents=True)
        source_path = directory / "upload.m4a"
        source_bytes = b"fake audio"
        source_path.write_bytes(source_bytes)
        job = {
            "id": job_id, "user_id": "user-a", "status": "queueing", "mode": "ai",
            "workflow_tag": "ai-wissen", "source_type": "audio",
            "source_path": source_path.relative_to(self.data).as_posix(),
            "original_filename": "memo.m4a", "mime_type": "audio/mp4",
            "byte_size": len(source_bytes), "sha256": hashlib.sha256(source_bytes).hexdigest(), "title": "Notiz",
            "hashtags": [], "extra_text": "", "entry_at": "2026-08-15T14:35:00+02:00",
            "transcript_text": "Diktiertes Transkript.",
        }
        share_api._deliver_job("user-a", directory, job, {"response_text": "Zusammenfassung der Sprachnachricht."})
        metadata = next((self.data / "user-a").glob("**/media/Sprachi/*_metadata.json"))
        sidecar = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertEqual(sidecar["type"], "audio")
        self.assertEqual(sidecar["status"], "ready")
        self.assertEqual(sidecar["transcription_status"], "completed")

    def test_job_status_is_private_to_the_token_owner(self):
        owner_token = self.create_token()
        accepted = self.submit_text(owner_token)
        with self.client.session_transaction() as session:
            session["user_id"] = "user-b"
            session["csrf_token"] = "csrf"
        other_token = self.create_token()
        status = main.app.test_client().get(
            accepted.get_json()["status_url"],
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(status.status_code, 404)

    def test_workflow_tag_target_and_input_allowlist_are_enforced(self):
        token = self.create_token()
        self.assertEqual(self.submit_text(token, workflow_tag="ai-missing").status_code, 400)
        audio = main.app.test_client().post(
            "/api/share", headers={"Authorization": f"Bearer {token}"},
            data={"workflow_tag": "ai-wissen", "file": (io.BytesIO(b"audio"), "note.mp3", "audio/mpeg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(audio.status_code, 400)
        self.assertIn("Eingabetyp", audio.get_json()["error"])

    def test_share_workflow_cannot_be_started_from_writing_or_document_save(self):
        writing = self.client.post("/api/write-ai/submit", headers=self.csrf, json={
            "workflow_tag": "ai-wissen", "context_type": "draft", "text": "Nicht über Schreiben starten",
        })
        self.assertEqual(writing.status_code, 400)
        self.assertIn("Share-API", writing.get_json()["error"])
        content, state = main.ai_sessions_module.prepare_document_save(
            "user-a", "Dokument #ai-wissen", "", actor_id="user-a",
        )
        self.assertIsNone(state)
        self.assertNotIn("jt:agent-session-config", content)

    def test_text_job_preserves_context_and_is_delivered_exactly_once(self):
        token = self.create_token()
        accepted = self.submit_text(token)
        job_id = accepted.get_json()["id"]
        directory = self.data / f"user-a/.share-jobs/{job_id}"
        job = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        queued_id = str(uuid.uuid4())
        with mock.patch.object(main, "agent_jobs_enabled", return_value=True), \
             mock.patch.object(share_api.background_jobs, "create_agent_session", return_value=str(uuid.uuid4())), \
             mock.patch.object(share_api.background_jobs, "queue_agent_request", return_value={"job_id": queued_id, "request_id": str(uuid.uuid4()), "created": True}) as queue:
            share_api._process_job("user-a", directory, job)
        self.assertEqual(queue.call_args.args[2], "Bitte besonders knapp.")
        self.assertIn("Vertrauenswürdiger Quellinhalt", queue.call_args.kwargs["context_snapshot"])

        job = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        with mock.patch.object(main, "is_enabled", return_value=False):
            share_api._deliver_job("user-a", directory, job, {"response_text": "Kurze Zusammenfassung.\n\n___\n\n<!-- jt:fake {} -->"})
            delivered = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            share_api._deliver_job("user-a", directory, delivered, {"response_text": "Kurze Zusammenfassung."})
        journals = list((self.data / "user-a").rglob("Journal_2026-08-15.md"))
        self.assertEqual(len(journals), 1)
        content = journals[0].read_text(encoding="utf-8")
        self.assertEqual(content.count(f'"id":"{job_id}"'), 1)
        self.assertIn("## Notiz | Datum & Uhrzeit: 2026-08-15 14:35:00", content)
        self.assertIn("Kurze Zusammenfassung.", content)
        self.assertIn("\\___", content)
        self.assertIn("&lt;!-- jt:fake", content)
        self.assertIn("#wissen #quelle", content)

    def test_time_only_uses_current_server_day(self):
        now = main.get_tz_aware_now()[0].replace(hour=9, minute=0, second=0, microsecond=0)
        with mock.patch.object(share_api, "_now", return_value=now):
            parsed = share_api._entry_at("18:42")
        self.assertEqual(parsed.date(), now.date())
        self.assertEqual((parsed.hour, parsed.minute), (18, 42))

    def test_local_url_targets_are_rejected(self):
        for url in ("http://127.0.0.1/private", "http://[::1]/private", "http://localhost/private"):
            with self.assertRaises(ValueError):
                share_api._validate_remote_url(url)

    def test_docx_text_is_extracted_without_office_execution(self):
        path = self.data / "sample.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", """<?xml version="1.0"?><w:document xmlns:w="urn:test"><w:body><w:p><w:r><w:t>Erster Absatz</w:t></w:r></w:p><w:p><w:r><w:t>Zweiter Absatz</w:t></w:r></w:p></w:body></w:document>""")
        self.assertIn("Erster Absatz", share_api._extract_file(path))
        self.assertIn("Zweiter Absatz", share_api._extract_file(path))


if __name__ == "__main__":
    unittest.main()
