"""Shared isolated fixture for Brain API test modules.

AI maintenance contract: keep environment/path setup here; behavior assertions
belong in focused test_brain_*.py modules so no test file exceeds 1000 lines.
"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "brain-api-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/opencode/journl-brain-api-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import brain
    import family
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class BrainApiCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self._old_main_data_dir = main.DATA_DIR
        self._old_users_path = main.USERS_PATH
        self._old_brain_data_dir = brain.DATA_DIR
        self._old_brain_family_dir = brain.FAMILY_DIR
        self._old_tagging_data_dir = brain.tagging_module.DATA_DIR
        self._old_tagging_family_dir = brain.tagging_module.FAMILY_DIR
        self._old_tagging_index_path = brain.tagging_module.INDEX_PATH
        self._old_tagging_snapshot = brain.tagging_module._snapshot
        self._old_family_data_dir = family.DATA_DIR
        self._old_family_dir = family.FAMILY_DIR
        self._old_family_projects_dir = family.PROJECTS_DIR
        self._old_family_planner_dir = family.PLANNER_DIR
        self._old_family_archive_dir = family.ARCHIVE_DIR
        self._old_family_planner_file = family.PLANNER_FILE
        self._old_family_tasks_file = family.FAMILY_TASKS_FILE
        main.DATA_DIR = self.data_dir
        main.USERS_PATH = self.data_dir / "users.json"
        brain.DATA_DIR = self.data_dir
        brain.FAMILY_DIR = self.data_dir / "family"
        brain.tagging_module.DATA_DIR = self.data_dir
        brain.tagging_module.FAMILY_DIR = self.data_dir / "family"
        brain.tagging_module.INDEX_PATH = self.data_dir / "indexes/hashtag_index.json"
        brain.tagging_module._snapshot = None
        brain.reset_search_caches()
        family.DATA_DIR = self.data_dir
        family.FAMILY_DIR = self.data_dir / "family"
        family.PROJECTS_DIR = family.FAMILY_DIR / "projects"
        family.PLANNER_DIR = family.FAMILY_DIR / "planner"
        family.ARCHIVE_DIR = family.FAMILY_DIR / "archive"
        family.PLANNER_FILE = family.PLANNER_DIR / "recurring.md"
        family.FAMILY_TASKS_FILE = family.FAMILY_DIR / "Familien-Aufgaben.md"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        main.USERS_PATH.write_text(json.dumps({
            "users": [
                {"id": "user-a", "username": "Alex", "password": "test"},
                {"id": "user-b", "username": "Bea", "password": "test"},
            ]
        }), encoding="utf-8")
        self._write_sources()
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf-test"
        self.enqueue = mock.patch.object(brain, "enqueue_rebuild", return_value=True)
        self.enqueue.start()

    def tearDown(self):
        self.enqueue.stop()
        main.DATA_DIR = self._old_main_data_dir
        main.USERS_PATH = self._old_users_path
        brain.DATA_DIR = self._old_brain_data_dir
        brain.FAMILY_DIR = self._old_brain_family_dir
        brain.tagging_module.DATA_DIR = self._old_tagging_data_dir
        brain.tagging_module.FAMILY_DIR = self._old_tagging_family_dir
        brain.tagging_module.INDEX_PATH = self._old_tagging_index_path
        brain.tagging_module._snapshot = self._old_tagging_snapshot
        family.DATA_DIR = self._old_family_data_dir
        family.FAMILY_DIR = self._old_family_dir
        family.PROJECTS_DIR = self._old_family_projects_dir
        family.PLANNER_DIR = self._old_family_planner_dir
        family.ARCHIVE_DIR = self._old_family_archive_dir
        family.PLANNER_FILE = self._old_family_planner_file
        family.FAMILY_TASKS_FILE = self._old_family_tasks_file
        self.temp_dir.cleanup()

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def _write(self, relative, content):
        path = self.data_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_sources(self):
        self._write(
            "user-a/hashtag_catalog.json",
            json.dumps({"version": 1, "canonical": ["focus", "work"], "aliases": {}, "proposals": ["unapproved"]}),
        )
        self.journal_path = self._write(
            "user-a/2026/08/03/Journal_2026-08-03.md",
            "# Journal 2026-08-03\n\n"
            "___\n\n"
            "## Thema: Time:10:00:00\n"
            "Visible journal text\n"
            "  - [ ] Personal task #work\n\n"
            "___\n\n"
            "___\n\n"
            "## Brainablage: Tags: Focus, Work **Datum & Uhrzeit: 2026-08-03 11:00:00**\n"
            "Stored note\n\n"
            "___\n",
        )
        self._write("user-a/projects/alpha.md", "# Alpha\n\n- [ ] Project task\n")
        self._write("user-a/notes/keep.md", "# Keep\n\nA lasting note.\n")
        self._write("user-a/notes/README.md", "Technical note that must not be indexed.\n")
        self._write("_Archiv/Projekte/reference.md", "# Reference\n\nArchive text.\n")
        self._write(
            "family/projects/visible.md",
            "---\nassigned_users: [user-a]\n---\n\n# Visible Family\n\n- [ ] Family visible task\n",
        )
        self._write(
            "family/projects/secret.md",
            "---\nassigned_users: [user-b]\n---\n\n# Secret Family\n\nSecret family text #secret\n",
        )

