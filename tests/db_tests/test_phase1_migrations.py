"""Phase-1 migration and health tests against the disposable PostgreSQL service."""

import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
import db  # noqa: E402


DATABASE_URL = os.environ["DATABASE_URL"]


class PhaseOneMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(DATABASE_URL)

    def tearDown(self):
        self.engine.dispose()

    def _config(self):
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", DATABASE_URL)
        return config

    def test_upgrade_is_repeatable_and_uses_its_own_version_table(self):
        command.upgrade(self._config(), "head")
        document_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO documents (
                    id, owner_id, scope, document_type, storage_root_key, relative_path,
                    storage_mode, state, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), 'phase1-test', 'personal', 'journal', 'test-root', :path,
                    'legacy_file', 'active', now(), now()
                )
            """), {"id": document_id, "path": "phase1/%s.md" % document_id})
        command.upgrade(self._config(), "head")
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT version_num FROM journl_alembic_version")), "0015_analysis_last_days")
            self.assertTrue(connection.scalar(text("SELECT to_regclass('public.documents') IS NOT NULL")))
            self.assertTrue(connection.scalar(text("SELECT to_regclass('public.document_media_references') IS NOT NULL")))
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM documents WHERE id = CAST(:id AS uuid)"), {"id": document_id}), 1)

    def test_schema_constraints_reject_invalid_data_and_rollback_the_transaction(self):
        command.upgrade(self._config(), "head")
        document_id = str(uuid.uuid4())
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO documents (
                        id, owner_id, scope, document_type, storage_root_key, relative_path,
                        storage_mode, state, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS uuid), 'phase1-test', 'personal', 'journal', 'test-root', '../unsafe.md',
                        'legacy_file', 'invalid-state', now(), now()
                    )
                """), {"id": document_id})
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM documents WHERE id = CAST(:id AS uuid)"), {"id": document_id}), 0)

    def test_two_connections_can_operate_independently(self):
        command.upgrade(self._config(), "head")
        first = self.engine.connect()
        second = self.engine.connect()
        try:
            self.assertEqual(first.scalar(text("SELECT 1")), 1)
            self.assertEqual(second.scalar(text("SELECT 1")), 1)
        finally:
            second.close()
            first.close()

    def test_disabled_health_does_not_connect_to_database(self):
        with mock.patch.dict(os.environ, {"JOURNL_DB_ENABLED": "false"}, clear=False):
            self.assertEqual(db.database_health()["status"], "disabled")

    def test_enabled_health_reports_schema_compatibility_without_exposing_url(self):
        command.upgrade(self._config(), "head")
        with mock.patch.dict(os.environ, {"JOURNL_DB_ENABLED": "true", "JOURNL_DATABASE_URL": DATABASE_URL}, clear=False):
            health = db.database_health()
        self.assertEqual(health, {
            "enabled": True, "status": "healthy", "schema_revision": "0015_analysis_last_days", "healthy": True,
        })

    def test_enabled_health_reports_unavailable_database_without_exposing_connection_data(self):
        unavailable_url = "postgresql+psycopg://spike@not-a-database:5432/spike"
        with mock.patch.dict(os.environ, {
            "JOURNL_DB_ENABLED": "true", "JOURNL_DATABASE_URL": unavailable_url,
        }, clear=False):
            health = db.database_health()
        self.assertEqual(health, {
            "enabled": True, "status": "unavailable", "schema_revision": None, "healthy": False,
        })
        self.assertNotIn("not-a-database", repr(health))

    def test_agent_job_schema_requires_a_complete_lease(self):
        command.upgrade(self._config(), "head")
        job_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO background_jobs (
                    id, owner_id, job_kind, status, attempt_count, max_attempts, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), 'phase12-test', 'maintenance', 'queued', 0, 1, now(), now()
                )
            """), {"id": job_id})
            self.assertEqual(connection.scalar(text("SELECT status FROM background_jobs WHERE id = CAST(:id AS uuid)"),
                                               {"id": job_id}), "queued")
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO background_jobs (
                        id, owner_id, job_kind, status, lease_token, attempt_count, max_attempts, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS uuid), 'phase12-test', 'maintenance', 'queued', CAST(:lease AS uuid), 0, 1, now(), now()
                    )
                """), {"id": str(uuid.uuid4()), "lease": str(uuid.uuid4())})


if __name__ == "__main__":
    unittest.main()
