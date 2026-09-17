import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "db-managed-guard-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-db-managed-guard-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import main


class _Connection:
    def __init__(self, mode, state="active", conflict_id=None):
        self.mode = mode
        self.state = state
        self.conflict_id = conflict_id

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return self

    def mappings(self):
        return self

    def one_or_none(self):
        return None if self.mode is None else {
            "id": "1", "storage_mode": self.mode, "state": self.state,
            "current_revision_id": "revision-1", "open_conflict_id": self.conflict_id,
        }


class _Engine:
    def __init__(self, mode, state="active", conflict_id=None):
        self.mode = mode
        self.state = state
        self.conflict_id = conflict_id
        self.disposed = False

    def connect(self):
        return _Connection(self.mode, self.state, self.conflict_id)

    def dispose(self):
        self.disposed = True


class _Store:
    def __init__(self):
        self.writes = []

    def write(self, **kwargs):
        self.writes.append(kwargs)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class DbManagedLegacyGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.path = self.root / "user-a/2026/08/11/Journal_2026-08-11.md"
        self.path.parent.mkdir(parents=True)
        self.path.write_text("# Heute\n", encoding="utf-8")
        self.old_data_dir = main.DATA_DIR
        main.DATA_DIR = self.root

    def tearDown(self):
        main.DATA_DIR = self.old_data_dir
        self.temp_dir.cleanup()

    def _managed_document(self, mode):
        engine = _Engine(mode)
        with main.app.test_request_context():
            from flask import session

            session["user_id"] = "user-a"
            with (
                mock.patch.object(main, "IS_DEV", True),
                mock.patch.object(main, "is_enabled", return_value=True),
                mock.patch.object(main, "database_url", return_value="postgresql://test"),
                mock.patch("sqlalchemy.create_engine", return_value=engine),
            ):
                document = main._managed_document_for_journal(self.path)
        if document:
            document["engine"].dispose()
        return document, engine

    def test_identifies_db_managed_journal_for_safe_writer(self):
        document, engine = self._managed_document("db_managed")
        self.assertEqual(document["storage_root_key"], "dev-user-user-a")
        self.assertEqual(document["root"], self.root / "user-a")
        self.assertTrue(engine.disposed)

    def test_service_writer_can_identify_owner_without_browser_session(self):
        engine = _Engine("db_managed")
        with (
            main.app.test_request_context(),
            mock.patch.object(main, "IS_DEV", True),
            mock.patch.object(main, "is_enabled", return_value=True),
            mock.patch.object(main, "database_url", return_value="postgresql://test"),
            mock.patch("sqlalchemy.create_engine", return_value=engine),
        ):
            document = main._managed_document_for_journal(self.path, user_id="user-a")
        self.assertEqual(document["storage_root_key"], "dev-user-user-a")
        document["engine"].dispose()
        self.assertTrue(engine.disposed)

    def test_allows_shadow_indexed_journal(self):
        document, engine = self._managed_document("shadow_indexed")
        self.assertIsNone(document)
        self.assertTrue(engine.disposed)

    def test_existing_managed_conflict_reopens_the_same_dialog(self):
        engine = _Engine("db_managed", state="conflict", conflict_id="conflict-1")
        with main.app.test_request_context():
            from flask import session

            session["user_id"] = "user-a"
            with (
                mock.patch.object(main, "IS_DEV", True),
                mock.patch.object(main, "is_enabled", return_value=True),
                mock.patch.object(main, "database_url", return_value="postgresql://test"),
                mock.patch("sqlalchemy.create_engine", return_value=engine),
            ):
                with self.assertRaisesRegex(main.JournalWriteConflict, "parallel geaendert"):
                    main._managed_document_for_journal(self.path)
        self.assertTrue(engine.disposed)

    def test_managed_writer_accepts_plain_content_updater(self):
        engine = _Engine("db_managed")
        store = _Store()
        managed = {
            "engine": engine,
            "document_id": "1",
            "storage_root_key": "dev-user-user-a",
            "root": self.root / "user-a",
        }
        with (
            mock.patch.object(main, "_managed_document_for_journal", return_value=managed),
            mock.patch("document_store.configured_document_store", return_value=store),
        ):
            result = main._update_journal_file(self.path, lambda current: current + "Neu\n")
        self.assertIsNone(result)
        self.assertEqual(store.writes[0]["content"], "# Heute\nNeu\n")
        self.assertTrue(engine.disposed)
