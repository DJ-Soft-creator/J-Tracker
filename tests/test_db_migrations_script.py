"""Fast safety checks for the environment-aware database migration runner."""

import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "DB-Migrations.sh"


class DatabaseMigrationScriptTests(unittest.TestCase):
    def test_help_documents_non_destructive_contract(self):
        result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("never deploys", result.stdout)
        self.assertIn("--confirm <target>", result.stdout)

    def test_upgrade_requires_exact_target_confirmation_before_any_environment_access(self):
        result = subprocess.run(["bash", str(SCRIPT), "dev", "upgrade"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--confirm dev", result.stderr)

    def test_invalid_target_is_rejected_before_any_environment_access(self):
        result = subprocess.run(["bash", str(SCRIPT), "staging", "status"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dev oder prod", result.stderr)


if __name__ == "__main__":
    unittest.main()
