"""Einsammeln unbenutzter Medien in die Tagesablage.

Die entscheidende Regel: eingesammelt wird nur, was **nicht verwendet** wurde.
Alles andere traegt einen Medienmarker, dessen Pfad nicht wandern darf
(docs/db/phase-10-media-inventory.md).
"""

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "bucket_scheduler", ROOT / "scripts" / "bucket-scheduler.py")
bucket_scheduler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bucket_scheduler)


class BucketSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.user = self.data / "user-1"
        self.now = datetime(2026, 8, 23, 0, 5, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def _screenshot(self, day, identifier, status="pending"):
        year, month, date = day.split("-")
        inbox = self.user / year / month / date / "media" / "Screenshot-Inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        base = f"{day}_10-00-00_{identifier}"
        (inbox / f"{base}.png").write_bytes(b"bild")
        (inbox / f"{base}_preview.jpg").write_bytes(b"vorschau")
        (inbox / f"{base}.json").write_text(json.dumps({
            "schema": 1, "id": identifier, "user_id": "user-1", "date": day,
            "type": "image", "status": status,
            "source_path": f"{year}/{month}/{date}/media/Screenshot-Inbox/{base}.png",
            "preview_path": f"{year}/{month}/{date}/media/Screenshot-Inbox/{base}_preview.jpg",
            "captured_at": f"{day}T10:00:00+02:00",
        }), encoding="utf-8")
        return inbox, base

    def _bucket(self, day):
        year, month, date = day.split("-")
        return self.user / year / month / date / "media" / "Tagesablage"

    def test_open_screenshots_of_past_days_are_collected(self):
        inbox, base = self._screenshot("2026-08-22", "aaa")
        moved = bucket_scheduler.run_once(self.data, now=self.now)
        self.assertEqual(moved, 1)

        bucket = self._bucket("2026-08-22")
        self.assertTrue((bucket / f"{base}.png").is_file())
        self.assertTrue((bucket / f"{base}_preview.jpg").is_file())
        # E-12: aus der Inbox verschwunden.
        self.assertFalse((inbox / f"{base}.json").exists())
        self.assertFalse((inbox / f"{base}.png").exists())

        item = json.loads((bucket / f"{base}.json").read_text(encoding="utf-8"))
        self.assertEqual(item["status"], "collected")
        self.assertTrue(item["collected_at"])
        self.assertEqual(item["origin"], "Screenshot-Inbox")

    def test_used_screenshots_stay_where_they_are(self):
        """Alles ausser `pending` ist verwendet und darf nicht wandern."""
        for status in ("attached", "linked", "collected"):
            with self.subTest(status=status):
                inbox, base = self._screenshot("2026-08-21", f"id-{status}", status=status)
                bucket_scheduler.run_once(self.data, now=self.now)
                self.assertTrue((inbox / f"{base}.png").is_file())
                self.assertTrue((inbox / f"{base}.json").is_file())

    def test_the_current_day_is_left_alone(self):
        inbox, base = self._screenshot("2026-08-23", "heute")
        self.assertEqual(bucket_scheduler.run_once(self.data, now=self.now), 0)
        self.assertTrue((inbox / f"{base}.json").is_file())

    def test_a_second_run_changes_nothing(self):
        self._screenshot("2026-08-22", "bbb")
        self.assertEqual(bucket_scheduler.run_once(self.data, now=self.now), 1)
        self.assertEqual(bucket_scheduler.run_once(self.data, now=self.now), 0)

    def test_an_entry_without_files_is_left_untouched(self):
        inbox, base = self._screenshot("2026-08-22", "ccc")
        (inbox / f"{base}.png").unlink()
        (inbox / f"{base}_preview.jpg").unlink()
        self.assertEqual(bucket_scheduler.run_once(self.data, now=self.now), 0)
        # Der verwaiste Eintrag verschwindet nicht still.
        self.assertTrue((inbox / f"{base}.json").is_file())

    def test_several_users_are_handled_independently(self):
        self._screenshot("2026-08-22", "ddd")
        self.user = self.data / "user-2"
        self._screenshot("2026-08-22", "eee")
        self.assertEqual(bucket_scheduler.run_once(self.data, now=self.now), 2)


if __name__ == "__main__":
    unittest.main()
