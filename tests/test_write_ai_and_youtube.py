import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "write-ai-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-write-ai-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import main
    import brain
    import tagging


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class WriteAiAndYoutubeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR, main.ai_sessions_module.DATA_DIR)
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.data_dir, self.data_dir / "users.json", self.data_dir / "config.json"
        tagging.DATA_DIR, tagging.FAMILY_DIR = self.data_dir, self.data_dir / "family"
        brain.DATA_DIR, brain.FAMILY_DIR = self.data_dir, self.data_dir / "family"
        main.ai_sessions_module.DATA_DIR = self.data_dir
        self.data_dir.mkdir(exist_ok=True)
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": "user-a", "username": "Alex", "password": "test"}]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({
            "ai_providers": [{"id": "provider-a", "label": "Provider A", "model": "model-a", "api_url": "http://example.invalid"}],
            "templates": [{"id": "schnell", "label": "Schnell", "type": "simple"}],
        }), encoding="utf-8")
        tagging.update_ai_workflow("user-a", "save", "ai-template", {"agent": "custom", "model": "model-a", "prompt": "Rewrite clearly", "context": "block", "target": "document", "provider_id": "provider-a"})
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf-test"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR, main.ai_sessions_module.DATA_DIR = self.old
        self.temp.cleanup()

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def test_write_ai_uses_private_draft_and_never_writes_a_journal(self):
        with mock.patch.object(main, "_call_ai_api", return_value="AI result") as call:
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": "My private draft",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "AI result")
        self.assertEqual((self.data_dir / "user-a/temp_Eingabe.md").read_text(encoding="utf-8"), "AI result")
        self.assertFalse(list((self.data_dir / "user-a").rglob("Journal_*.md")))
        self.assertIn("My private draft", call.call_args.args[2])

    def test_template_ai_receives_an_immutable_screenshot_without_consuming_the_session(self):
        text = "Beschreibe den Screenshot #ai-template"
        created = self.client.post(
            "/api/write-sessions", headers=self.headers,
            json={"content": text, "template_id": "schnell"},
        ).get_json()
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        uploaded = self.client.post(
            f"/api/write-sessions/{created['id']}/images", headers=self.headers,
            data={"file": (io.BytesIO(png), "screenshot.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.get_json())

        with mock.patch.object(main, "_call_ai_api", return_value="Bild beschrieben") as call:
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": text, "write_session_id": created["id"],
            })

        self.assertEqual(response.status_code, 200, response.get_json())
        images = call.call_args.kwargs["image_attachments"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mime_type"], "image/png")
        self.assertIn("als visueller KI-Anhang beigefügt", call.call_args.args[2])
        self.assertFalse((self.data_dir / "user-a/ai_jobs/attachments").exists())
        session = self.client.get(f"/api/write-sessions/{created['id']}").get_json()
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["media_count"], 1)

    def test_template_ai_transcribes_voice_message_into_explicit_attachment_context(self):
        text = "Fasse meine Sprachnachricht zusammen #ai-template"
        created = self.client.post(
            "/api/write-sessions", headers=self.headers,
            json={"content": text, "template_id": "schnell"},
        ).get_json()
        started = self.client.post(
            f"/api/write-sessions/{created['id']}/audio", headers=self.headers,
            json={"mime_type": "audio/webm"},
        ).get_json()
        self.assertEqual(self.client.put(
            f"/api/write-sessions/media/{started['id']}/chunks/0", headers=self.headers,
            data=b"fake-webm-audio", content_type="application/octet-stream",
        ).status_code, 200)
        self.assertEqual(self.client.post(
            f"/api/write-sessions/media/{started['id']}/complete", headers=self.headers,
            json={"chunk_count": 1},
        ).status_code, 200)

        transcript = {"text": "Heute bespreche ich den Projektstand.", "segments": []}
        with mock.patch.object(main.ai_attachments_module, "_transcribe", return_value=(transcript, transcript["text"])), \
             mock.patch.object(main, "_call_ai_api", return_value="Kurze Zusammenfassung") as call:
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": text, "write_session_id": created["id"],
            })

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn("Heute bespreche ich den Projektstand.", call.call_args.args[2])
        metadata = json.loads((
            self.data_dir / "user-a/write_sessions" / created["id"] / "media" / started["id"] / "metadata.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(metadata["transcription_status"], "completed")
        self.assertEqual(metadata["transcript_text"], transcript["text"])

    def test_independently_replaced_second_device_draft_blocks_ai_result(self):
        draft = self.client.post("/api/write-ai/draft", headers=self.headers, json={"content": "base"}).get_json()
        def ai_call(*_):
            main.write_text_file(self.data_dir / "user-a/temp_Eingabe.md", "typed on another device")
            return "AI result"
        with mock.patch.object(main, "_call_ai_api", side_effect=ai_call):
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": "base", "revision": draft["revision"],
            })
        self.assertEqual(response.status_code, 409)
        self.assertEqual((self.data_dir / "user-a/temp_Eingabe.md").read_text(encoding="utf-8"), "typed on another device")

    def test_only_text_added_after_submit_is_kept_below_ai_result(self):
        draft = self.client.post("/api/write-ai/draft", headers=self.headers, json={"content": "base"}).get_json()
        def ai_call(*_):
            main.write_text_file(self.data_dir / "user-a/temp_Eingabe.md", "base\nnew text")
            return "AI result"
        with mock.patch.object(main, "_call_ai_api", side_effect=ai_call):
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": "base", "revision": draft["revision"],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "AI result\n\nnew text")

    def test_editing_two_positions_during_ai_run_does_not_duplicate_the_document(self):
        draft = self.client.post("/api/write-ai/draft", headers=self.headers, json={"content": "first\nsecond"}).get_json()
        def ai_call(*_):
            main.write_text_file(self.data_dir / "user-a/temp_Eingabe.md", "first changed\nsecond\ncontinued")
            return "AI result"
        with mock.patch.object(main, "_call_ai_api", side_effect=ai_call):
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": "first\nsecond", "revision": draft["revision"],
            })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            (self.data_dir / "user-a/temp_Eingabe.md").read_text(encoding="utf-8"),
            "first changed\nsecond\ncontinued",
        )
        self.assertNotIn("AI result", (self.data_dir / "user-a/temp_Eingabe.md").read_text(encoding="utf-8"))

    def test_normal_submit_consumes_only_the_known_ai_draft_revision(self):
        draft = self.client.post("/api/write-ai/draft", headers=self.headers, json={"content": "AI result"}).get_json()
        saved = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "AI result",
            "consume_draft_revision": draft["revision"],
        })
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.get_json()["draft_consumed"])
        self.assertEqual((self.data_dir / "user-a/temp_Eingabe.md").read_text(encoding="utf-8"), "")

        old = self.client.post("/api/write-ai/draft", headers=self.headers, json={"content": "old"}).get_json()
        current = self.client.post("/api/write-ai/draft", headers=self.headers, json={
            "content": "newer device text", "revision": old["revision"],
        }).get_json()
        saved = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "local journal text",
            "consume_draft_revision": old["revision"],
        })
        self.assertEqual(saved.status_code, 200)
        self.assertFalse(saved.get_json()["draft_consumed"])
        self.assertEqual(saved.get_json()["draft_revision"], current["revision"])
        self.assertEqual((self.data_dir / "user-a/temp_Eingabe.md").read_text(encoding="utf-8"), "newer device text")

    def test_source_edit_can_render_simple_and_form_templates_without_writing(self):
        config = json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))
        config["templates"].extend([
            {"id": "idee", "label": "Idee", "type": "simple"},
            {"id": "stimmung", "label": "Stimmung", "type": "form", "fields": [
                {"id": "wert", "label": "Wert", "type": "number", "min": 1, "max": 10, "required": True},
            ]},
            {"id": "extern", "label": "Extern", "type": "simple", "write_to_journal": False},
        ])
        main.CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")

        idea = self.client.post("/api/write/template-preview", headers=self.headers, json={
            "template_id": "idee", "content": "Eine neue Richtung",
        })
        self.assertEqual(idea.status_code, 200, idea.get_json())
        self.assertRegex(idea.get_json()["content"], r"^## Idee \| Datum & Uhrzeit: \d{4}-\d{2}-\d{2}")
        self.assertIn("Eine neue Richtung", idea.get_json()["content"])

        mood = self.client.post("/api/write/template-preview", headers=self.headers, json={
            "template_id": "stimmung", "values": {"wert": "7"},
        })
        self.assertEqual(mood.status_code, 200, mood.get_json())
        self.assertIn("## Stimmung | Datum & Uhrzeit:", mood.get_json()["content"])
        self.assertIn("**Wert:** 7/10", mood.get_json()["content"])
        external = self.client.post("/api/write/template-preview", headers=self.headers, json={
            "template_id": "extern", "content": "Nicht anwenden",
        })
        self.assertEqual(external.status_code, 400)
        self.assertFalse(list((self.data_dir / "user-a").rglob("Journal_*.md")))

    def test_quick_submit_stays_journal_only_unless_background_is_explicit(self):
        tagging.update_ai_workflow("user-a", "save", "ai-pi", {
            "agent": "pi", "model": "provider/model", "prompt": "Plan", "context": "block", "target": "write_tab",
        })
        saved = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "Nur merken #ai-pi",
        })
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["ai_sessions"], [])
        self.assertFalse((self.data_dir / "user-a/ai-agent").exists())
        journal = next((self.data_dir / "user-a").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertIn("Nur merken #ai-pi", journal)
        self.assertNotIn("jt:agent-session-config", journal)

    def test_background_quick_submit_creates_identified_persistent_session_and_follow_up(self):
        tagging.update_ai_workflow("user-a", "save", "ai-pi", {
            "agent": "pi", "model": "provider/model", "prompt": "Plan", "context": "block", "target": "write_tab",
        })
        saved = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": "Plane den Tag #ai-pi", "background_agent": True,
        })
        self.assertEqual(saved.status_code, 200, saved.get_json())
        session = saved.get_json()["ai_sessions"][0]
        self.assertRegex(session["path"], r"^user-a/ai-agent/Agent-Session_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d+)?\.md$")
        job = json.loads((self.data_dir / "user-a/ai_jobs" / f"{session['job_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual((job["username"], job["user_id"], job["delivery"]), ("Alex", "user-a", "background"))
        source = (self.data_dir / session["path"]).read_text(encoding="utf-8")
        self.assertIn('username: "Alex"', source)
        self.assertIn('"user_id": "user-a"', source)
        self.assertIn("Plane den Tag", source)

        job["status"] = "completed"
        (self.data_dir / "user-a/ai_jobs" / f"{session['job_id']}.json").write_text(json.dumps(job), encoding="utf-8")

        follow_up = self.client.post(f"/api/ai-sessions/{session['session_id']}/reply", headers=self.headers, json={"text": "Und jetzt mit Pausen."})
        self.assertEqual(follow_up.status_code, 202, follow_up.get_json())
        follow_job = json.loads((self.data_dir / "user-a/ai_jobs" / f"{follow_up.get_json()['job_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(follow_job["session_id"], session["session_id"])
        self.assertEqual((follow_job["username"], follow_job["user_id"], follow_job["delivery"]), ("Alex", "user-a", "follow_up"))

    def test_background_submit_freezes_screenshot_for_the_host_worker(self):
        tagging.update_ai_workflow("user-a", "save", "ai-pi", {
            "agent": "pi", "model": "provider/model", "prompt": "Plan", "context": "block", "target": "write_tab",
        })
        text = "Analysiere diesen Screenshot #ai-pi"
        created = self.client.post(
            "/api/write-sessions", headers=self.headers,
            json={"content": text, "template_id": "schnell"},
        ).get_json()
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        self.assertEqual(self.client.post(
            f"/api/write-sessions/{created['id']}/images", headers=self.headers,
            data={"file": (io.BytesIO(png), "screen.png", "image/png")},
            content_type="multipart/form-data",
        ).status_code, 201)

        saved = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "schnell", "content": text, "background_agent": True,
            "write_session_id": created["id"],
        })

        self.assertEqual(saved.status_code, 200, saved.get_json())
        job_id = saved.get_json()["ai_sessions"][0]["job_id"]
        job = json.loads((self.data_dir / "user-a/ai_jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(len(job["attachment_images"]), 1)
        snapshot = self.data_dir / job["attachment_images"][0]["path"]
        self.assertTrue(snapshot.is_file())
        self.assertEqual(job["attachment_images"][0]["sha256"], __import__("hashlib").sha256(snapshot.read_bytes()).hexdigest())

    def test_hashtag_template_uses_quick_format_and_allows_explicit_background(self):
        config = json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))
        config["templates"].append({
            "id": "document-partner", "label": "Dokument-Partner", "type": "simple",
            "hashtag_template": True, "hashtags": ["#ai-pi", "#Dokument"],
        })
        main.CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")
        tagging.update_ai_workflow("user-a", "save", "ai-pi", {
            "agent": "pi", "model": "provider/model", "prompt": "Plan", "context": "block", "target": "write_tab",
        })

        saved = self.client.post("/api/submit", headers=self.headers, json={
            "template_id": "document-partner",
            "content": "#ai-pi #Dokument\n\nÜberarbeite diesen Text.",
            "background_agent": True,
        })

        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(len(saved.get_json()["ai_sessions"]), 1)
        journal = next((self.data_dir / "user-a").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertRegex(journal, r"- \d{2}\.\d{2}\. \d{2}:\d{2}:\d{2}\n#ai-pi #Dokument")
        self.assertNotIn("## Dokument-Partner", journal)

    def test_admin_audit_sees_requests_from_all_users_and_regular_user_is_denied(self):
        tagging.update_ai_workflow("user-a", "save", "ai-pi", {
            "agent": "pi", "model": "provider/model", "prompt": "Plan", "context": "block", "target": "write_tab",
        })
        queued = self.client.post("/api/write-ai/submit", headers=self.headers, json={
            "workflow_tag": "ai-pi", "provider_id": "__host_worker__", "model": "provider/model",
            "context_type": "draft", "text": "Auditierbarer Auftrag #ai-pi",
        })
        self.assertEqual(queued.status_code, 202)
        denied = self.client.get("/api/ai-sessions/audit", headers=self.headers)
        self.assertEqual(denied.status_code, 403)
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": "user-a", "username": "Alex", "password": "test", "admin": True}]}), encoding="utf-8")
        audit = self.client.get("/api/ai-sessions/audit", headers=self.headers)
        self.assertEqual(audit.status_code, 200)
        entry = next(item for item in audit.get_json()["entries"] if item["id"] == queued.get_json()["job_id"])
        self.assertEqual((entry["username"], entry["user_id"], entry["request"]), ("Alex", "user-a", "Auditierbarer Auftrag #ai-pi"))
        self.assertEqual(entry["agent_request"], "Auditierbarer Auftrag")

    def test_external_knowledge_folder_is_a_tool_reference_not_prompt_snapshot(self):
        (self.data_dir / "host_worker.json").write_text(json.dumps({
            "external_read_roots": [{"id": "docs", "path": "/srv/docs", "label": "Dokumente"}],
        }), encoding="utf-8")
        saved = self.client.post("/api/brain/tag-catalog", headers=self.headers, json={
            "scope": "knowledge", "action": "save", "tag": "architecture",
            "source": {"root_id": "docs", "path": "/srv/docs/team", "kind": "reference"},
        })
        self.assertEqual(saved.status_code, 200, saved.get_json())
        tagging.update_ai_workflow("user-a", "save", "ai-pi", {
            "agent": "pi", "model": "provider/model", "prompt": "Plan", "context": "block", "target": "write_tab",
        })
        queued = self.client.post("/api/write-ai/submit", headers=self.headers, json={
            "workflow_tag": "ai-pi", "provider_id": "__host_worker__", "model": "provider/model",
            "context_type": "draft", "text": "Suche die Regeln #ai-pi #architecture",
        })
        self.assertEqual(queued.status_code, 202, queued.get_json())
        job = json.loads((self.data_dir / "user-a/ai_jobs" / f"{queued.get_json()['job_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(job["knowledge_snapshot_paths"], [])
        self.assertEqual(job["knowledge_references"][0]["path"], "/srv/docs/team")
        self.assertEqual(job["user_request"], "Suche die Regeln")

    def test_completed_host_job_commits_delta_once_and_never_revives_consumed_draft(self):
        job_id = str(uuid.uuid4())
        jobs = self.data_dir / "user-a/ai_jobs"
        jobs.mkdir(parents=True)
        job_path = jobs / f"{job_id}.json"
        source_path = jobs / f"{job_id}.source.md"
        job_path.write_text(json.dumps({
            "id": job_id, "user_id": "user-a", "status": "completed",
            "submitted_text": "base", "section_path": f"user-a/ai_jobs/{job_id}.source.md",
        }), encoding="utf-8")
        source_path.write_text("<!-- jt:agent-session {} -->\nAI result\n\n___\n", encoding="utf-8")
        main.write_text_file(self.data_dir / "user-a/temp_Eingabe.md", "base\nnew text")

        committed = self.client.post(f"/api/write-ai/jobs/{job_id}/commit-draft", headers=self.headers, json={})
        self.assertEqual(committed.status_code, 200)
        self.assertEqual(committed.get_json()["content"], "AI result\n\nnew text")
        repeated = self.client.post(f"/api/write-ai/jobs/{job_id}/commit-draft", headers=self.headers, json={})
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.get_json()["already"])

        second_job_id = str(uuid.uuid4())
        (jobs / f"{second_job_id}.json").write_text(json.dumps({
            "id": second_job_id, "user_id": "user-a", "status": "completed",
            "submitted_text": "another base", "section_path": f"user-a/ai_jobs/{second_job_id}.source.md",
        }), encoding="utf-8")
        (jobs / f"{second_job_id}.source.md").write_text(
            "<!-- jt:agent-session {} -->\nLate result\n\n___\n", encoding="utf-8",
        )
        main.write_text_file(self.data_dir / "user-a/temp_Eingabe.md", "")
        obsolete = self.client.post(f"/api/write-ai/jobs/{second_job_id}/commit-draft", headers=self.headers, json={})
        self.assertEqual(obsolete.status_code, 409)
        self.assertTrue(obsolete.get_json()["obsolete"])
        self.assertEqual((self.data_dir / "user-a/temp_Eingabe.md").read_text(encoding="utf-8"), "")

    def test_write_ai_uses_the_hashtag_workflow_without_a_journal(self):
        with mock.patch.object(main, "_call_ai_api", return_value="AI result"):
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": "Bitte umschreiben #ai-template",
            })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(list((self.data_dir / "user-a").rglob("Journal_*.md")))

    def test_configured_knowledge_tag_is_snapshotted_only_for_explicit_submit(self):
        note = self.data_dir / "user-a/notes/shopping.md"
        note.parent.mkdir(parents=True)
        note.write_text("# Einkauf\n\nMilch und Brot\n", encoding="utf-8")
        saved = self.client.post("/api/brain/tag-catalog", headers=self.headers, json={
            "scope": "knowledge", "action": "save", "tag": "Einkaufsliste",
            "source": {"path": "notes/shopping.md", "family": False},
        })
        self.assertEqual(saved.status_code, 200)

        with mock.patch.object(main, "_call_ai_api", return_value="AI result") as call:
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": "Bitte planen #ai-template #Einkaufsliste #normaler-tag",
            })
        self.assertEqual(response.status_code, 200)
        prompt = call.call_args.args[2]
        self.assertIn("## Benutzerauftrag\n\nBitte planen #normaler-tag", prompt)
        self.assertIn("## Knowledge-Quellen (Referenzmaterial)", prompt)
        self.assertIn("#einkaufsliste · reference · personal:notes/shopping.md", prompt)
        self.assertIn("Milch und Brot", prompt)
        self.assertIn("#normaler-tag", prompt)
        self.assertEqual(note.read_text(encoding="utf-8"), "# Einkauf\n\nMilch und Brot\n")

    def test_pi_job_contains_private_knowledge_snapshot_and_rejects_missing_source(self):
        note = self.data_dir / "user-a/notes/shopping.md"
        note.parent.mkdir(parents=True)
        note.write_text("# Einkauf\n\nMilch\n", encoding="utf-8")
        tagging.update_knowledge_source("user-a", False, "save", "einkaufsliste", {"path": "notes/shopping.md"})
        tagging.update_ai_workflow("user-a", "save", "ai-pi", {
            "agent": "pi", "model": "provider/model", "prompt": "Plan", "context": "block", "target": "write_tab",
        })
        response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
            "workflow_tag": "ai-pi", "provider_id": "__host_worker__", "model": "provider/model",
            "context_type": "draft", "text": "Bitte planen #ai-pi #einkaufsliste",
        })
        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["job_id"]
        job = json.loads((self.data_dir / "user-a/ai_jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(job["knowledge_snapshots"], [{
            "tag": "einkaufsliste", "kind": "reference", "description": "", "scope": "personal", "path": "notes/shopping.md", "content": "# Einkauf\n\nMilch\n",
        }])
        snapshot = self.data_dir / job["knowledge_snapshot_paths"][0]
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "# Einkauf\n\nMilch\n")
        manifest = json.loads((self.data_dir / job["knowledge_manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest[0]["tag"], "einkaufsliste")
        self.assertEqual(job["user_request"], "Bitte planen")

        note.unlink()
        rejected = self.client.post("/api/write-ai/submit", headers=self.headers, json={
            "workflow_tag": "ai-pi", "provider_id": "__host_worker__", "model": "provider/model",
            "context_type": "draft", "text": "Nochmals #ai-pi #einkaufsliste",
        })
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("Knowledge source", rejected.get_json()["error"])

    def test_queued_pi_job_can_be_cancelled_and_undone_before_worker_start(self):
        tagging.update_ai_workflow("user-a", "save", "ai-pi", {
            "agent": "pi", "model": "provider/model", "prompt": "Plan", "context": "block", "target": "write_tab",
        })
        queued = self.client.post("/api/write-ai/submit", headers=self.headers, json={
            "workflow_tag": "ai-pi", "provider_id": "__host_worker__", "model": "provider/model",
            "context_type": "draft", "text": "Bitte planen #ai-pi",
        })
        self.assertEqual(queued.status_code, 202)
        job_id = queued.get_json()["job_id"]
        cancelled = self.client.post(f"/api/write-ai/jobs/{job_id}/cancel", headers=self.headers, json={})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json(), {"ok": True, "status": "cancelled", "can_undo": True})
        state = self.client.get(f"/api/write-ai/jobs/{job_id}", headers=self.headers).get_json()
        self.assertEqual(state["status"], "cancelled")
        self.assertTrue(state["can_undo"])
        restored = self.client.post(f"/api/write-ai/jobs/{job_id}/undo-cancel", headers=self.headers, json={})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.get_json(), {"ok": True, "status": "queued"})

    def test_youtube_mode_is_dev_only_presentation_state(self):
        with mock.patch.object(main, "IS_DEV", True):
            response = self.client.post("/api/settings/youtube-mode", headers=self.headers, json={"enabled": True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["enabled"])
            self.assertEqual(response.get_json()["environment"], "dev")
        with mock.patch.object(main, "IS_DEV", False):
            self.assertEqual(self.client.get("/api/settings/youtube-mode").status_code, 404)

    def test_write_hint_preference_is_persisted_per_user(self):
        self.assertTrue(self.client.get("/api/settings/profile").get_json()["show_write_hint"])
        response = self.client.post("/api/settings/write-hint", headers=self.headers, json={"enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "enabled": False})
        self.assertFalse(self.client.get("/api/settings/profile").get_json()["show_write_hint"])
        self.assertEqual(self.client.post("/api/settings/write-hint", headers=self.headers, json={"enabled": "no"}).status_code, 400)

    def test_hashtag_quick_button_preference_is_persisted_per_user(self):
        self.assertTrue(self.client.get("/api/settings/profile").get_json()["show_hashtag_quick_button"])
        response = self.client.post("/api/settings/hashtag-quick-button", headers=self.headers, json={"enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "enabled": False})
        self.assertFalse(self.client.get("/api/settings/profile").get_json()["show_hashtag_quick_button"])
        self.assertEqual(self.client.post("/api/settings/hashtag-quick-button", headers=self.headers, json={"enabled": "no"}).status_code, 400)

    def test_journal_calendar_returns_only_content_free_dates_for_requested_month(self):
        for relative in (
            "user-a/2026/08/03/Journal_2026-08-03.md",
            "user-a/2026/08/17/Journal_extra.md",
            "user-a/2026/09/01/Journal_2026-09-01.md",
            "user-a/2026/08/22/not-a-journal.md",
            "user-b/2026/08/05/Journal_2026-08-05.md",
        ):
            path = self.data_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("private content", encoding="utf-8")
        response = self.client.get("/api/journals/dates?month=2026-08")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"month": "2026-08", "dates": ["2026-08-03", "2026-08-17"]})
        self.assertNotIn("private content", response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/journals/dates?month=2026-13").status_code, 400)


if __name__ == "__main__":
    unittest.main()
