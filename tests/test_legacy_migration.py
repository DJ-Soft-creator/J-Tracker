import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from legacy_migration import (assess_legacy_migration, extract_legacy_metadata,
                              render_without_legacy_metadata, summary)  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "db_migration"


class LegacyMigrationTests(unittest.TestCase):
    def read(self, name):
        return (FIXTURES / name).read_text(encoding="utf-8")

    def test_clean_journal_is_the_only_currently_ready_class(self):
        decision = assess_legacy_migration(self.read("current-day-journal.md"))
        self.assertEqual(decision.status, "ready")
        self.assertEqual(decision.marker_kinds, ())

    def test_valid_metadata_is_ready_for_archive_cleanup(self):
        decision = assess_legacy_migration(self.read("agent-session-valid.md"))
        self.assertEqual(decision.status, "ready_for_archive_cleanup")
        self.assertIn("agent-session", decision.marker_kinds)
        self.assertTrue(decision.visible_content_preserved)
        self.assertIn("archiviert", " ".join(decision.reasons))

    def test_malformed_marker_stays_blocked_without_guessing(self):
        decision = assess_legacy_migration(self.read("hashtag-footer-malformed.md"))
        self.assertEqual(decision.status, "blocked")
        self.assertIn("unbekannte oder beschaedigte", " ".join(decision.reasons))

    def test_summary_is_content_free(self):
        value = summary((assess_legacy_migration(self.read("current-day-journal.md")),
                         assess_legacy_migration(self.read("media-transcript-valid.md"))))
        self.assertEqual(value["by_status"], {"ready": 1, "ready_for_archive_cleanup": 1})
        self.assertEqual(value["marker_kinds"], {"media": 1, "transcript": 1})

    def test_valid_agent_comments_are_archivable_and_removed_without_losing_answer(self):
        source = self.read("agent-session-valid.md")
        records = extract_legacy_metadata(source)
        rendered = render_without_legacy_metadata(source)
        self.assertEqual([item["kind"] for item in records], [
            "agent-session-config", "agent-session-event", "agent-session",
        ])
        self.assertNotIn("jt:", rendered)
        self.assertIn("Sichtbare Anfrage an den Agenten.", rendered)
        self.assertIn("Sichtbare synthetische Agentenantwort.", rendered)

    def test_malformed_legacy_metadata_is_never_rendered_clean(self):
        with self.assertRaisesRegex(ValueError, "beschädigte"):
            render_without_legacy_metadata(self.read("agent-session-malformed.md"))
