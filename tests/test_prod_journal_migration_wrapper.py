import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate-prod-journals.sh"


class ProductionJournalMigrationWrapperTests(unittest.TestCase):
    def test_help_documents_offline_scope(self):
        result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("offline migration", result.stdout)

    def test_apply_needs_deliberate_confirmation_before_environment_access(self):
        result = subprocess.run(["bash", str(SCRIPT), "--owner-id", "00000000-0000-4000-8000-000000000001"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("I_UNDERSTAND_PROD_MIGRATION", result.stderr)

    def test_legacy_non_uuid_owner_id_is_accepted_before_environment_access(self):
        result = subprocess.run(["bash", str(SCRIPT), "--owner-id", "legacy-tablet-owner"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("I_UNDERSTAND_PROD_MIGRATION", result.stderr)

    def test_owner_id_rejects_path_characters_before_environment_access(self):
        result = subprocess.run(["bash", str(SCRIPT), "--owner-id", "../outside"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("sichere Root-ID", result.stderr)

    def test_help_documents_read_only_preflight(self):
        result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn("does not start PostgreSQL", result.stdout)

    def test_postgres_backup_uses_privileged_output_writer(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('| sudo tee "$backup_dir/postgres.sql" >/dev/null', source)

    def test_apply_waits_for_healthy_postgres_before_backup(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('up -d --wait postgres', source)


if __name__ == "__main__":
    unittest.main()
