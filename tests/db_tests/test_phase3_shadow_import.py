"""Phase-3 import idempotency against disposable PostgreSQL."""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
from shadow_index import inventory, shadow_import  # noqa: E402


class PhaseThreeShadowImportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["DATABASE_URL"])
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
        command.upgrade(config, "head")

    def tearDown(self):
        self.engine.dispose()

    def test_import_is_idempotent_and_never_mutates_source_file(self):
        owner_id = "phase3-user-" + str(uuid.uuid4())
        storage_root_key = "test-root-" + str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "2026" / "08" / "11" / "Journal_2026-08-11.md"
            path.parent.mkdir(parents=True)
            content = "# Journal\n\n## Eintrag | Datum & Uhrzeit: 2026-08-11T09:00:00+02:00\n\nInhalt\n"
            path.write_text(content, encoding="utf-8")
            before = path.read_bytes()
            items = inventory(root)
            with self.engine.begin() as connection:
                first = shadow_import(connection, owner_id=owner_id, storage_root_key=storage_root_key, items=items)
            with self.engine.begin() as connection:
                second = shadow_import(connection, owner_id=owner_id, storage_root_key=storage_root_key, items=items)
            changed_content = content + "\nExterner Autosave\n"
            path.write_text(changed_content, encoding="utf-8")
            changed_before = path.read_bytes()
            with self.engine.begin() as connection:
                changed = shadow_import(connection, owner_id=owner_id, storage_root_key=storage_root_key,
                                        items=inventory(root))
            self.assertEqual(before, content.encode("utf-8"))
            self.assertEqual(path.read_bytes(), changed_before)
        self.assertEqual(first, {"imported": 1, "unchanged": 0, "blocked": 0, "working": 0})
        self.assertEqual(second, {"imported": 0, "unchanged": 1, "blocked": 0, "working": 0})
        self.assertEqual(changed, {"imported": 0, "unchanged": 0, "blocked": 0, "working": 1})
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM documents WHERE owner_id = :owner"),
                                               {"owner": owner_id}), 1)
            self.assertEqual(connection.scalar(text("""
                SELECT count(*) FROM document_revisions revision
                JOIN documents document ON document.id = revision.document_id
                WHERE document.storage_root_key = :root AND revision.summary = 'Phase-3 Shadow-Import'
            """), {"root": storage_root_key}), 1)
            self.assertEqual(connection.scalar(text("""
                SELECT snapshot.markdown_content FROM document_snapshots snapshot
                JOIN document_revisions revision ON revision.id = snapshot.document_revision_id
                JOIN documents document ON document.id = revision.document_id
                WHERE document.storage_root_key = :root
            """), {"root": storage_root_key}), content)
            self.assertEqual(connection.scalar(text("""
                SELECT working.observed_content FROM document_working_states working
                JOIN documents document ON document.id = working.document_id
                WHERE document.storage_root_key = :root
            """), {"root": storage_root_key}), changed_content)


if __name__ == "__main__":
    unittest.main()
