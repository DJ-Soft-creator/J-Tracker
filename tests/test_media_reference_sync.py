import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "db-sync-media-references.py"
SPEC = importlib.util.spec_from_file_location("db_sync_media_references", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MediaReferenceSyncTests(unittest.TestCase):
    def test_apply_requires_exact_stable_dry_run_id(self):
        with self.assertRaisesRegex(ValueError, "exakt"):
            MODULE.apply({"run_id": "stable-run", "owner_id": "user", "journals": []}, "wrong")

    def test_empty_apply_is_a_noop_after_confirmation(self):
        self.assertEqual(MODULE.apply({"run_id": "stable-run", "owner_id": "user", "journals": []}, "stable-run"), 0)


if __name__ == "__main__":
    unittest.main()
