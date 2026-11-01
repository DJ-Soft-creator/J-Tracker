"""Ausgabemodi der Journal-Analyse: Journal-Eintrag und Familien-Notiz."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import journal_analysis_output as output  # noqa: E402
import tagging  # noqa: E402


FOOTER = (tagging.FOOTER_START + "\n<!--\n{\n  \"blocks\": {}\n}\n-->\n"
          + tagging.FOOTER_END + "\n")


class BlockTests(unittest.TestCase):
    def test_render_block_carries_job_and_date_markers(self):
        block = output.render_block("job-1", "Pendel", date(2026, 9, 23), "Text")
        self.assertIn('job="job-1"', block)
        self.assertIn('date="2026-09-23"', block)
        self.assertIn("## Journal-Analyse: Pendel", block)
        self.assertTrue(block.rstrip().endswith(output.ANALYSIS_END))

    def test_strip_block_removes_only_the_matching_job_and_day(self):
        first = output.render_block("job-1", "A", date(2026, 9, 23), "eins")
        other = output.render_block("job-2", "B", date(2026, 9, 23), "zwei")
        other_day = output.render_block("job-1", "A", date(2026, 9, 22), "drei")
        content = f"Vor\n\n{first}{other}{other_day}Nach"
        stripped = output.strip_block(content, "job-1", date(2026, 9, 23))
        self.assertNotIn("eins", stripped)
        self.assertIn("zwei", stripped)
        self.assertIn("drei", stripped)
        self.assertIn("Nach", stripped)

    def test_prepare_journal_body_replaces_and_keeps_footer_separate(self):
        old = output.render_block("job-1", "A", date(2026, 9, 23), "alt")
        current = f"Bestehender Text\n\n{old}\n{FOOTER}"
        updated, footer = output.prepare_journal_body(current, "job-1", "A", date(2026, 9, 23), "neu")
        self.assertIn("Bestehender Text", updated)
        self.assertIn("neu", updated)
        self.assertNotIn("alt", updated)
        self.assertNotIn("jt:hashtag-index", updated)
        self.assertEqual(footer, {"blocks": {}})


class JournalWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_write_journal_entry_uses_the_journal_path_and_footer_refresh(self):
        captured = {}

        def fake_writer(path, updater, user_id=None):
            captured["path"] = path
            captured["content"] = updater("Alter Inhalt\n")
            return captured["content"]

        def fake_refresh(user_id, body, day, footer):
            return body + "FOOTER\n"

        output.write_journal_entry("david", date(2026, 9, 23),
                                    {"id": "job-1", "name": "Pendel"}, "Ergebnis",
                                    data_root=self.root, writer=fake_writer,
                                    refresh_footer=fake_refresh)
        self.assertEqual(captured["path"].name, "Journal_2026-09-23.md")
        self.assertIn("Alter Inhalt", captured["content"])
        self.assertIn("Ergebnis", captured["content"])
        self.assertTrue(captured["content"].endswith("FOOTER\n"))

    def test_empty_result_writes_nothing(self):
        self.assertIsNone(output.write_journal_entry(
            "david", date(2026, 9, 23), {"id": "job-1"}, "   ", data_root=self.root))


class FamilyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.notes = self.root / "family" / "Notizen.md"
        self.notes.parent.mkdir(parents=True)
        self.notes.write_text("Bestehende Notiz\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_family_target_accepts_relative_and_prefixed_paths(self):
        self.assertEqual(output.family_document_path("Notizen.md", self.root), self.notes)
        self.assertEqual(output.family_document_path("familie/Notizen.md", self.root), self.notes)

    def test_family_target_rejects_traversal_and_non_markdown(self):
        for bad in ("../secret.md", "/etc/passwd", "Notizen.txt", "gibt-es-nicht.md"):
            with self.assertRaises(ValueError):
                output.family_document_path(bad, self.root)

    def test_family_note_is_appended_and_replaced_idempotently(self):
        job = {"id": "job-1", "name": "Pendel"}
        output.write_family_note("Notizen.md", job, date(2026, 9, 23), "Erster Lauf",
                                 data_root=self.root)
        output.write_family_note("Notizen.md", job, date(2026, 9, 23), "Zweiter Lauf",
                                 data_root=self.root)
        text = self.notes.read_text(encoding="utf-8")
        self.assertIn("Bestehende Notiz", text)
        self.assertEqual(text.count(output.ANALYSIS_START), 1)
        self.assertIn("Zweiter Lauf", text)
        self.assertNotIn("Erster Lauf", text)


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_notes_entry_is_written_and_replaced_per_job_and_period(self):
        job = {"id": "job-1", "name": "Pendel", "slug": "pendel", "notes_target": None}
        days = [date(2026, 9, 11), date(2026, 9, 15)]
        path = output.write_notes_entry("david", job, days, "Zusammenfassung", data_root=self.root)
        self.assertEqual(path.name, "pendel.md")
        output.write_notes_entry("david", job, days, "Neue Zusammenfassung", data_root=self.root)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("jt:analysis-run:start"), 1)
        self.assertIn("Neue Zusammenfassung", text)

    def test_explicit_notes_target_is_used(self):
        job = {"id": "job-1", "name": "Pendel", "slug": "pendel", "notes_target": "berichte/check.md"}
        path = output.write_notes_entry("david", job, [date(2026, 9, 15)], "Text", data_root=self.root)
        self.assertEqual(path, self.root / "david" / "notes" / "berichte" / "check.md")

    def test_notes_target_rejects_traversal_and_non_markdown(self):
        for bad in ("../secret.md", "/etc/passwd", "x.txt"):
            with self.assertRaises(ValueError):
                output.write_notes_entry("david", {"id": "j", "slug": "s", "notes_target": bad},
                                         [date(2026, 9, 15)], "Text", data_root=self.root)


if __name__ == "__main__":
    unittest.main()