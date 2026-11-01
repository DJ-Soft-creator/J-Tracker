"""Reine Hilfsfunktionen des Terminal-Clients (ohne Textual, ohne Server)."""

import importlib.util
import unittest
from datetime import date
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "tools" / "journl-tui" / "journl_core.py"
_SPEC = importlib.util.spec_from_file_location("journl_core", _PATH)
tui = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tui)


class JournlTuiHelpersTest(unittest.TestCase):
    def test_only_simple_journal_templates_for_the_user(self):
        config = {
            "current_user": {"id": "u1"},
            "templates": [
                {"id": "schnell", "type": "simple"},
                {"id": "form", "type": "form"},
                {"id": "family", "type": "simple", "write_to_journal": False},
                {"id": "liste", "type": "simple", "target_file": "family/einkaufsliste.md"},
                {"id": "fremd", "type": "simple", "assigned_users": ["u2"]},
                {"id": "meins", "type": "simple", "assigned_users": ["u1"]},
            ],
        }
        self.assertEqual([t["id"] for t in tui.writable_templates(config)], ["schnell", "meins"])

    def test_template_tags_keep_only_valid_hashtags(self):
        template = {"hashtag_template": True, "hashtags": ["#ai-agent", "kaputt", "#ai-agent", "#Dok"]}
        self.assertEqual(tui.template_tags(template), ["#ai-agent", "#Dok"])
        self.assertEqual(tui.template_tags({"hashtags": ["#x"]}), [])

    def test_strip_managed_tags_mirrors_web_editor(self):
        self.assertEqual(tui.strip_managed_tags("#a #b\n\nText", ["#a", "#b"]), "Text")
        self.assertEqual(tui.strip_managed_tags("#a eigener\nText", ["#a"]), "eigener\nText")
        self.assertEqual(tui.strip_managed_tags("Text #a", []), "Text #a")

    def test_entry_summary_for_quick_and_heading_entries(self):
        quick = "- 24.10. 15:27:17\nErster Gedanke\n~~24.10. 15:28:00~~"
        self.assertEqual(tui.entry_summary(quick), ("15:27", ["Erster Gedanke"]))
        heading = "## Idee | Datum & Uhrzeit: 2026-10-24 09:05:00\nInhalt"
        self.assertEqual(tui.entry_summary(heading), ("09:05", ["## Idee", "Inhalt"]))
        self.assertEqual(tui.entry_summary("Ohne Zeit"), ("", ["Ohne Zeit"]))

    def test_hashtag_prefix_before_cursor(self):
        self.assertEqual(tui.HASHTAG_BEFORE_CURSOR.search("Text #pro").group(1), "pro")
        self.assertEqual(tui.HASHTAG_BEFORE_CURSOR.search("#").group(1), "")
        self.assertIsNone(tui.HASHTAG_BEFORE_CURSOR.search("a#b"))


    def test_origin_line_only_when_enabled_and_parser_safe(self):
        settings = {"origin_enabled": True, "origin_label": "Mac_Book #privat *x*"}
        self.assertEqual(tui.with_origin("Text\n", settings), "Text\n*Gesendet von: Mac Book privat x*")
        self.assertEqual(tui.with_origin("Text", {"origin_label": "Mac"}), "Text")
        self.assertEqual(tui.with_origin("  ", settings), "  ")
        self.assertTrue(tui.origin_line({"origin_enabled": True}).endswith("(TUI)*"))


class JournlTuiSearchFilterTest(unittest.TestCase):
    """Dreimengenmodell wie brain-tag-filters.js: Themen ODER, Verfeinern UND, Ausschluss."""

    def test_sets_stay_disjoint(self):
        tags = tui.TagFilter()
        tags.include("a")
        tags.exclude("a")
        self.assertEqual((tags.selected, tags.excluded), (set(), {"a"}))
        tags.include("a")
        self.assertEqual((tags.selected, tags.excluded), ({"a"}, set()))
        tags.include("a")
        self.assertFalse(tags.active())

    def test_theme_turns_refinements_into_themes(self):
        tags = tui.TagFilter()
        tags.include("garten")
        tags.exclude("urlaub")
        tags.theme("finanzen")
        self.assertEqual(tags.any, {"garten", "finanzen"})
        self.assertEqual(tags.selected, set())
        self.assertEqual(tags.excluded, {"urlaub"})
        self.assertEqual(tags.summary(), "#finanzen oder #garten  ohne #urlaub")
        tags.theme("garten")
        self.assertEqual(tags.any, {"finanzen"})

    def test_search_params_match_brain_client(self):
        tags = tui.TagFilter()
        tags.theme("a")
        tags.include("b")
        tags.exclude("c")
        params = tui.search_params(" urlaub ", tags, days=7, kind="journal", order="oldest",
                                   offset=100, today=date(2026, 10, 24))
        self.assertEqual(params, [
            ("q", "urlaub"), ("any_tags", "a"), ("tags", "b"), ("exclude_tags", "c"),
            ("start_date", "2026-10-17"), ("kind", "journal"), ("order", "oldest"),
            ("limit", "100"), ("offset", "100"),
        ])
        self.assertEqual(tui.search_params("", tui.TagFilter())[0], ("order", "newest"))

    def test_facet_rows_put_active_filters_first(self):
        tags = tui.TagFilter()
        tags.exclude("weg")
        facets = [{"name": "viel", "count": 9}, {"name": "wenig", "count": 1}, {"name": "leer", "count": 0}]
        self.assertEqual(tui.facet_rows(facets, tags), [("weg", 0), ("viel", 9), ("wenig", 1)])
        self.assertEqual(tui.facet_rows(facets, tags, everything=True)[-1], ("leer", 0))
        self.assertEqual(tui.facet_rows(facets, tags, needle="#WEN"), [("wenig", 1)])


if __name__ == "__main__":
    unittest.main()
