"""Validation for the Phase-0E DDL prototype against disposable PostgreSQL."""

import os
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text


DATABASE_URL = os.environ["DATABASE_URL"]
ROOT_DIR = Path(__file__).resolve().parents[2]
DDL_PATH = ROOT_DIR / "app" / "migrations" / "schema.sql"


class SchemaPrototypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(DATABASE_URL)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_prototype_creates_core_schema_with_restrictive_foreign_keys(self):
        ddl = "\n".join(
            line for line in DDL_PATH.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("--")
        )
        statements = [statement.strip() for statement in ddl.split(";") if statement.strip()]
        with self.engine.begin() as connection:
            exists = connection.scalar(text("SELECT to_regclass('public.documents') IS NOT NULL"))
            if not exists:
                for statement in statements:
                    connection.execute(text(statement))
            tables = set(connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))
            delete_rules = connection.execute(text("""
                SELECT rc.delete_rule
                FROM information_schema.referential_constraints rc
                WHERE rc.constraint_schema = 'public'
            """)).scalars().all()
            indexes = set(connection.scalars(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")))

        self.assertTrue({
            "documents", "document_revisions", "blocks", "block_revisions",
            "document_revision_blocks", "document_snapshots", "document_working_states",
            "sync_observations", "sync_conflicts", "sync_conflict_versions",
            "pending_file_operations",
        }.issubset(tables))
        self.assertNotIn("CASCADE", delete_rules)
        self.assertIn("uq_documents_active_path", indexes)
        self.assertIn("ix_pending_file_operations_open", indexes)


if __name__ == "__main__":
    unittest.main()
