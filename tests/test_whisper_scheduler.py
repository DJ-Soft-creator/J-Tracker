"""Der Whisper-Sidecar muss eine zweite Transkription ersetzen, nicht anhaengen.

Genau daran haengt „mit einem groesseren Modell noch einmal versuchen": ohne
Ersetzen stuenden zwei Transkript-Bloecke im selben Eintrag.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
_spec = importlib.util.spec_from_file_location(
    "whisper_scheduler", ROOT / "scripts" / "whisper-scheduler.py")
whisper_scheduler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(whisper_scheduler)

import transcript_blocks  # noqa: E402

MEDIA_ID = "e4f71c48-e5d3-4797-ab39-6fd64cef113e"
OTHER_ID = "11111111-2222-3333-4444-555555555555"
MARKER = '<!-- jt:media {"id":"%s","type":"audio"} -->' % MEDIA_ID
LABEL = "Sprachnachricht | Aufnahmezeit: 2026-08-24T08:34:36+00:00"


class ApplyToJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patch = mock.patch.object(whisper_scheduler, "DATA_DIR", self.data)
        self.patch.start()
        self.journal = self.data / "u1" / "2026" / "08" / "24" / "Journal_2026-08-24.md"
        self.journal.parent.mkdir(parents=True)
        self.item = {
            "id": MEDIA_ID, "user_id": "u1",
            "journal_path": "2026/08/24/Journal_2026-08-24.md",
        }

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def apply(self, text):
        whisper_scheduler._apply_to_journal("u1", self.item, {"text": text})
        return self.journal.read_text(encoding="utf-8")

    def test_first_run_inserts_behind_the_media_label(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, "", "~~24.08. 10:35:02~~", ""]), encoding="utf-8")
        updated = self.apply("Erste Fassung.")
        lines = updated.split("\n")
        self.assertLess(lines.index(LABEL), lines.index(transcript_blocks.transcript_marker(MEDIA_ID)))
        self.assertIn("Erste Fassung.", updated)

    def test_second_run_replaces_instead_of_adding_a_block(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, "", "~~24.08. 10:35:02~~", ""]), encoding="utf-8")
        self.apply("Schwaches Modell.")
        updated = self.apply("Starkes Modell versteht mehr.")
        self.assertEqual(updated.count(transcript_blocks.transcript_marker(MEDIA_ID)), 1)
        self.assertEqual(updated.count("### Transkript"), 1)
        self.assertNotIn("Schwaches Modell.", updated)
        self.assertIn("Starkes Modell versteht mehr.", updated)

    def test_an_empty_first_result_can_be_filled_later(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, ""]), encoding="utf-8")
        self.apply("")
        updated = self.apply("Beim zweiten Versuch verstanden.")
        self.assertEqual(updated.count("### Transkript"), 1)
        self.assertIn("Beim zweiten Versuch verstanden.", updated)

    def test_every_rewrite_keeps_a_backup(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, ""]), encoding="utf-8")
        self.apply("Erste Fassung.")
        self.apply("Zweite Fassung.")
        self.assertEqual(len(list((self.journal.parent / "_Backup").glob("*.bak"))), 2)

    def test_an_unchanged_transcript_writes_no_backup(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, ""]), encoding="utf-8")
        self.apply("Gleich.")
        self.apply("Gleich.")
        self.assertEqual(len(list((self.journal.parent / "_Backup").glob("*.bak"))), 1)

    def test_a_journal_without_the_marker_is_left_alone(self):
        self.journal.write_text("- 10:34:36\n\nNur Text.\n", encoding="utf-8")
        self.assertEqual(self.apply("Egal."), "- 10:34:36\n\nNur Text.\n")


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patch = mock.patch.object(whisper_scheduler, "DATA_DIR", self.data)
        self.patch.start()
        self.directory = self.data / "u1" / "2026" / "08" / "24" / "media" / "Sprachi"
        self.directory.mkdir(parents=True)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def sidecar(self, media_id, status):
        path = self.directory / f"2026-08-24_10-00-00_{media_id}_metadata.json"
        path.write_text(json.dumps({
            "id": media_id, "user_id": "u1", "type": "audio", "status": "ready",
            "transcription_status": status,
        }), encoding="utf-8")

    def ids(self, **kwargs):
        return sorted(item["id"] for _, item in whisper_scheduler.pending(**kwargs))

    def test_the_daily_run_skips_finished_recordings(self):
        self.sidecar(MEDIA_ID, "completed")
        self.sidecar(OTHER_ID, "error")
        self.assertEqual(self.ids(), [OTHER_ID])

    def test_a_requested_recording_is_redone_although_it_is_finished(self):
        self.sidecar(MEDIA_ID, "completed")
        self.sidecar(OTHER_ID, "error")
        self.assertEqual(self.ids(media_filter=MEDIA_ID), [MEDIA_ID])

    def test_a_foreign_user_is_never_included(self):
        self.sidecar(MEDIA_ID, "pending")
        self.assertEqual(self.ids(user_filter="u2"), [])


class ManualTriggerTests(unittest.TestCase):
    def test_a_valid_media_id_limits_the_run(self):
        self.assertEqual(whisper_scheduler._requested_media({"media_id": MEDIA_ID}), MEDIA_ID)

    def test_a_bogus_media_id_widens_to_the_normal_run(self):
        self.assertIsNone(whisper_scheduler._requested_media({"media_id": "../../etc/passwd"}))
        self.assertIsNone(whisper_scheduler._requested_media({}))


if __name__ == "__main__":
    unittest.main()
