"""Conservative Phase-6 block merge tests."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from merge_service import merge  # noqa: E402


def journal(one="A", two="B"):
    return (
        "# Journal\n\n___\n\n## Eintrag | Datum & Uhrzeit: 2026-08-11T09:00:00+02:00\n\n"
        + one + "\n\n___\n\n## Eintrag | Datum & Uhrzeit: 2026-08-11T10:00:00+02:00\n\n" + two + "\n"
    )


class MergeServiceTests(unittest.TestCase):
    def test_independent_block_changes_merge_deterministically(self):
        result = merge(journal(), journal(one="Extern"), journal(two="J-Tracker"))
        self.assertEqual(result.status, "merged")
        self.assertIn("Extern", result.content)
        self.assertIn("J-Tracker", result.content)

    def test_same_block_divergence_is_conflict(self):
        result = merge(journal(), journal(one="Extern"), journal(one="J-Tracker"))
        self.assertEqual(result.status, "conflict")

    def test_independent_new_block_is_added_to_unchanged_base_blocks(self):
        external = journal() + "\n___\n\n## Eintrag | Datum & Uhrzeit: 2026-08-11T11:00:00+02:00\n\nExtern neu\n"
        result = merge(journal(), external, journal())
        self.assertEqual(result.status, "merged")
        self.assertIn("Extern neu", result.content)

    def test_delete_against_change_is_conflict(self):
        base = journal()
        deleted = base.split("\n___\n\n## Eintrag | Datum & Uhrzeit: 2026-08-11T10:00:00+02:00")[0] + "\n"
        self.assertEqual(merge(base, deleted, journal(two="Geaendert")).status, "conflict")

    def test_duplicate_anchors_are_never_guessed(self):
        duplicate = journal().replace("2026-08-11T10:00:00+02:00", "2026-08-11T09:00:00+02:00")
        self.assertEqual(merge(duplicate, duplicate, duplicate).status, "conflict")


if __name__ == "__main__":
    unittest.main()
