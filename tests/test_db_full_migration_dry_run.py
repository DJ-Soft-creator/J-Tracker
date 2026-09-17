import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "db-full-migration-dry-run.py"
SPEC = importlib.util.spec_from_file_location("db_full_migration_dry_run", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FullMigrationDryRunTests(unittest.TestCase):
    def test_report_remains_read_only_and_blocks_active_jobs(self):
        item = type("Item", (), {"status": "eligible", "relative_path": "Journal_2026-08-13.md"})()
        document = {"id": "document-1", "relative_path": "Journal_2026-08-13.md", "document_type": "journal",
                    "storage_mode": "shadow_indexed", "state": "active", "materialized_file_hash": "hash",
                    "materialized_size": 12}
        job = {"id": "job-1", "job_status": "queued", "error_code": None, "response_status": None}
        with (
            mock.patch.object(MODULE, "inventory", return_value=(item,)),
            mock.patch.object(MODULE, "report", return_value={"summary": {"items": 1, "by_status": {"eligible": 1}}}),
            mock.patch.object(MODULE, "_database_state", return_value=([document], [job])),
            mock.patch.object(MODULE, "reconcile", return_value={"file_new": [], "hash_changed": [], "parser_blocked": [], "missing_media": [], "path_not_allowed": [], "database_without_file": []}),
            mock.patch.object(MODULE, "_free_space", return_value={"available_bytes": 100, "filesystem_total_bytes": 200, "journal_root_bytes": 12}),
            mock.patch.object(MODULE.db, "database_url", return_value="postgresql://read-only-test"),
            mock.patch.dict(sys.modules, {"sqlalchemy": type("Sql", (), {"create_engine": lambda *_a, **_k: _ReadOnlyEngine(), "text": lambda value: value})}),
        ):
            payload = MODULE.plan(Path("/tmp/root"), "owner", "dev-owner")
        self.assertEqual(payload["mode"], "read_only_phase_13_dry_run")
        self.assertEqual(payload["expected_changes"]["database_writes"], 0)
        self.assertEqual(payload["background_jobs"]["active_job_ids"], ["job-1"])
        self.assertFalse(payload["ready_for_any_apply"])


class _ReadOnlyEngine:
    def connect(self): return self
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def dispose(self): pass
    def execute(self, *_args, **_kwargs): return self
    def mappings(self): return self
    def all(self): return []


if __name__ == "__main__":
    unittest.main()
