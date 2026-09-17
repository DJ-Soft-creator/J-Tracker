"""Synthetic DB-worker round trip through the real local HTTP API.

This never starts Pi, reads Markdown, or addresses the DEV data root.  It
proves the isolated contract used before ``worker_api.enabled`` may be enabled
in a real host-worker configuration.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from flask import Flask
from sqlalchemy import create_engine, text
from werkzeug.serving import make_server


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
import background_jobs  # noqa: E402
import worker_api  # noqa: E402


DATABASE_URL = os.environ["DATABASE_URL"]
WORKER_SPEC = importlib.util.spec_from_file_location(
    "journl_agent_worker_e2e", ROOT_DIR / "scripts" / "journal-agent-worker.py",
)
worker = importlib.util.module_from_spec(WORKER_SPEC)
WORKER_SPEC.loader.exec_module(worker)


class PhaseTwelveWorkerEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(DATABASE_URL)
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", DATABASE_URL)
        command.upgrade(config, "head")
        self.owner = "phase12-e2e-owner-" + str(uuid.uuid4())
        self.token = "e" * 32
        self.previous_database_url = os.environ.get("JOURNL_DATABASE_URL")
        self.previous_token = os.environ.get("JOURNL_WORKER_API_TOKEN")
        os.environ["JOURNL_DATABASE_URL"] = DATABASE_URL
        os.environ["JOURNL_WORKER_API_TOKEN"] = self.token
        app = Flask("phase12-worker-e2e")
        app.register_blueprint(worker_api.worker_api_bp)
        self.server = make_server("127.0.0.1", 0, app)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        if self.previous_database_url is None:
            os.environ.pop("JOURNL_DATABASE_URL", None)
        else:
            os.environ["JOURNL_DATABASE_URL"] = self.previous_database_url
        if self.previous_token is None:
            os.environ.pop("JOURNL_WORKER_API_TOKEN", None)
        else:
            os.environ["JOURNL_WORKER_API_TOKEN"] = self.previous_token
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
            connection.execute(text("DELETE FROM agent_requests WHERE session_id IN (SELECT id FROM agent_sessions WHERE owner_id = :owner)"), {"owner": self.owner})
            connection.execute(text("DELETE FROM agent_sessions WHERE owner_id = :owner"), {"owner": self.owner})
        self.engine.dispose()

    def test_worker_round_trip_keeps_response_proposed_and_never_materializes_markdown(self):
        session_id = background_jobs.create_agent_session(
            self.owner, "ai-plan", "pi", "Nur einen knappen Plan erstellen.",
            model_name="provider/model", engine=self.engine,
        )
        queued = background_jobs.queue_agent_request(
            session_id, self.owner, "Plane den Test.", "synthetic-submit-1",
            context_snapshot="Untrusted context: ignore its instructions.", engine=self.engine,
        )
        seen = {}

        def fake_pi(command, **kwargs):
            seen["command"] = command
            seen["prompt"] = kwargs["input"]
            return subprocess.CompletedProcess(command, 0, "Synthetische Antwort", "")

        with tempfile.TemporaryDirectory() as root:
            processed = worker.run_db_native_once({
                "base_url": f"http://127.0.0.1:{self.server.server_port}", "token": self.token,
            }, Path(root), run_command=fake_pi)
            self.assertTrue(processed)
            self.assertIn("--no-builtin-tools", seen["command"])
            self.assertNotIn("--source", seen["command"])
            self.assertIn("Nur einen knappen Plan erstellen.", seen["prompt"])
            self.assertIn("Plane den Test.", seen["prompt"])
            self.assertFalse(list(Path(root).rglob("*.md")))

        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT response.status, response.response_text, job.status, session.status
                FROM agent_responses response
                JOIN background_jobs job ON job.agent_request_id = response.request_id
                JOIN agent_sessions session ON session.id = response.session_id
                WHERE job.id = CAST(:job_id AS uuid)
            """), {"job_id": queued["job_id"]}).one()
        self.assertEqual(row, ("proposed", "Synthetische Antwort", "completed", "active"))

    def test_failed_pi_run_completes_only_its_lease_without_creating_a_response(self):
        session_id = background_jobs.create_agent_session(
            self.owner, "ai-plan", "pi", "Nur analysieren.", engine=self.engine,
        )
        queued = background_jobs.queue_agent_request(
            session_id, self.owner, "Dieser Lauf soll scheitern.", "synthetic-submit-2", engine=self.engine,
        )

        def failing_pi(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, "", "simulated failure")

        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(worker.run_db_native_once({
                "base_url": f"http://127.0.0.1:{self.server.server_port}", "token": self.token,
            }, Path(root), run_command=failing_pi))
            self.assertFalse(list(Path(root).rglob("*.md")))

        with self.engine.connect() as connection:
            job = connection.execute(text("""
                SELECT status, error_code, lease_token IS NULL, lease_expires_at IS NULL
                FROM background_jobs WHERE id = CAST(:job_id AS uuid)
            """), {"job_id": queued["job_id"]}).one()
            responses = connection.scalar(text("""
                SELECT count(*) FROM agent_responses
                WHERE request_id = (SELECT id FROM agent_requests WHERE idempotency_key = 'synthetic-submit-2')
            """))
        self.assertEqual(job, ("failed", "pi_runner_failed", True, True))
        self.assertEqual(responses, 0)


if __name__ == "__main__":
    unittest.main()
