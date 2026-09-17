"""Transcript blocks must survive both historic journal layouts unchanged."""

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import transcript_blocks  # noqa: E402


MEDIA_ID = "e4f71c48-e5d3-4797-ab39-6fd64cef113e"
MARKER = (
    '<!-- jt:media {"id":"%s","type":"audio","captured_at":"2026-08-24T08:34:36+00:00",'
    '"mime_type":"audio/mp4","media_path":"2026/08/24/media/Sprachi/x_audio.m4a",'
    '"sha256":"cad0","original_filename":null,"title":null} -->' % MEDIA_ID
)
LABEL = "Sprachnachricht | Aufnahmezeit: 2026-08-24T08:34:36+00:00"


def spaced_entry(text):
    """The layout `write_sessions.media_markdown` produces (blank-line separated)."""
    return "\n".join([
        "- 10:34:36", "", "Kurzer Gedanke.", "", MARKER, "", LABEL, "",
        transcript_blocks.transcript_marker(MEDIA_ID), "", transcript_blocks.HEADING, "", text, "",
        "~~24.08. 10:35:02~~", "",
    ])


def packed_entry(text):
    """The layout the nightly worker used to write (three adjacent lines)."""
    return "\n".join([
        "- 10:34:36", "", "Kurzer Gedanke.", "", MARKER,
        transcript_blocks.transcript_marker(MEDIA_ID),
        "### Transkript #Sprachnachricht #Transkription", text, LABEL,
        "~~24.08. 10:35:02~~", "",
    ])


class FindBlockTests(unittest.TestCase):
    def test_spaced_layout_covers_marker_heading_and_text(self):
        lines = spaced_entry("Altes Transkript.").split("\n")
        start, end = transcript_blocks.find_block(lines, MEDIA_ID)
        self.assertEqual(lines[start], transcript_blocks.transcript_marker(MEDIA_ID))
        self.assertEqual(lines[start:end][-1], "Altes Transkript.")
        self.assertEqual(lines[end], "")

    def test_packed_layout_stops_before_the_media_label(self):
        lines = packed_entry("Altes Transkript.").split("\n")
        start, end = transcript_blocks.find_block(lines, MEDIA_ID)
        self.assertEqual(lines[start:end][-1], "Altes Transkript.")
        self.assertEqual(lines[end], LABEL)

    def test_empty_transcript_block_is_found_without_swallowing_the_entry(self):
        lines = spaced_entry("").split("\n")
        start, end = transcript_blocks.find_block(lines, MEDIA_ID)
        # The blank line below the heading belongs to the block; the text slot
        # itself is empty, which is exactly what a re-run has to be able to fill.
        self.assertEqual(lines[start:end], [
            transcript_blocks.transcript_marker(MEDIA_ID), "", transcript_blocks.HEADING, "",
        ])

    def test_other_media_ids_are_not_matched(self):
        lines = spaced_entry("Altes Transkript.").split("\n")
        self.assertIsNone(transcript_blocks.find_block(lines, "11111111-2222-3333-4444-555555555555"))

    def test_invalid_media_id_is_rejected(self):
        with self.assertRaises(ValueError):
            transcript_blocks.transcript_marker('x" --><script>')


class UpsertTests(unittest.TestCase):
    def test_spaced_layout_is_replaced_in_place(self):
        updated, changed = transcript_blocks.upsert(spaced_entry("Zu schwach."), MEDIA_ID, "Jetzt korrekt.")
        self.assertTrue(changed)
        self.assertNotIn("Zu schwach.", updated)
        self.assertIn("Jetzt korrekt.", updated)
        self.assertEqual(updated.count(transcript_blocks.transcript_marker(MEDIA_ID)), 1)
        self.assertIn(MARKER, updated)
        self.assertIn("~~24.08. 10:35:02~~", updated)

    def test_packed_layout_is_replaced_and_keeps_the_label(self):
        updated, changed = transcript_blocks.upsert(packed_entry("Zu schwach."), MEDIA_ID, "Jetzt korrekt.")
        self.assertTrue(changed)
        self.assertNotIn("Zu schwach.", updated)
        self.assertIn(LABEL, updated)
        self.assertEqual(updated.count("### Transkript"), 1)

    def test_empty_transcript_is_filled_on_the_second_run(self):
        updated, changed = transcript_blocks.upsert(spaced_entry(""), MEDIA_ID, "Beim zweiten Versuch verstanden.")
        self.assertTrue(changed)
        self.assertIn("Beim zweiten Versuch verstanden.", updated)
        self.assertEqual(updated.count(transcript_blocks.transcript_marker(MEDIA_ID)), 1)

    def test_identical_text_reports_no_change(self):
        content = spaced_entry("Unverändert.")
        updated, changed = transcript_blocks.upsert(content, MEDIA_ID, "Unverändert.")
        self.assertFalse(changed)
        self.assertEqual(updated, content)

    def test_missing_block_is_inserted_behind_the_media_label(self):
        content = "\n".join(["- 10:34:36", "", "Kurzer Gedanke.", "", MARKER, "", LABEL, "", "~~24.08. 10:35:02~~", ""])
        updated, changed = transcript_blocks.upsert(content, MEDIA_ID, "Frisch transkribiert.")
        self.assertTrue(changed)
        lines = updated.split("\n")
        self.assertLess(lines.index(LABEL), lines.index(transcript_blocks.transcript_marker(MEDIA_ID)))
        self.assertIn("Frisch transkribiert.", updated)

    def test_unknown_media_marker_leaves_the_journal_untouched(self):
        content = "- 10:34:36\n\nNur Text.\n"
        updated, changed = transcript_blocks.upsert(content, MEDIA_ID, "Egal.")
        self.assertFalse(changed)
        self.assertEqual(updated, content)

    def test_trailing_newline_is_preserved(self):
        updated, _ = transcript_blocks.upsert(spaced_entry("Alt."), MEDIA_ID, "Neu.")
        self.assertTrue(updated.endswith("\n"))

    def test_a_second_media_block_is_not_disturbed(self):
        other = MARKER.replace(MEDIA_ID, "11111111-2222-3333-4444-555555555555")
        content = spaced_entry("Alt.") + "\n".join([
            other, "", LABEL, "",
            transcript_blocks.transcript_marker("11111111-2222-3333-4444-555555555555"), "",
            transcript_blocks.HEADING, "", "Zweites Transkript.", "",
        ])
        updated, changed = transcript_blocks.upsert(content, MEDIA_ID, "Neu.")
        self.assertTrue(changed)
        self.assertIn("Zweites Transkript.", updated)
        self.assertIn("Neu.", updated)


class HeadingTests(unittest.TestCase):
    """Mehrere Sprachnachrichten in einem Eintrag muessen zuordenbar bleiben."""

    def test_a_lone_recording_keeps_the_plain_heading(self):
        self.assertEqual(transcript_blocks.heading(), transcript_blocks.HEADING)

    def test_a_title_names_the_recording(self):
        heading = transcript_blocks.heading("Einkauf besprochen")
        self.assertTrue(heading.startswith("### Transkript: Einkauf besprochen "))
        self.assertIn(transcript_blocks.HEADING_TAGS, heading)

    def test_without_a_title_the_position_numbers_it(self):
        self.assertEqual(
            transcript_blocks.heading(None, 2),
            "### Transkript 2 " + transcript_blocks.HEADING_TAGS,
        )

    def test_a_title_wins_over_the_position(self):
        self.assertIn("Transkript: Abends", transcript_blocks.heading("Abends", 3))

    def test_a_multiline_title_stays_one_heading_line(self):
        self.assertEqual(transcript_blocks.heading("Zwei\nZeilen").count("\n"), 0)


class AudioPositionTests(unittest.TestCase):
    def entry(self, *media_ids):
        lines = ["___", "", "- 10:34:36", ""]
        for media_id in media_ids:
            lines.extend([MARKER.replace(MEDIA_ID, media_id), "", LABEL, ""])
        return "\n".join(lines)

    def test_numbering_follows_the_order_inside_one_entry(self):
        content = self.entry(MEDIA_ID, "11111111-2222-3333-4444-555555555555")
        positions = transcript_blocks.audio_positions(content)
        self.assertEqual(positions[MEDIA_ID], (1, 2))
        self.assertEqual(positions["11111111-2222-3333-4444-555555555555"], (2, 2))

    def test_numbering_restarts_in_the_next_entry(self):
        content = self.entry(MEDIA_ID) + self.entry("11111111-2222-3333-4444-555555555555")
        positions = transcript_blocks.audio_positions(content)
        self.assertEqual(positions[MEDIA_ID], (1, 1))
        self.assertEqual(positions["11111111-2222-3333-4444-555555555555"], (1, 1))

    def test_upsert_numbers_only_when_the_entry_holds_several_recordings(self):
        single, _ = transcript_blocks.upsert(self.entry(MEDIA_ID), MEDIA_ID, "Allein.")
        self.assertIn(transcript_blocks.HEADING, single)
        content = self.entry(MEDIA_ID, "11111111-2222-3333-4444-555555555555")
        several, _ = transcript_blocks.upsert(content, "11111111-2222-3333-4444-555555555555", "Zweite.")
        self.assertIn("### Transkript 2 ", several)

    def test_upsert_prefers_the_recording_title(self):
        updated, _ = transcript_blocks.upsert(self.entry(MEDIA_ID), MEDIA_ID, "Text.", "Arzttermin")
        self.assertIn("### Transkript: Arzttermin ", updated)


class TranscriptTextTests(unittest.TestCase):
    def test_reads_back_both_layouts(self):
        self.assertEqual(transcript_blocks.transcript_text(spaced_entry("Hallo Welt."), MEDIA_ID), "Hallo Welt.")
        self.assertEqual(transcript_blocks.transcript_text(packed_entry("Hallo Welt."), MEDIA_ID), "Hallo Welt.")

    def test_empty_block_reads_as_empty_string(self):
        self.assertEqual(transcript_blocks.transcript_text(spaced_entry(""), MEDIA_ID), "")

    def test_absent_block_reads_as_none(self):
        self.assertIsNone(transcript_blocks.transcript_text("- 10:00:00\n\nNur Text.\n", MEDIA_ID))


if __name__ == "__main__":
    unittest.main()
