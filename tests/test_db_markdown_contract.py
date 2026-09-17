"""Golden tests for the Phase-0D lossless Markdown parser/renderer contract."""

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))

from markdown import ParseStatus, UnsafeRenderError, parse_document, render_clean, render_preserving  # noqa: E402


FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures" / "db_migration"


class MarkdownContractTests(unittest.TestCase):
    def read_fixture(self, name):
        with (FIXTURE_DIR / name).open(encoding="utf-8", newline="") as fixture:
            return fixture.read()

    def test_every_markdown_fixture_has_a_lossless_lf_roundtrip(self):
        for path in sorted(FIXTURE_DIR.glob("*.md")):
            if path.name == "README.md":
                continue
            with self.subTest(fixture=path.name):
                with path.open(encoding="utf-8", newline="") as fixture:
                    raw = fixture.read()
                document = parse_document(raw)
                self.assertEqual(render_preserving(document), raw.replace("\r\n", "\n").replace("\r", "\n"))

    def test_clean_day_journal_extracts_blocks_tags_and_relative_media(self):
        document = parse_document(self.read_fixture("current-day-journal.md"))

        self.assertEqual(document.status, ParseStatus.CLEAN)
        self.assertEqual([block.anchor for block in document.blocks], [
            "2026-08-11T09:15:00+02:00", "2026-08-11T09:16:00+02:00",
        ])
        self.assertEqual(document.tags, ("projekt",))
        self.assertEqual(document.media_links, ("media/Fotos/synthetisch.jpg",))
        self.assertNotIn("jt:", render_clean(document))

    def test_duplicate_anchors_are_preserved_but_warned_not_assigned_an_identity(self):
        document = parse_document(self.read_fixture("edge-cases-crlf.md"))

        self.assertEqual(document.status, ParseStatus.CLEAN)
        self.assertIn("doppelte Zeitanker", " ".join(document.warnings))
        self.assertEqual(render_clean(document), self.read_fixture("edge-cases-crlf.md").replace("\r\n", "\n"))

    def test_valid_legacy_metadata_is_classified_but_never_removed_by_the_spike(self):
        document = parse_document(self.read_fixture("agent-session-valid.md"))

        self.assertEqual(document.status, ParseStatus.LEGACY_METADATA)
        self.assertTrue(all(marker.valid for marker in document.legacy_markers))
        self.assertIn("Sichtbare synthetische Agentenantwort.", render_preserving(document))
        with self.assertRaises(UnsafeRenderError):
            render_clean(document)

    def test_malformed_and_unknown_markers_block_clean_rendering(self):
        for name in ("agent-session-malformed.md", "hashtag-footer-malformed.md", "unknown-marker.md"):
            with self.subTest(fixture=name):
                document = parse_document(self.read_fixture(name))
                self.assertEqual(document.status, ParseStatus.BLOCKED)
                with self.assertRaises(UnsafeRenderError):
                    render_clean(document)

    def test_normal_html_comment_is_not_a_jtracker_marker(self):
        document = parse_document(self.read_fixture("normal-comment.md"))

        self.assertEqual(document.status, ParseStatus.CLEAN)
        self.assertIn("Redaktionshinweis", render_clean(document))

    def test_generated_unicode_blocks_retain_their_source_order(self):
        source = "# Journal\n\n" + "\n___\n\n".join(
            "## Eintrag %d | Datum & Uhrzeit: 2026-08-11T%02d:00:00+02:00\n\n%s" %
            (index, index, "Äöü ß – Eintrag %d" % index)
            for index in range(10)
        )
        document = parse_document(source)
        self.assertEqual([block.label for block in document.blocks], ["Eintrag %d" % index for index in range(10)])
        self.assertEqual(render_preserving(document), source)


if __name__ == "__main__":
    unittest.main()
