"""Phase-4 revision, checkpoint, restore, and scheduler integration tests."""

import os
import sys
import hashlib
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
from revisions import (  # noqa: E402
    CHECKPOINT_INTERVAL,
    observe_external,
    pre_write_checkpoint,
    promote_checkpoint,
    promote_due_checkpoints,
    restore_revision,
)


class PhaseFourRevisionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["DATABASE_URL"])
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
        command.upgrade(config, "head")
        self.document_id = uuid.uuid4()
        self.base_revision_id = uuid.uuid4()
        self.now = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
        self.base_content = "# Journal\n\nAusgangsstand\n"
        self.base_hash = __import__("hashlib").sha256(self.base_content.encode()).hexdigest()
        self.media_temp = tempfile.TemporaryDirectory()
        self.media_root = Path(self.media_temp.name)
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO documents (id, owner_id, scope, document_type, storage_root_key, relative_path,
                    storage_mode, state, materialized_file_hash, created_at, updated_at)
                VALUES (:id, 'phase4-user', 'personal', 'journal', 'phase4-root', :path,
                    'shadow_indexed', 'active', :hash, :now, :now)
            """), {"id": self.document_id, "path": "2026/08/11/Journal_" + str(self.document_id) + ".md",
                  "hash": self.base_hash, "now": self.now})
            connection.execute(text("""
                INSERT INTO document_revisions (id, document_id, origin, reason, file_hash, semantic_hash, created_at, summary)
                VALUES (:id, :document, 'external', 'external_import', :hash, :hash, :now, 'Phase-4 Testbasis')
            """), {"id": self.base_revision_id, "document": self.document_id, "hash": self.base_hash, "now": self.now})
            connection.execute(text("""
                INSERT INTO document_snapshots (document_revision_id, markdown_content, file_hash, created_at)
                VALUES (:id, :content, :hash, :now)
            """), {"id": self.base_revision_id, "content": self.base_content, "hash": self.base_hash, "now": self.now})
            connection.execute(text("UPDATE documents SET current_revision_id = :revision WHERE id = :id"),
                               {"revision": self.base_revision_id, "id": self.document_id})

    def tearDown(self):
        self.engine.dispose()
        self.media_temp.cleanup()

    def test_autosave_storm_creates_one_checkpoint_with_latest_content(self):
        with self.engine.begin() as connection:
            for minute in range(15):
                observed = observe_external(connection, document_id=self.document_id,
                                            content="# Journal\n\nAutosave " + str(minute) + "\n",
                                            now=self.now + timedelta(minutes=minute))
            self.assertEqual(observed["checkpoint_due_at"], self.now + CHECKPOINT_INTERVAL)
            self.assertIsNone(promote_checkpoint(connection, document_id=self.document_id,
                                                  now=self.now + timedelta(minutes=14)))
            revision_id = promote_checkpoint(connection, document_id=self.document_id,
                                              now=self.now + CHECKPOINT_INTERVAL)
            self.assertIsNotNone(revision_id)
            self.assertIsNone(promote_checkpoint(connection, document_id=self.document_id,
                                                  now=self.now + timedelta(minutes=16)))
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM document_working_states WHERE document_id = :id"),
                                               {"id": self.document_id}), 0)
            self.assertEqual(connection.scalar(text("""
                SELECT block_revision.markdown_content
                FROM block_revisions block_revision
                JOIN blocks block ON block.id = block_revision.block_id
                WHERE block.document_id = :id
                ORDER BY block_revision.created_at DESC LIMIT 1
            """), {"id": self.document_id}), "# Journal\n\nAutosave 14\n")
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM document_revisions WHERE document_id = :id"),
                                               {"id": self.document_id}), 2)

    def test_pre_write_checkpoint_and_restore_are_durable_without_materializing_file(self):
        changed = "# Journal\n\nExterner Stand\n"
        with self.engine.begin() as connection:
            observe_external(connection, document_id=self.document_id, content=changed,
                             now=self.now + timedelta(minutes=2))
            checkpoint = pre_write_checkpoint(connection, document_id=self.document_id,
                                               now=self.now + timedelta(minutes=2))
            restored = restore_revision(connection, document_id=self.document_id,
                                        source_revision_id=self.base_revision_id,
                                        now=self.now + timedelta(minutes=3))
        with self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id, origin, reason FROM document_revisions
                WHERE document_id = :id ORDER BY created_at
            """), {"id": self.document_id}).mappings().all()
            self.assertEqual([row["id"] for row in rows], [self.base_revision_id, checkpoint, restored])
            self.assertEqual(rows[-1]["origin"], "restore")
            self.assertEqual(connection.scalar(text("""
                SELECT markdown_content FROM document_snapshots WHERE document_revision_id = :id
            """), {"id": restored}), self.base_content)

    def test_external_checkpoint_references_only_hash_verified_media_for_managed_document(self):
        media_id = uuid.uuid4()
        relative_path = "2026/08/11/media/external.jpg"
        media = self.media_root / relative_path
        media.parent.mkdir(parents=True)
        media.write_bytes(b"external checkpoint photo")
        digest = hashlib.sha256(media.read_bytes()).hexdigest()
        content = (
            "# Journal\n\n<!-- jt:media {\"id\":\"%s\",\"type\":\"image\",\"mime_type\":\"image/jpeg\","
            "\"media_path\":\"%s\",\"sha256\":\"%s\"} -->\n" % (media_id, relative_path, digest)
        )
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE documents SET storage_mode = 'db_managed' WHERE id = :id"),
                               {"id": self.document_id})
            observe_external(connection, document_id=self.document_id, content=content, now=self.now)
            revision = promote_checkpoint(connection, document_id=self.document_id,
                                          now=self.now + CHECKPOINT_INTERVAL, media_root=self.media_root)
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT media_id, file_hash FROM document_media_references WHERE document_revision_id = :revision
            """), {"revision": revision}).mappings().one()
        self.assertEqual(str(row["media_id"]), str(media_id))
        self.assertEqual(row["file_hash"], digest)

    def test_snapshot_after_twenty_deltas_and_due_scheduler_is_idempotent(self):
        with self.engine.begin() as connection:
            for index in range(20):
                observed_at = self.now + timedelta(minutes=index * 16)
                observe_external(connection, document_id=self.document_id,
                                 content="# Journal\n\nDelta " + str(index) + "\n", now=observed_at)
                promoted = promote_due_checkpoints(connection, now=observed_at + CHECKPOINT_INTERVAL)
                self.assertEqual(len(promoted), 1)
            self.assertEqual(promote_due_checkpoints(connection, now=self.now + timedelta(hours=6)), [])
        with self.engine.connect() as connection:
            snapshots = connection.scalar(text("""
                SELECT count(*) FROM document_snapshots snapshot
                JOIN document_revisions revision ON revision.id = snapshot.document_revision_id
                WHERE revision.document_id = :id
            """), {"id": self.document_id})
            self.assertEqual(snapshots, 2)

    def test_second_scheduler_skips_document_locked_by_first_scheduler(self):
        due_at = self.now + CHECKPOINT_INTERVAL
        with self.engine.begin() as connection:
            observe_external(connection, document_id=self.document_id, content="# Journal\n\nParallel\n", now=self.now)

        first_connection = self.engine.connect()
        first_transaction = first_connection.begin()
        try:
            first_connection.execute(text("""
                SELECT document_id FROM document_working_states
                WHERE document_id = :id FOR UPDATE
            """), {"id": self.document_id})
            with self.engine.begin() as second_connection:
                self.assertEqual(promote_due_checkpoints(second_connection, now=due_at), [])
        finally:
            first_transaction.commit()
            first_connection.close()

        with self.engine.begin() as connection:
            promoted = promote_due_checkpoints(connection, now=due_at)
        self.assertEqual(len(promoted), 1)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("""
                SELECT count(*) FROM document_revisions WHERE document_id = :id
            """), {"id": self.document_id}), 2)


if __name__ == "__main__":
    unittest.main()
