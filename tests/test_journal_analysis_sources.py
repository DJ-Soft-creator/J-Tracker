"""Quellen einer Journal-Analyse: Journal plus freigegebene Dateien."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import journal_analysis_sources as sources  # noqa: E402


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.user = "david"
        self.day_dir = self.root / self.user / "2026" / "09" / "23"
        self.day_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_journal_path_matches_the_day_layout(self):
        path = sources.journal_path(self.user, date(2026, 9, 23), self.root)
        self.assertEqual(path.name, "Journal_2026-09-23.md")
        self.assertEqual(path.parent.name, "23")

    def test_collect_sources_reads_the_journal(self):
        (self.day_dir / "Journal_2026-09-23.md").write_text("Heute war ein Tag.", encoding="utf-8")
        collected = sources.collect_sources(self.user, date(2026, 9, 23), [], self.root)
        self.assertEqual(collected["journal"], "Heute war ein Tag.")
        self.assertEqual(collected["extras"], [])

    def test_missing_journal_is_empty_not_an_error(self):
        collected = sources.collect_sources(self.user, date(2026, 9, 23), [], self.root)
        self.assertEqual(collected["journal"], "")

    def test_extra_source_is_relative_to_the_user_root(self):
        extra = self.root / self.user / "Auswertungen" / "check.md"
        extra.parent.mkdir(parents=True)
        extra.write_text("Auswertung", encoding="utf-8")
        collected = sources.collect_sources(self.user, date(2026, 9, 23),
                                            ["Auswertungen/check.md"], self.root)
        self.assertEqual(collected["extras"][0]["text"], "Auswertung")

    def test_family_prefix_reads_from_the_family_folder(self):
        family = self.root / "family" / "Notizen"
        family.mkdir(parents=True)
        (family / "plan.md").write_text("Familienplan", encoding="utf-8")
        collected = sources.collect_sources(self.user, date(2026, 9, 23),
                                            ["familie/Notizen/plan.md"], self.root)
        self.assertEqual(collected["extras"][0]["text"], "Familienplan")
        self.assertEqual(collected["extras"][0]["source"], "familie/Notizen/plan.md")

    def test_path_traversal_outside_the_allowed_roots_is_rejected(self):
        for bad in ("../secret.md", "/etc/passwd", "familie/../../secret.md"):
            with self.assertRaises(ValueError):
                sources.read_extra_source(self.user, bad, self.root)

    def test_missing_extra_source_is_silently_skipped(self):
        collected = sources.collect_sources(self.user, date(2026, 9, 23),
                                            ["gibt-es-nicht.md"], self.root)
        self.assertEqual(collected["extras"], [])

    def test_date_placeholders_pick_the_file_of_the_analysed_day(self):
        tracker = self.root / self.user / "W-Tracker-data" / "2026" / "09" / "23"
        tracker.mkdir(parents=True)
        (tracker / "Weedtracker_2026-09-23.md").write_text("Tageswerte", encoding="utf-8")
        template = "W-Tracker-data/{YYYY}/{MM}/{DD}/Weedtracker_{day}.md"
        collected = sources.collect_sources(self.user, date(2026, 9, 23), [template], self.root)
        self.assertEqual(collected["extras"], [{
            "source": "W-Tracker-data/2026/09/23/Weedtracker_2026-09-23.md",
            "text": "Tageswerte", "per_day": True,
        }])
        other_day = sources.collect_sources(self.user, date(2026, 9, 22), [template], self.root)
        self.assertEqual(other_day["extras"], [])

    def test_normalise_source_strips_leading_slash_and_rejects_unknown_placeholders(self):
        self.assertEqual(sources.normalise_source("/W-Tracker-data/{YYYY}/x_{day}.md"),
                         "W-Tracker-data/{YYYY}/x_{day}.md")
        for bad in ("W-Tracker-data/{JJJJ}/x.md", "../x.md", "/"):
            with self.assertRaises(ValueError):
                sources.normalise_source(bad)

    def test_changed_day_source_counts_but_fixed_source_does_not(self):
        base = {"journal": "Eintrag", "extras": [{"source": "plan.md", "text": "alt"}]}
        stored = sources.content_hash(base)
        # Ohne Tagesquelle bleibt der Hash wie bisher: nur das Journal zaehlt.
        self.assertEqual(stored, sources.content_hash({"journal": "Eintrag", "extras": []}))
        fixed_changed = {"journal": "Eintrag", "extras": [{"source": "plan.md", "text": "neu"}]}
        self.assertFalse(sources.content_changed(stored, fixed_changed))
        day_source = {"source": "W/2026/09/23/w.md", "text": "Werte", "per_day": True}
        with_day = sources.content_hash({"journal": "Eintrag", "extras": [day_source]})
        self.assertTrue(sources.content_changed(stored, {"journal": "Eintrag", "extras": [day_source]}))
        updated = {"journal": "Eintrag", "extras": [{**day_source, "text": "Werte neu"}]}
        self.assertTrue(sources.content_changed(with_day, updated))

    def test_content_hash_changes_with_content(self):
        first = {"journal": "a", "extras": []}
        second = {"journal": "b", "extras": []}
        self.assertNotEqual(sources.content_hash(first), sources.content_hash(second))
        self.assertEqual(sources.content_hash(first), sources.content_hash(dict(first)))
        self.assertEqual(len(sources.content_hash(first)), 64)  # Spalte char(64)

    def test_technical_blocks_and_extras_do_not_count_as_a_change(self):
        stored = sources.content_hash({"journal": "Eintrag\n", "extras": []})
        written_back = {
            "journal": ("Eintrag\n\n<!-- jt:analysis:start job=\"j\" date=\"2026-09-23\" -->\n"
                        "## Journal-Analyse: X\n\nR\n<!-- jt:analysis:end -->\n\n"
                        '<!-- jt:hashtag-index:start schema="1" -->\n<!--\n{}\n-->\n'
                        "<!-- jt:hashtag-index:end -->\n"),
            "extras": [{"source": "familie/x.md", "text": "neu"}],
        }
        self.assertFalse(sources.content_changed(stored, written_back))
        self.assertTrue(sources.content_changed(stored, {"journal": "Eintrag\nNachtrag", "extras": []}))
        # Altes Hash-Format oder keins: nicht als geaendert werten.
        self.assertFalse(sources.content_changed("ab" * 32, {"journal": "anders", "extras": []}))
        self.assertFalse(sources.content_changed(None, {"journal": "anders", "extras": []}))

    def test_empty_day_ignores_footer_and_analysis_blocks(self):
        footer = '<!-- jt:hashtag-index:start schema="1" -->\n<!--\n{}\n-->\n<!-- jt:hashtag-index:end -->\n'
        self.assertTrue(sources.is_empty({"journal": "  \n" + footer, "extras": []}))
        self.assertFalse(sources.is_empty({"journal": footer, "extras": [{"source": "x", "text": "Z"}]}))
        self.assertFalse(sources.is_empty({"journal": "Text", "extras": []}))

    def test_render_sources_includes_journal_and_extras(self):
        text = sources.render_sources({
            "day": "2026-09-23",
            "journal": "Journaltext",
            "extras": [{"source": "x.md", "text": "Zusatz"}],
        })
        self.assertIn("Journal 2026-09-23", text)
        self.assertIn("Quelle: x.md", text)


if __name__ == "__main__":
    unittest.main()