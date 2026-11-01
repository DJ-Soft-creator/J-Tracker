"""Datei-Export der Journal-Analyse-Verwaltung ("B" in JA-02)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import journal_analysis_store as store  # noqa: E402


class SlugTests(unittest.TestCase):
    def test_slugify_is_lowercase_ascii_and_hyphenated(self):
        self.assertEqual(store.slugify("Täglicher Rückblick 2026"), "taeglicher-rueckblick-2026")
        self.assertEqual(store.slugify("  A / B  "), "a-b")
        self.assertEqual(store.slugify("ÄÖÜß"), "aeoeuess")

    def test_slugify_of_empty_or_punctuation_only_is_empty(self):
        self.assertEqual(store.slugify(""), "")
        self.assertEqual(store.slugify(None), "")
        self.assertEqual(store.slugify("---"), "")


class PathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_job_root_is_below_the_user_and_the_analysis_dir(self):
        path = store.job_root("david", "pendel-check", root=self.root)
        self.assertEqual(path, self.root / "david" / "journal-analysen" / "pendel-check")

    def test_path_traversal_and_absolute_segments_are_rejected(self):
        for bad in ("..", ".", "a/b", "/etc", "a\\b", "..hidden", ""):
            with self.assertRaises(ValueError):
                store.job_root("david", bad, root=self.root)
            with self.assertRaises(ValueError):
                store.user_root(bad, root=self.root)

    def test_dot_inside_a_slug_is_allowed_but_a_leading_dot_is_not(self):
        self.assertEqual(store.slugify("v1.2"), "v1-2")
        with self.assertRaises(ValueError):
            store.job_root("david", ".secret", root=self.root)


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_job_summary_state_round_trip(self):
        job = {"slug": "pendel", "name": "Pendel-Check", "enabled": True}
        store.write_job("david", "pendel", job, root=self.root)
        self.assertEqual(store.read_job("david", "pendel", root=self.root), job)
        store.write_summary("david", "pendel", "## Tag\n\nInhalt\n", root=self.root)
        self.assertEqual(store.read_summary("david", "pendel", root=self.root), "## Tag\n\nInhalt\n")
        store.write_state("david", "pendel", {"open_tasks": ["a"], "themes": []}, root=self.root)
        self.assertEqual(store.read_state("david", "pendel", root=self.root),
                         {"open_tasks": ["a"], "themes": []})

    def test_runs_and_notifications_round_trip(self):
        store.write_run("david", "pendel", "2026-09-23", {"status": "completed", "result": "x"},
                        root=self.root)
        loaded = store.read_run("david", "pendel", "2026-09-23", root=self.root)
        self.assertEqual(loaded["status"], "completed")
        self.assertEqual(loaded["run_date"], "2026-09-23")
        store.write_run("david", "pendel", "2026-09-21", {"status": "failed"}, root=self.root)
        dates = [run.get("run_date") for run in store.list_runs("david", "pendel", root=self.root)]
        self.assertEqual(dates, ["2026-09-21", "2026-09-23"])
        store.write_notifications("david", "pendel", [{"id": "n1", "status": "open"}], root=self.root)
        self.assertEqual(store.read_notifications("david", "pendel", root=self.root),
                         [{"id": "n1", "status": "open"}])

    def test_invalid_run_date_is_rejected(self):
        with self.assertRaises(ValueError):
            store.write_run("david", "pendel", "23.09.2026", {}, root=self.root)

    def test_missing_files_yield_empty_defaults(self):
        self.assertIsNone(store.read_job("david", "neu", root=self.root))
        self.assertEqual(store.read_summary("david", "neu", root=self.root), "")
        self.assertEqual(store.read_state("david", "neu", root=self.root), {})
        self.assertIsNone(store.read_run("david", "neu", "2026-09-23", root=self.root))
        self.assertEqual(store.list_runs("david", "neu", root=self.root), [])
        self.assertEqual(store.read_notifications("david", "neu", root=self.root), [])

    def test_corrupt_json_is_treated_as_missing(self):
        path = store.job_root("david", "kaputt", root=self.root)
        path.mkdir(parents=True, exist_ok=True)
        (path / "job.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(store.read_job("david", "kaputt", root=self.root))

    def test_export_snapshot_writes_every_part(self):
        store.export_snapshot(
            "david", "pendel",
            job={"slug": "pendel"},
            summary="Zusammenfassung",
            state={"themes": ["x"]},
            runs=[{"run_date": "2026-09-23", "status": "completed"}],
            notifications=[{"id": "n1"}],
            root=self.root,
        )
        base = store.job_root("david", "pendel", root=self.root)
        for name in ("job.json", "summary.md", "state.json", "notifications.json"):
            self.assertTrue((base / name).is_file(), name)
        self.assertTrue((base / "runs" / "2026-09-23.json").is_file())
        # Sortierschlüssel bleiben stabil, damit ein Diff nur echte Änderungen zeigt.
        self.assertEqual(json.loads((base / "job.json").read_text(encoding="utf-8")), {"slug": "pendel"})

    def test_export_snapshot_skips_runs_without_a_date(self):
        store.export_snapshot("david", "pendel", job={}, summary="", state={},
                              runs=[{"status": "completed"}], notifications=[], root=self.root)
        self.assertEqual(store.list_runs("david", "pendel", root=self.root), [])


if __name__ == "__main__":
    unittest.main()