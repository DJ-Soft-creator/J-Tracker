"""DB-native browser submission contract without a real Pi process or user data."""

import base64
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ["DATABASE_URL"]
os.environ.update({"JOURNL_DATABASE_URL": DATABASE_URL, "JOURNL_DB_ENABLED": "true", "JOURNL_DB_AGENT_JOBS": "true",
                   "SECRET_KEY": "phase12-write-ai-db-test"})
sys.path.insert(0, str(ROOT_DIR / "app"))
import main  # noqa: E402
import brain  # noqa: E402
import tagging  # noqa: E402


class PhaseTwelveWriteAiDbTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(DATABASE_URL)
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", DATABASE_URL)
        command.upgrade(config, "head")
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR)
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.data, self.data / "users.json", self.data / "config.json"
        tagging.DATA_DIR, tagging.FAMILY_DIR = self.data, self.data / "family"
        brain.DATA_DIR, brain.FAMILY_DIR = self.data, self.data / "family"
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": "db-e2e-owner", "username": "Alex", "password": "test", "admin": True}]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": [{"id": "schnell", "label": "Schnell", "type": "simple"}]}), encoding="utf-8")
        tagging.update_ai_workflow("db-e2e-owner", "save", "ai-pi", {"agent": "pi", "model": "provider/model", "prompt": "Antworte knapp.", "context": "block", "target": "write_tab"})
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "db-e2e-owner"
            session["csrf_token"] = "csrf-test"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR = self.old
        self.temp.cleanup()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM job_events")
            connection.exec_driver_sql("DELETE FROM agent_responses")
            connection.exec_driver_sql("DELETE FROM background_jobs")
            connection.exec_driver_sql("DELETE FROM agent_requests")
            connection.exec_driver_sql("DELETE FROM agent_sessions")
        self.engine.dispose()

    def test_db_job_response_is_inserted_into_the_submitted_draft(self):
        headers = {"X-CSRF-Token": "csrf-test"}
        submitted = self.client.post("/api/write-ai/submit", headers=headers, json={"workflow_tag": "ai-pi", "provider_id": "__host_worker__", "context_type": "draft", "text": "Bitte planen #ai-pi"})
        self.assertEqual(submitted.status_code, 202, submitted.get_json())
        job_id = submitted.get_json()["job_id"]
        claimed = main.background_jobs.claim_next_agent_job()
        main.background_jobs.record_agent_response(job_id, claimed["lease_token"], "Direkte DB-Antwort")
        status = self.client.get(f"/api/write-ai/jobs/{job_id}")
        self.assertEqual(status.get_json()["status"], "completed")
        committed = self.client.post(f"/api/write-ai/jobs/{job_id}/commit-draft", headers=headers, json={})
        self.assertEqual(committed.status_code, 200, committed.get_json())
        self.assertEqual(committed.get_json()["content"], "Direkte DB-Antwort")
        repeated = self.client.post(f"/api/write-ai/jobs/{job_id}/commit-draft", headers=headers, json={})
        self.assertEqual(repeated.status_code, 200, repeated.get_json())
        self.assertTrue(repeated.get_json()["already"])

    def test_db_image_capability_failure_is_immediately_visible_to_the_browser(self):
        headers = {"X-CSRF-Token": "csrf-test"}
        submitted = self.client.post("/api/write-ai/submit", headers=headers, json={
            "workflow_tag": "ai-pi", "provider_id": "__host_worker__",
            "context_type": "draft", "text": "Beschreibe das Bild #ai-pi",
        })
        self.assertEqual(submitted.status_code, 202, submitted.get_json())
        job_id = submitted.get_json()["job_id"]
        claimed = main.background_jobs.claim_next_agent_job()
        self.assertTrue(main.background_jobs.finish_job(
            job_id, claimed["lease_token"], "failed", error_code="model_no_image_support",
        ))

        status = self.client.get(f"/api/write-ai/jobs/{job_id}")

        self.assertEqual(status.status_code, 200, status.get_json())
        self.assertEqual(status.get_json()["status"], "error")
        self.assertIn("unterstützt keine Bildanhänge", status.get_json()["error"])

    def test_db_template_ai_claim_contains_immutable_screenshot(self):
        headers = {"X-CSRF-Token": "csrf-test"}
        text_value = "Beschreibe das Bild #ai-pi"
        session = self.client.post("/api/write-sessions", headers=headers, json={
            "content": text_value, "template_id": "schnell",
        }).get_json()
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        uploaded = self.client.post(
            f"/api/write-sessions/{session['id']}/images", headers=headers,
            data={"file": (io.BytesIO(png), "screen.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.get_json())

        submitted = self.client.post("/api/write-ai/submit", headers=headers, json={
            "workflow_tag": "ai-pi", "provider_id": "__host_worker__",
            "context_type": "draft", "text": text_value, "write_session_id": session["id"],
        })

        self.assertEqual(submitted.status_code, 202, submitted.get_json())
        claimed = main.background_jobs.claim_next_agent_job()
        self.assertEqual(str(claimed["job_id"]), submitted.get_json()["job_id"])
        self.assertEqual(len(claimed["attachment_images"]), 1)
        image = claimed["attachment_images"][0]
        self.assertEqual(image["mime_type"], "image/png")
        self.assertTrue((self.data / image["path"]).is_file())

    def test_explicit_background_submission_queues_only_a_db_job(self):
        headers = {"X-CSRF-Token": "csrf-test"}
        submitted = self.client.post("/api/submit", headers=headers, json={
            "template_id": "schnell", "content": "Plane morgen #ai-pi", "background_agent": True,
        })
        self.assertEqual(submitted.status_code, 200, submitted.get_json())
        session = submitted.get_json()["ai_sessions"][0]
        self.assertTrue(session["db_native"])
        self.assertFalse((self.data / "db-e2e-owner" / "ai_jobs").exists())
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT job.status, request.request_text
                FROM background_jobs job JOIN agent_requests request ON request.id = job.agent_request_id
                WHERE job.id = CAST(:job_id AS uuid)
            """), {"job_id": session["job_id"]}).one()
        self.assertEqual(row, ("queued", "Plane morgen"))
        journal = next((self.data / "db-e2e-owner").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertNotIn("jt:agent-session", journal)

        claimed = main.background_jobs.claim_next_agent_job()
        main.background_jobs.record_agent_response(
            session["job_id"], claimed["lease_token"], "Der sichtbare Hintergrundbericht."
        )
        brain_sessions = self.client.get("/api/brain/ai-agent")
        self.assertEqual(brain_sessions.status_code, 200, brain_sessions.get_json())
        db_session = next(
            item for item in brain_sessions.get_json()["sessions"]
            if item["ai_session"].get("db_native")
        )
        self.assertEqual(db_session["ai_session"]["session_id"], session["session_id"])
        self.assertEqual(
            [event["type"] for event in db_session["ai_session"]["events"]],
            ["agent-answer", "user-request"],
        )

        audit = self.client.get("/api/ai-sessions/audit")
        self.assertEqual(audit.status_code, 200, audit.get_json())
        db_audit = next(item for item in audit.get_json()["entries"] if item.get("db_native"))
        self.assertEqual(db_audit["id"], session["job_id"])
        self.assertEqual(db_audit["delivery"], "background-db")

    def test_hashtag_template_can_queue_a_db_background_job(self):
        config = json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))
        config["templates"].append({
            "id": "backend-agent", "label": "Backend-Agent", "type": "simple",
            "hashtag_template": True, "hashtags": ["#ai-pi"],
        })
        main.CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")

        submitted = self.client.post("/api/submit", headers={"X-CSRF-Token": "csrf-test"}, json={
            "template_id": "backend-agent",
            "content": "#ai-pi\n\nPrüfe diesen Auftrag.",
            "background_agent": True,
        })

        self.assertEqual(submitted.status_code, 200, submitted.get_json())
        session = submitted.get_json()["ai_sessions"][0]
        self.assertTrue(session["db_native"])
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT job.status, request.request_text
                FROM background_jobs job JOIN agent_requests request ON request.id = job.agent_request_id
                WHERE job.id = CAST(:job_id AS uuid)
            """), {"job_id": session["job_id"]}).one()
        self.assertEqual(row, ("queued", "Prüfe diesen Auftrag."))
        journal = next((self.data / "db-e2e-owner").rglob("Journal_*.md")).read_text(encoding="utf-8")
        self.assertRegex(journal, r"- \d{2}\.\d{2}\. \d{2}:\d{2}:\d{2}\n#ai-pi")


if __name__ == "__main__":
    unittest.main()
