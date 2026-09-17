import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "db-journal-cutover.py"
SPEC = importlib.util.spec_from_file_location("db_journal_cutover", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class JournalCutoverScriptTests(unittest.TestCase):
    def test_apply_requires_exact_stable_dry_run_id(self):
        payload = {"run_id": "stable-run", "journals": []}
        with self.assertRaisesRegex(ValueError, "exakt"):
            MODULE.apply_cutover(payload, "wrong")

    def test_candidate_plan_excludes_non_shadow_documents(self):
        class Engine:
            def connect(self): return self
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def dispose(self): pass
            def execute(self, *_args, **_kwargs): return self
            def mappings(self): return self
            def all(self): return [{"id": "document-1", "relative_path": "2026/08/10/Journal_2026-08-10.md",
                                     "storage_mode": "legacy_file", "state": "active", "materialized_file_hash": "hash"}]

        item = type("Item", (), {"status": "eligible", "relative_path": "2026/08/10/Journal_2026-08-10.md", "file_hash": "hash"})()
        with (
            mock.patch.dict(sys.modules, {"sqlalchemy": type("Sql", (), {"create_engine": lambda *_a, **_k: Engine(), "text": lambda value: value})}),
            mock.patch.object(MODULE, "_candidates", return_value=[(MODULE._date("2026-08-10"), item)]),
            mock.patch.object(MODULE.db, "database_url", return_value="postgresql://test"),
        ):
            payload = MODULE.plan(Path("/tmp/root"), "user", "dev-user-user", MODULE._date("2026-08-10"), MODULE._date("2026-08-10"))
        self.assertFalse(payload["journals"][0]["eligible"])
        self.assertEqual(payload["journals"][0]["reason"], "kein shadow_indexed-Dokument")
        self.assertEqual(payload["journals"][0]["materialized_file_hash"], "hash")


if __name__ == "__main__":
    unittest.main()
