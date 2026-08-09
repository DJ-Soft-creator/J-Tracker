import importlib.util
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
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR)
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.data_dir, self.data_dir / "users.json", self.data_dir / "config.json"
        tagging.DATA_DIR, tagging.FAMILY_DIR = self.data_dir, self.data_dir / "family"
        brain.DATA_DIR, brain.FAMILY_DIR = self.data_dir, self.data_dir / "family"
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
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR = self.old
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


if __name__ == "__main__":
    unittest.main()
