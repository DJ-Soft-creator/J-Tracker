"""Nightly cutover end-to-end: shadow import -> db_managed -> searchable/complete."""

import importlib.util
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
from shadow_index import inventory, shadow_import  # noqa: E402

SCRIPT = ROOT_DIR / "scripts" / "db-journal-cutover-scheduler.py"
SPEC = importlib.util.spec_from_file_location("db_journal_cutover_scheduler", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PhaseEightCutoverSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["DATABASE_URL"])
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
        command.upgrade(config, "head")

    def tearDown(self):
        self.engine.dispose()

    def _journal_in(self, root, day, content):
        path = root / day.strftime("%Y") / day.strftime("%m") / day.strftime("%d") / f"Journal_{day:%Y-%m-%d}.md"
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_nightly_cutover_finalizes_db_managed_journal(self):
        owner_id = str(uuid.uuid4())
        storage_root_key = "dev-user-" + owner_id
        now = datetime(2026, 8, 11, 22, 0, 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / owner_id
            root.mkdir()
            content = "# Journal\n\n## Eintrag | Datum & Uhrzeit: 2026-08-11T18:00:00+02:00\n\nInhalt\n"
            self._journal_in(root, now.date(), content)
            with self.engine.begin() as connection:
                imported = shadow_import(connection, owner_id=owner_id,
                                         storage_root_key=storage_root_key,
                                         items=inventory(root))
            self.assertEqual(imported, {"imported": 1, "unchanged": 0, "blocked": 0, "working": 0})

            first = MODULE.run_once(engine=self.engine, data_dir=str(root.parent), since_days=2, now=now)
            self.assertEqual(first[0]["switched"], 1)
            self.assertEqual(first[0]["eligible"], 1)

            with self.engine.connect() as connection:
                mode = connection.scalar(text(
                    "SELECT storage_mode FROM documents WHERE storage_root_key = :root"
                ), {"root": storage_root_key})
                self.assertEqual(mode, "db_managed")
                revision = connection.scalar(text(
                    "SELECT current_revision_id FROM documents WHERE storage_root_key = :root"
                ), {"root": storage_root_key})
                search_rows = connection.scalar(text(
                    "SELECT COUNT(*) FROM document_search WHERE document_id = "
                    "(SELECT id FROM documents WHERE storage_root_key = :root)"
                ), {"root": storage_root_key})
                self.assertEqual(search_rows, 1)
                manifest_rows = connection.scalar(text(
                    "SELECT COUNT(*) FROM document_revision_blocks WHERE document_revision_id = :revision"
                ), {"revision": revision})
                self.assertEqual(manifest_rows, 1)

            second = MODULE.run_once(engine=self.engine, data_dir=str(root.parent), since_days=2, now=now)
            self.assertEqual(second, [])  # bereits db_managed -> nichts mehr umzuschalten
            with self.engine.connect() as connection:
                search_rows = connection.scalar(text(
                    "SELECT COUNT(*) FROM document_search WHERE document_id = "
                    "(SELECT id FROM documents WHERE storage_root_key = :root)"
                ), {"root": storage_root_key})
                self.assertEqual(search_rows, 1)

    def test_catch_up_flips_old_and_hash_matching_journal_only(self):
        owner_id = str(uuid.uuid4())
        storage_root_key = "dev-user-" + owner_id
        now = datetime(2026, 8, 11, 22, 0, 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / owner_id
            root.mkdir()
            content = "# Journal\n\n## Eintrag | Datum & Uhrzeit: 2026-08-01T18:00:00+02:00\n\nInhalt\n"
            self._journal_in(root, now.date(), content)  # aktueller Tag -> Fenster
            self._journal_in(root, content=content, day=now.replace(day=1).date())  # alt -> Catch-up
            with self.engine.begin() as connection:
                shadow_import(connection, owner_id=owner_id, storage_root_key=storage_root_key,
                              items=inventory(root))
            # Die alte Datei wird nach dem Import geändert -> Hash-Differenz => bleibt liegen.
            old = root / "2026" / "08" / "01" / "Journal_2026-08-01.md"
            old.write_text(content + "\nExterner Autosave\n", encoding="utf-8")
            result = MODULE.run_once(engine=self.engine, data_dir=str(root.parent), since_days=2,
                                     now=now, catch_up_limit=50)
            self.assertEqual(result[0]["window"], 1)
            self.assertEqual(result[0]["catch_up"], 0)  # alte Datei derzeit hash-abweichend
            self.assertEqual(result[0]["switched"], 1)
            with self.engine.connect() as connection:
                rows = connection.execute(text("""
                    SELECT relative_path, storage_mode FROM documents WHERE storage_root_key = :root
                """), {"root": storage_root_key}).mappings().all()
            modes = {Path(row["relative_path"]).name: row["storage_mode"] for row in rows}
            self.assertEqual(modes["Journal_2026-08-11.md"], "db_managed")
            self.assertEqual(modes["Journal_2026-08-01.md"], "shadow_indexed")
            # Spiegel gleicht die alte Datei an; danach zieht der Catch-up sie nach.
            with self.engine.begin() as connection:
                shadow_import(connection, owner_id=owner_id, storage_root_key=storage_root_key,
                              items=inventory(root))
            from revisions import promote_checkpoint
            with self.engine.begin() as connection:
                old_row = connection.execute(text("""
                    SELECT id FROM documents WHERE storage_root_key = :root AND relative_path = :path
                """), {"root": storage_root_key, "path": "2026/08/01/Journal_2026-08-01.md"}).mappings().one()
                promote_checkpoint(connection, document_id=old_row["id"],
                                   now=datetime.now().astimezone(), reason="pre_write_checkpoint")
            result = MODULE.run_once(engine=self.engine, data_dir=str(root.parent), since_days=2,
                                     now=now, catch_up_limit=50)
            self.assertEqual(result[0]["catch_up"], 1)
            self.assertEqual(result[0]["switched"], 1)

    def test_dry_run_switches_nothing(self):
        owner_id = str(uuid.uuid4())
        storage_root_key = "dev-user-" + owner_id
        now = datetime(2026, 8, 11, 22, 0, 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / owner_id
            root.mkdir()
            content = "# Journal\n\n## Eintrag | Datum & Uhrzeit: 2026-08-11T18:00:00+02:00\n\nInhalt\n"
            self._journal_in(root, now.date(), content)
            with self.engine.begin() as connection:
                shadow_import(connection, owner_id=owner_id, storage_root_key=storage_root_key,
                              items=inventory(root))
            result = MODULE.run_once(engine=self.engine, data_dir=str(root.parent), since_days=2,
                                     now=now, dry_run=True)
            self.assertEqual(result[0]["switched"], 0)
            with self.engine.connect() as connection:
                mode = connection.scalar(text(
                    "SELECT storage_mode FROM documents WHERE storage_root_key = :root"
                ), {"root": storage_root_key})
                self.assertEqual(mode, "shadow_indexed")


if __name__ == "__main__":
    unittest.main()