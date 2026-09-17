"""DB-native Share queue and direct journal delivery integration."""

import json
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ["DATABASE_URL"]
os.environ.update({
    "JOURNL_DATABASE_URL": DATABASE_URL, "JOURNL_DB_ENABLED": "true",
    "JOURNL_DB_AGENT_JOBS": "true", "SECRET_KEY": "share-api-db-test",
})
sys.path.insert(0, str(ROOT_DIR / "app"))
import brain  # noqa: E402
import main  # noqa: E402
import share_api  # noqa: E402
import tagging  # noqa: E402


class ShareApiDbTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(DATABASE_URL)
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", DATABASE_URL)
        command.upgrade(config, "head")
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.owner = "share-db-" + str(uuid.uuid4())
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
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": self.owner, "username": "Alex", "password": "x"}]}), encoding="utf-8")
        main.CONFIG_PATH.write_text("{}", encoding="utf-8")
        tagging.update_ai_workflow(self.owner, "save", "ai-share", {
            "agent": "pi", "model": "provider/model", "prompt": "Fasse knapp zusammen.",
            "context": "block", "target": "daily_journal", "provider_id": "__host_worker__",
        })

    def tearDown(self):
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM job_events WHERE job_id IN (SELECT id FROM background_jobs WHERE owner_id = :owner)"), {"owner": self.owner})
            connection.execute(text("DELETE FROM agent_responses WHERE session_id IN (SELECT id FROM agent_sessions WHERE owner_id = :owner)"), {"owner": self.owner})
            connection.execute(text("DELETE FROM background_jobs WHERE owner_id = :owner"), {"owner": self.owner})
            connection.execute(text("DELETE FROM agent_requests WHERE session_id IN (SELECT id FROM agent_sessions WHERE owner_id = :owner)"), {"owner": self.owner})
            connection.execute(text("DELETE FROM agent_sessions WHERE owner_id = :owner"), {"owner": self.owner})
        self.engine.dispose()
        (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            tagging.DATA_DIR, tagging.FAMILY_DIR, brain.DATA_DIR, brain.FAMILY_DIR,
        ) = self.old
        self.temp.cleanup()

    def test_share_context_round_trips_through_db_worker_and_journal(self):
        share_id = str(uuid.uuid4())
        directory = self.data / self.owner / ".share-jobs" / share_id
        directory.mkdir(parents=True)
        now = datetime(2026, 8, 15, 16, 20, tzinfo=timezone.utc)
        job = {
            "schema": 1, "id": share_id, "user_id": self.owner, "status": "preparing",
            "workflow_tag": "ai-share", "source_type": "text", "source_text": "DB Share Kontext",
            "source_url": None, "source_path": None, "original_filename": None, "mime_type": None,
            "byte_size": None, "sha256": None, "title": "DB-Wissen", "hashtags": ["wissen"],
            "extra_text": "Verdichte den Inhalt.", "entry_at": now.isoformat(),
            "created_at": now.isoformat(), "updated_at": now.isoformat(), "error": None,
        }
        share_api._write_json(directory / "metadata.json", job)
        with share_api.path_lock(directory / ".claim", exclusive=True):
            share_api._process_job(self.owner, directory, job)
        queued = share_api._read_json(directory / "metadata.json")
        first_agent_job_id = queued["agent_job_id"]
        queued.pop("agent_job_id")
        share_api._update_job(directory, queued, status="queueing")
        with share_api.path_lock(directory / ".claim", exclusive=True):
            share_api._process_job(self.owner, directory, share_api._read_json(directory / "metadata.json"))
        queued = share_api._read_json(directory / "metadata.json")
        self.assertEqual(queued["agent_job_id"], first_agent_job_id)
        claimed = main.background_jobs.claim_next_agent_job()
        self.assertEqual(str(claimed["job_id"]), queued["agent_job_id"])
        self.assertIn("DB Share Kontext", claimed["context_snapshot"])
        main.background_jobs.record_agent_response(queued["agent_job_id"], claimed["lease_token"], "DB Zusammenfassung")
        self.assertTrue(share_api.finalize_agent_job(queued["agent_job_id"]))
        completed = share_api._read_json(directory / "metadata.json")
        self.assertEqual(completed["status"], "completed")
        journal = next((self.data / self.owner).rglob("Journal_2026-08-15.md")).read_text(encoding="utf-8")
        self.assertIn("DB Zusammenfassung", journal)
        self.assertEqual(journal.count(f'"id":"{share_id}"'), 1)


if __name__ == "__main__":
    unittest.main()
