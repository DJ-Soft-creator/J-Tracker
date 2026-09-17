"""Phase-6 immutable conflict persistence against disposable PostgreSQL."""

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
from conflicts import record_conflict, resolve_conflict  # noqa: E402


class ConflictPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["DATABASE_URL"])
        config = Config(str(ROOT / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
        command.upgrade(config, "head")
        self.document_id, self.revision_id = uuid.uuid4(), uuid.uuid4()
        self.now = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO documents (id, owner_id, scope, document_type, storage_root_key, relative_path,
                    storage_mode, state, materialized_file_hash, created_at, updated_at)
                VALUES (:id, 'phase6', 'personal', 'journal', 'phase6-root', :path,
                    'db_managed', 'active', :hash, :now, :now)
            """), {"id": self.document_id, "path": str(self.document_id) + ".md", "hash": "0" * 64, "now": self.now})
            connection.execute(text("""
                INSERT INTO document_revisions (id, document_id, origin, reason, file_hash, created_at, summary)
                VALUES (:id, :document, 'external', 'external_import', :hash, :now, '')
            """), {"id": self.revision_id, "document": self.document_id, "hash": "0" * 64, "now": self.now})
            connection.execute(text("UPDATE documents SET current_revision_id = :revision WHERE id = :id"),
                               {"revision": self.revision_id, "id": self.document_id})

    def tearDown(self):
        self.engine.dispose()

    def test_conflict_preserves_all_sides_and_blocks_normal_document_writes(self):
        with self.engine.begin() as connection:
            conflict_id = record_conflict(connection, document_id=self.document_id,
                                          base_revision_id=self.revision_id, base_content="Basis",
                                          external_content="Extern", jtracker_content="J-Tracker", now=self.now)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT state FROM documents WHERE id = :id"),
                                               {"id": self.document_id}), "conflict")
            versions = connection.execute(text("""
                SELECT kind, markdown_content FROM sync_conflict_versions
                WHERE conflict_id = :id ORDER BY kind
            """), {"id": conflict_id}).all()
        self.assertEqual(versions, [("base", "Basis"), ("external", "Extern"), ("jtracker", "J-Tracker")])

    def test_repeated_conflict_for_same_document_reuses_the_open_record(self):
        with self.engine.begin() as connection:
            first = record_conflict(connection, document_id=self.document_id,
                                    base_revision_id=self.revision_id, base_content="Basis",
                                    external_content="Extern", jtracker_content="J-Tracker", now=self.now)
            second = record_conflict(connection, document_id=self.document_id,
                                     base_revision_id=self.revision_id, base_content="Basis 2",
                                     external_content="Extern 2", jtracker_content="J-Tracker 2", now=self.now)
        self.assertEqual(first, second)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM sync_conflicts WHERE document_id = :id"),
                                               {"id": self.document_id}), 1)

    def test_all_resolution_choices_are_idempotent_and_keep_document_blocked(self):
        for choice, expected, merged in (("external", "Extern", None), ("jtracker", "J-Tracker", None),
                                         ("both", "Extern\n\nJ-Tracker", "Extern\n\nJ-Tracker")):
            document_id = uuid.uuid4()
            revision_id = uuid.uuid4()
            with self.engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO documents (id, owner_id, scope, document_type, storage_root_key, relative_path,
                        storage_mode, state, materialized_file_hash, created_at, updated_at)
                    VALUES (:id, 'phase6', 'personal', 'journal', 'phase6-root', :path,
                        'db_managed', 'active', :hash, :now, :now)
                """), {"id": document_id, "path": str(document_id) + ".md", "hash": "1" * 64, "now": self.now})
                connection.execute(text("""
                    INSERT INTO document_revisions (id, document_id, origin, reason, file_hash, created_at, summary)
                    VALUES (:id, :document, 'external', 'external_import', :hash, :now, '')
                """), {"id": revision_id, "document": document_id, "hash": "1" * 64, "now": self.now})
                connection.execute(text("UPDATE documents SET current_revision_id = :revision WHERE id = :id"),
                                   {"revision": revision_id, "id": document_id})
                conflict_id = record_conflict(connection, document_id=document_id, base_revision_id=revision_id,
                                              base_content="Basis", external_content="Extern",
                                              jtracker_content="J-Tracker", now=self.now)
                self.assertEqual(resolve_conflict(connection, conflict_id=conflict_id, choice=choice,
                                                  merged_content=merged, now=self.now), expected)
            with self.engine.connect() as connection:
                self.assertEqual(connection.scalar(text("SELECT state FROM documents WHERE id = :id"),
                                                   {"id": document_id}), "conflict")
                self.assertEqual(connection.scalar(text("SELECT resolution FROM sync_conflicts WHERE id = :id"),
                                                   {"id": conflict_id}), choice)


if __name__ == "__main__":
    unittest.main()
