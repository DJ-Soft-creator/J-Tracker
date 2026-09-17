"""Unit tests for the Phase-5 recovery worker's root allowlist."""

import runpy
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "db-operation-recovery.py"
MODULE = runpy.run_path(str(SCRIPT), run_name="db_operation_recovery_test")


class RecoveryWorkerRootTests(unittest.TestCase):
    def test_only_uuid_user_directories_become_dev_storage_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_id = str(uuid.uuid4())
            (root / user_id).mkdir()
            (root / "family").mkdir()
            (root / "indexes").mkdir()
            (root / "not-a-user").mkdir()
            roots = MODULE["dev_user_roots"](root)
        self.assertEqual(roots, {"dev-user-" + user_id: (root / user_id).resolve()})


if __name__ == "__main__":
    unittest.main()
