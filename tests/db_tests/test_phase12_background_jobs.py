"""Phase-12B atomic background-job lease tests against disposable PostgreSQL."""

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
import background_jobs  # noqa: E402


DATABASE_URL = os.environ["DATABASE_URL"]


class BackgroundJobLeaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(DATABASE_URL)
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", DATABASE_URL)
        command.upgrade(config, "head")
        self.owner = "phase12-owner-" + str(uuid.uuid4())

    def tearDown(self):
        # Claims are intentionally global for a real worker.  Remove only this
        # test's synthetic owner graph so one test cannot feed the next one.
        with self.engine.begin() as connection:
            connection.execute(text("""
                DELETE FROM job_events
                WHERE job_id IN (SELECT id FROM background_jobs WHERE owner_id = :owner)
            """), {"owner": self.owner})
            connection.execute(text("""
                DELETE FROM agent_responses
                WHERE session_id IN (SELECT id FROM agent_sessions WHERE owner_id = :owner)
            """), {"owner": self.owner})
            connection.execute(text("DELETE FROM background_jobs WHERE owner_id = :owner"), {"owner": self.owner})
            connection.execute(text("""
                DELETE FROM agent_permissions
                WHERE session_id IN (SELECT id FROM agent_sessions WHERE owner_id = :owner)
            """), {"owner": self.owner})
            connection.execute(text("""
                DELETE FROM agent_requests
                WHERE session_id IN (SELECT id FROM agent_sessions WHERE owner_id = :owner)
            """), {"owner": self.owner})
            connection.execute(text("DELETE FROM agent_sessions WHERE owner_id = :owner"), {"owner": self.owner})
        self.engine.dispose()

    def _job(self, *, status="queued", lease_token=None, lease_expires_at=None):
        job_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO background_jobs (
                    id, owner_id, job_kind, status, lease_token, lease_expires_at,
                    attempt_count, max_attempts, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), :owner, 'agent_request', :status,
                    CAST(:lease_token AS uuid), :lease_expires_at, 0, 2, now(), now()
                )
            """), {"id": job_id, "owner": self.owner, "status": status,
                   "lease_token": lease_token, "lease_expires_at": lease_expires_at})
        return job_id

    def test_claim_is_single_use_and_a_stale_token_cannot_finish(self):
        job_id = self._job()
        claimed = background_jobs.claim_next_job(self.owner, "agent_request", engine=self.engine)
        self.assertEqual(str(claimed["id"]), job_id)
        self.assertIsNone(background_jobs.claim_next_job(self.owner, "agent_request", engine=self.engine))
        self.assertFalse(background_jobs.finish_job(job_id, uuid.uuid4(), "completed", engine=self.engine))
        self.assertTrue(background_jobs.finish_job(job_id, claimed["lease_token"], "completed", engine=self.engine))
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT status FROM background_jobs WHERE id = CAST(:id AS uuid)"),
                                               {"id": job_id}), "completed")
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM job_events WHERE job_id = CAST(:id AS uuid)"),
                                               {"id": job_id}), 2)

    def test_expired_lease_returns_to_queue_once(self):
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        job_id = self._job(status="leased", lease_token=str(uuid.uuid4()), lease_expires_at=expired)
        self.assertEqual(background_jobs.recover_expired_leases(engine=self.engine), 1)
        self.assertEqual(background_jobs.recover_expired_leases(engine=self.engine), 0)
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT status, lease_token IS NULL, lease_expires_at IS NULL, error_code
                FROM background_jobs WHERE id = CAST(:id AS uuid)
            """), {"id": job_id}).one()
        self.assertEqual(row, ("queued", True, True, "lease_expired"))

    def test_request_queue_is_idempotent_and_allows_only_one_open_request(self):
        session_id = background_jobs.create_agent_session(
            self.owner, "ai-plan", "pi", "Nur einen Plan erstellen.", model_name="provider/model", engine=self.engine,
        )
        first = background_jobs.queue_agent_request(
            session_id, self.owner, "Erstelle einen Plan", "browser-submit-1",
            context_snapshot="untrusted reference", max_attempts=2, engine=self.engine,
        )
        duplicate = background_jobs.queue_agent_request(
            session_id, self.owner, "Erstelle einen Plan", "browser-submit-1", engine=self.engine,
        )
        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["request_id"], first["request_id"])
        self.assertEqual(duplicate["job_id"], first["job_id"])
        with self.assertRaisesRegex(ValueError, "keine neue Anfrage"):
            background_jobs.queue_agent_request(
                session_id, self.owner, "Zweiter Auftrag", "browser-submit-2", engine=self.engine,
            )
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM agent_requests WHERE session_id = CAST(:id AS uuid)"),
                                               {"id": session_id}), 1)
            self.assertEqual(connection.scalar(text("SELECT status FROM agent_sessions WHERE id = CAST(:id AS uuid)"),
                                               {"id": session_id}), "waiting")

    def test_worker_response_is_proposed_once_and_never_applied_to_a_document(self):
        session_id = background_jobs.create_agent_session(self.owner, "ai-plan", "pi", "Nur analysieren.", engine=self.engine)
        queued = background_jobs.queue_agent_request(
            session_id, self.owner, "Bitte analysieren", "browser-submit-1", engine=self.engine,
        )
        claimed = background_jobs.claim_next_agent_job(engine=self.engine)
        self.assertEqual(str(claimed["job_id"]), queued["job_id"])
        self.assertEqual(claimed["workflow_prompt"], "Nur analysieren.")
        result = background_jobs.record_agent_response(
            queued["job_id"], claimed["lease_token"], "Vorgeschlagene Antwort", engine=self.engine,
        )
        self.assertEqual(result["status"], "proposed")
        self.assertIsNone(background_jobs.record_agent_response(
            queued["job_id"], claimed["lease_token"], "Doppelte Antwort", engine=self.engine,
        ))
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT response.status, response.response_text, job.status, session.status
                FROM agent_responses response
                JOIN background_jobs job ON job.agent_request_id = response.request_id
                JOIN agent_sessions session ON session.id = response.session_id
                WHERE response.id = CAST(:id AS uuid)
            """), {"id": result["response_id"]}).one()
        self.assertEqual(row, ("proposed", "Vorgeschlagene Antwort", "completed", "active"))

    def test_agent_worker_claim_ignores_unlinked_technical_job(self):
        self._job()
        session_id = background_jobs.create_agent_session(self.owner, "ai-plan", "pi", "Nur analysieren.", engine=self.engine)
        queued = background_jobs.queue_agent_request(
            session_id, self.owner, "Bitte analysieren", "browser-submit-1", engine=self.engine,
        )
        claimed = background_jobs.claim_next_agent_job(engine=self.engine)
        self.assertEqual(str(claimed["job_id"]), queued["job_id"])
