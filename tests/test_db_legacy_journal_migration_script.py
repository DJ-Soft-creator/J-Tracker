import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "db-legacy-journal-migration.py"
SPEC = importlib.util.spec_from_file_location("db_legacy_journal_migration", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LegacyJournalMigrationScriptTests(unittest.TestCase):
    def test_apply_requires_both_exact_dry_run_confirmations(self):
        payload = {"run_id": "stable-run", "reconciliation": {"file_new": []}, "journals": []}
        with self.assertRaisesRegex(ValueError, "backup-confirm"):
            MODULE.apply(payload, Path("/tmp"), confirm="stable-run", backup_confirm="wrong")

    def test_run_id_is_stable_for_identical_plan(self):
        payload = {"owner_id": "owner", "journals": [], "reconciliation": {}, "summary": {"eligible": 0, "excluded": 0}}
        self.assertEqual(MODULE._run_id(payload), MODULE._run_id(payload))


if __name__ == "__main__":
    unittest.main()
