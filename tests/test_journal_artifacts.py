import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))

from journal_artifacts import inspect_journal_artifacts, inspect_media_sidecars, report_journal_artifacts  # noqa: E402


class JournalArtifactTests(unittest.TestCase):
    def test_inventory_counts_sidecars_and_preserves_invalid_marker_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "2026/08/12/Journal_2026-08-12.md"
            journal = root / relative
            journal.parent.mkdir(parents=True)
            journal.write_text("# Journal\n", encoding="utf-8")
            backup = journal.parent / "_Backup"
            backup.mkdir()
            (backup / "Journal_2026-08-12_backup_a.bak").write_text("old", encoding="utf-8")
            sidecar = journal.parent / ".journal-media"
            sidecar.mkdir()
            (sidecar / "media.json").write_text("{}", encoding="utf-8")
            item = inspect_journal_artifacts(root, relative, "<!-- jt:media {bad} -->")
        self.assertEqual(item["sidecars"], {"backup_files": 1, "journal_media_metadata": 1})
        self.assertEqual(item["markers"]["invalid"], {"media": 1})
        self.assertNotIn("{bad}", str(report_journal_artifacts([item])))

    def test_sidecar_consistency_reports_missing_and_orphan_ids_without_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "2026/08/12/Journal_2026-08-12.md"
            journal = root / relative
            journal.parent.mkdir(parents=True)
            journal.write_text("# Journal\n", encoding="utf-8")
            sidecar = journal.parent / ".journal-media"
            sidecar.mkdir()
            (sidecar / "orphan.json").write_text('{"id":"orphan"}', encoding="utf-8")
            report = inspect_media_sidecars(
                root,
                relative,
                '<!-- jt:media {"id":"marker","media_path":"2026/08/12/media/marker.jpg"} -->',
            )
        self.assertEqual(report["missing_sidecars"], ["marker"])
        self.assertEqual(report["orphan_sidecars"], ["orphan"])

    def test_legacy_media_marker_without_path_does_not_require_a_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "2026/08/10/Journal_2026-08-10.md"
            journal = root / relative
            journal.parent.mkdir(parents=True)
            journal.write_text("# Journal\n", encoding="utf-8")
            report = inspect_media_sidecars(root, relative, '<!-- jt:media {"id":"legacy"} -->')
        self.assertEqual(report["marker_ids"], 0)
        self.assertEqual(report["missing_sidecars"], [])


if __name__ == "__main__":
    unittest.main()
