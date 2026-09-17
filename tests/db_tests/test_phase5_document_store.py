"""Phase-5 recoverable document-store integration tests using only temp files."""

import hashlib
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
from document_store import DocumentConflict, DocumentStore, DocumentStoreError, runtime_lock_root  # noqa: E402
from revisions import prepare_write_revision  # noqa: E402


class PhaseFiveDocumentStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["DATABASE_URL"])
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
        command.upgrade(config, "head")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir()
        self.lock_root = Path(self.temp.name) / "runtime-locks"
        self.path = self.root / "2026/08/11/Journal_2026-08-11.md"
        self.path.parent.mkdir(parents=True)
        self.base_content = "# Journal\n\nAusgangsstand\n"
        self.path.write_text(self.base_content, encoding="utf-8")
        self.document_id = uuid.uuid4()
        self.base_revision_id = uuid.uuid4()
        self.root_key = "phase5-root-" + str(uuid.uuid4())
        self.now = datetime(2026, 8, 11, 10, tzinfo=timezone.utc)
        self._create_document()
        self.store = DocumentStore(self.engine, lock_root=self.lock_root)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _create_document(self):
        base_hash = hashlib.sha256(self.base_content.encode("utf-8")).hexdigest()
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO documents (id, owner_id, scope, document_type, storage_root_key, relative_path,
                    storage_mode, state, materialized_file_hash, materialized_size, materialized_mtime_ns,
                    created_at, updated_at)
                VALUES (:id, 'phase5-user', 'personal', 'journal', :root, :path, 'db_managed', 'active',
                    :hash, :size, :mtime, :now, :now)
            """), {"id": self.document_id, "root": self.root_key,
                  "path": self.path.relative_to(self.root).as_posix(), "hash": base_hash,
                  "size": len(self.base_content.encode("utf-8")), "mtime": self.path.stat().st_mtime_ns,
                  "now": self.now})
            connection.execute(text("""
                INSERT INTO document_revisions (id, document_id, origin, reason, file_hash, semantic_hash, created_at, summary)
                VALUES (:id, :document, 'external', 'external_import', :hash, :hash, :now, 'Phase-5 Testbasis')
            """), {"id": self.base_revision_id, "document": self.document_id, "hash": base_hash, "now": self.now})
            connection.execute(text("""
                INSERT INTO document_snapshots (document_revision_id, markdown_content, file_hash, created_at)
                VALUES (:id, :content, :hash, :now)
            """), {"id": self.base_revision_id, "content": self.base_content, "hash": base_hash, "now": self.now})
            connection.execute(text("UPDATE documents SET current_revision_id = :revision WHERE id = :id"),
                               {"revision": self.base_revision_id, "id": self.document_id})

    def _prepare_operation(self, content, key="recover-key"):
        with self.engine.begin() as connection:
            revision_id, target_hash = prepare_write_revision(connection, document_id=self.document_id,
                                                               content=content, now=self.now)
            operation_id = uuid.uuid4()
            base_hash = hashlib.sha256(self.base_content.encode("utf-8")).hexdigest()
            connection.execute(text("""
                INSERT INTO pending_file_operations (id, document_id, idempotency_key, status, base_file_hash,
                    target_file_hash, target_markdown_content, target_revision_id, created_at, updated_at)
                VALUES (:id, :document, :key, 'prepared', :base, :target, :content, :revision, :now, :now)
            """), {"id": operation_id, "document": self.document_id, "key": key, "base": base_hash,
                    "target": target_hash, "content": content, "revision": revision_id, "now": self.now})
        return operation_id, revision_id

    def test_write_is_atomic_finalized_and_idempotent(self):
        target = "# Journal\n\nGespeicherter Stand\n"
        first = self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                                 content=target, idempotency_key="browser-save-1", now=self.now)
        second = self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                                  content=target, idempotency_key="browser-save-1", now=self.now)
        self.assertEqual(first, second)
        self.assertEqual(self.path.read_text(encoding="utf-8"), target)
        self.assertTrue((self.lock_root / (str(self.document_id) + ".lock")).is_file())
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT current_revision_id FROM documents WHERE id = :id"),
                                               {"id": self.document_id}), first)
            self.assertEqual(connection.scalar(text("""
                SELECT count(*) FROM pending_file_operations WHERE document_id = :id AND status = 'finalized'
            """), {"id": self.document_id}), 1)

    def test_write_persists_only_hash_verified_media_reference_for_its_new_revision(self):
        media_id = uuid.uuid4()
        media = self.root / "2026/08/11/media/photo.jpg"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"verified photo")
        media_hash = hashlib.sha256(b"verified photo").hexdigest()
        target = (
            "# Journal\n\n<!-- jt:media {\"id\":\"%s\",\"type\":\"image\",\"mime_type\":\"image/jpeg\","
            "\"media_path\":\"2026/08/11/media/photo.jpg\",\"sha256\":\"%s\"} -->\n" % (media_id, media_hash)
        )
        revision = self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                                    content=target, idempotency_key="media-save", now=self.now)
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT media_id, media_path, file_hash, byte_size FROM document_media_references
                WHERE document_revision_id = :revision
            """), {"revision": revision}).mappings().one()
        self.assertEqual(str(row["media_id"]), str(media_id))
        self.assertEqual(row["media_path"], "2026/08/11/media/photo.jpg")
        self.assertEqual(row["file_hash"], media_hash)
        self.assertEqual(row["byte_size"], len(b"verified photo"))

    def test_restored_media_content_creates_a_reference_for_the_new_revision(self):
        media_id = uuid.uuid4()
        media = self.root / "2026/08/11/media/restored.jpg"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"restored photo")
        media_hash = hashlib.sha256(b"restored photo").hexdigest()
        with_media = (
            "# Journal\n\n<!-- jt:media {\"id\":\"%s\",\"type\":\"image\",\"mime_type\":\"image/jpeg\","
            "\"media_path\":\"2026/08/11/media/restored.jpg\",\"sha256\":\"%s\"} -->\n" % (media_id, media_hash)
        )
        first = self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                                 content=with_media, idempotency_key="media-original", now=self.now)
        self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                         content="# Journal\n\nOhne Foto\n", idempotency_key="media-removed", now=self.now)
        restored = self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                                    content=with_media, idempotency_key="media-restored", now=self.now)
        self.assertNotEqual(first, restored)
        with self.engine.connect() as connection:
            restored_media = connection.execute(text("""
                SELECT media_id, file_hash FROM document_media_references WHERE document_revision_id = :revision
            """), {"revision": restored}).mappings().one()
        self.assertEqual(str(restored_media["media_id"]), str(media_id))
        self.assertEqual(restored_media["file_hash"], media_hash)

    def test_runtime_lock_root_must_be_explicit(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(DocumentStoreError):
                runtime_lock_root()
        with mock.patch.dict(os.environ, {"JOURNL_DB_LOCK_ROOT": str(self.lock_root)}, clear=True):
            self.assertEqual(runtime_lock_root(), self.lock_root)

    def test_conflict_document_requires_explicit_resolution_write_path(self):
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE documents SET state = 'conflict' WHERE id = :id"),
                               {"id": self.document_id})
        with self.assertRaises(DocumentStoreError):
            self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                             content="# Journal\n\nNormal\n", idempotency_key="normal-conflict", now=self.now)
        revision = self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                                    content="# Journal\n\nAufgeloest\n", idempotency_key="resolve-conflict",
                                    now=self.now, allow_conflict=True)
        self.assertIsNotNone(revision)

    def test_external_content_gets_pre_write_checkpoint_before_materialization(self):
        external = "# Journal\n\nExtern\n"
        target = "# Journal\n\nJ-Tracker\n"
        self.path.write_text(external, encoding="utf-8")
        self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                         content=target, idempotency_key="browser-save-2", now=self.now)
        with self.engine.connect() as connection:
            reasons = connection.execute(text("""
                SELECT reason FROM document_revisions WHERE document_id = :id ORDER BY created_at, id
            """), {"id": self.document_id}).scalars().all()
        self.assertIn("pre_write_checkpoint", reasons)
        self.assertEqual(self.path.read_text(encoding="utf-8"), target)

    def test_expected_hash_rejects_stale_browser_content_without_overwriting_external_change(self):
        stale_hash = hashlib.sha256(self.base_content.encode("utf-8")).hexdigest()
        external = "# Journal\n\nExtern\n"
        self.path.write_text(external, encoding="utf-8")
        with self.assertRaises(DocumentConflict):
            self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                             content="# Journal\n\nBrowser\n", idempotency_key="stale-browser-save",
                             now=self.now, expected_file_hash=stale_hash)
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("""
                SELECT count(*) FROM pending_file_operations WHERE document_id = :id
            """), {"id": self.document_id}), 0)

    def test_second_external_change_during_checkpoint_aborts_before_prepared_operation(self):
        self.path.write_text("# Journal\n\nExtern A\n", encoding="utf-8")
        real_read = __import__("document_store")._read_bytes
        reads = 0

        def changed_on_second_read(path):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.path.write_text("# Journal\n\nExtern B\n", encoding="utf-8")
            return real_read(path)

        with mock.patch("document_store._read_bytes", side_effect=changed_on_second_read):
            with self.assertRaises(DocumentConflict):
                self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                                 content="# Journal\n\nJ-Tracker\n", idempotency_key="checkpoint-race", now=self.now)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "# Journal\n\nExtern B\n")
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("""
                SELECT count(*) FROM pending_file_operations WHERE document_id = :id
            """), {"id": self.document_id}), 0)

    def test_failed_filesystem_write_keeps_source_and_marks_operation_failed(self):
        with mock.patch("document_store._atomic_replace", side_effect=OSError("synthetic")):
            with self.assertRaises(DocumentStoreError):
                self.store.write(document_id=self.document_id, storage_root_key=self.root_key, root=self.root,
                                 content="# Journal\n\nNeu\n", idempotency_key="filesystem-failure", now=self.now)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.base_content)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("""
                SELECT status FROM pending_file_operations WHERE document_id = :id
            """), {"id": self.document_id}), "failed")

    def test_recovery_finalizes_target_hash(self):
        target = "# Journal\n\nNach Neustart\n"
        operation_id, revision_id = self._prepare_operation(target)
        self.path.write_text(target, encoding="utf-8")  # Simulates rename before DB status update.
        recovered = self.store.recover_open_operations(storage_roots={self.root_key: self.root})
        self.assertEqual(recovered, [operation_id])
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT current_revision_id FROM documents WHERE id = :id"),
                                               {"id": self.document_id}), revision_id)

    def test_recovery_marks_third_hash_conflict_without_overwriting_file(self):
        operation_id, _ = self._prepare_operation("# Journal\n\nZiel\n", key="third-hash")
        self.path.write_text("# Journal\n\nDritter Stand\n", encoding="utf-8")
        self.assertEqual(self.store.recover_open_operations(storage_roots={self.root_key: self.root}), [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "# Journal\n\nDritter Stand\n")
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT status FROM pending_file_operations WHERE id = :id"),
                                               {"id": operation_id}), "conflict")


if __name__ == "__main__":
    unittest.main()
