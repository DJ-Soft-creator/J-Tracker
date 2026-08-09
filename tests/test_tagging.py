import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))
import tagging


class TaggingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old_data_dir, self.old_family_dir, self.old_index_path = tagging.DATA_DIR, tagging.FAMILY_DIR, tagging.INDEX_PATH
        tagging.DATA_DIR = self.root
        tagging.FAMILY_DIR = self.root / "family"
        tagging.INDEX_PATH = self.root / "indexes" / "hashtag_index.json"
        tagging._snapshot = None

    def tearDown(self):
        tagging.DATA_DIR, tagging.FAMILY_DIR, tagging.INDEX_PATH = self.old_data_dir, self.old_family_dir, self.old_index_path
        tagging._snapshot = None
        self.temp_dir.cleanup()

    def test_footer_contains_only_approved_canonical_tags(self):
        source = (
            "# Journal 2026-08-04\n\n___\n\n"
            "## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\n#Focus #Unapproved\n\n___\n"
        )
        tagging.propose_tags("alex", ["Focus", "Unapproved"])
        tagging.update_catalog("alex", False, "approve", "Focus")
        refreshed = tagging.refresh_journal_footer("alex", source)
        body, footer = tagging.strip_footer(refreshed)
        self.assertIn("#Unapproved", body)
        self.assertEqual(footer, {"blocks": {"2026-08-04 12:00:00": ["focus"]}})

    def test_journal_blocks_recognises_legacy_quick_note_timestamp(self):
        source = (
            "___\n\n"
            "- 14:59:44\n\n"
            "~~14:59:44~~\n"
            "\n"
            "___\n"
        )

        blocks = tagging.journal_blocks(source, "2026-08-04")

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["anchor"], "2026-08-04 14:59:44")

    def test_rebuild_preserves_historical_footer_tags_without_inline_hashtags(self):
        path = self.root / "alex/2026/08/04/Journal_2026-08-04.md"
        path.parent.mkdir(parents=True)
        tagging.update_catalog("alex", False, "approve", "focus")
        source = (
            "# Journal\n\n___\n\n"
            "## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\nNo inline hashtag\n\n___\n"
        )
        catalogs = [tagging.read_catalog("alex"), tagging.read_catalog(family=True)]
        source += "\n" + tagging.render_footer(
            [{"anchor": "2026-08-04 12:00:00", "raw_tags": ["focus"]}],
            catalogs,
        )
        path.write_text(source, encoding="utf-8")

        tagging.rebuild_index(["alex"])

        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer, {"blocks": {"2026-08-04 12:00:00": ["focus"]}})
        self.assertEqual(len(tagging.references_for_tags("alex", ["focus"])), 1)

    def test_append_refresh_can_preserve_detached_footer(self):
        tagging.update_catalog("alex", False, "approve", "focus")
        source = (
            "___\n\n## Existing | Datum & Uhrzeit: 2026-08-04 12:00:00\nText\n\n___\n"
        )
        catalogs = [tagging.read_catalog("alex"), tagging.read_catalog(family=True)]
        content = source + "\n" + tagging.render_footer(
            [{"anchor": "2026-08-04 12:00:00", "raw_tags": ["focus"]}],
            catalogs,
        )
        body, existing_footer = tagging.strip_footer(content)
        body += "\n___\n\n## New | Datum & Uhrzeit: 2026-08-04 13:00:00\nNew\n\n___\n"

        refreshed = tagging.refresh_journal_footer(
            "alex", body, "2026-08-04", existing_footer
        )

        _, footer = tagging.strip_footer(refreshed)
        self.assertEqual(footer, {"blocks": {"2026-08-04 12:00:00": ["focus"]}})

    def test_index_intersects_tags_without_reading_unrelated_documents(self):
        first = self.root / "alex/2026/08/04/Journal_2026-08-04.md"
        second = self.root / "alex/2026/08/05/Journal_2026-08-05.md"
        first.parent.mkdir(parents=True)
        second.parent.mkdir(parents=True)
        tagging.update_catalog("alex", False, "approve", "focus")
        tagging.update_catalog("alex", False, "approve", "work")
        first.write_text("___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\n#focus #work\n\n___\n", encoding="utf-8")
        second.write_text("___\n\n## B | Datum & Uhrzeit: 2026-08-05 12:00:00\n#focus\n\n___\n", encoding="utf-8")
        tagging.rebuild_index(["alex"])
        results = tagging.references_for_tags("alex", ["focus", "work"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["anchor"], "2026-08-04 12:00:00")
        self.assertEqual(json.loads(tagging.INDEX_PATH.read_text(encoding="utf-8"))["version"], 1)

    def test_family_tags_remain_proposals_until_approved(self):
        family_file = self.root / "family" / "shared.md"
        family_file.parent.mkdir(parents=True)
        family_file.write_text("# Shared\nA manual #Recipe\n", encoding="utf-8")
        tagging.rebuild_index([])
        self.assertEqual(tagging.references_for_tags("alex", ["recipe"]), [])
        self.assertIn("recipe", tagging.read_catalog(family=True)["proposals"])
        tagging.update_catalog("alex", True, "approve", "recipe")
        tagging.rebuild_index([])
        self.assertEqual(len(tagging.references_for_tags("alex", ["recipe"])), 1)

    def test_ai_workflow_keeps_selected_context_classification(self):
        tagging.update_ai_workflow("alex", "save", "ai-review", {
            "agent": "opencode",
            "model": "test",
            "prompt": "Review this.",
            "context": "files",
            "classification": "confidential",
            "context_files": ["notes/context.md"],
        })
        workflow = tagging.read_catalog("alex")["ai_workflows"]["ai-review"]
        self.assertEqual(workflow["context"], "files")
        self.assertEqual(workflow["classification"], "confidential")
        self.assertEqual(workflow["context_files"], ["notes/context.md"])
        with self.assertRaisesRegex(ValueError, "classification"):
            tagging.update_ai_workflow("alex", "save", "ai-review", {
                "agent": "opencode", "model": "test", "prompt": "Review this.",
                "classification": "private",
            })

    def test_knowledge_sources_are_scoped_and_reject_unsafe_or_ambiguous_paths(self):
        saved = tagging.update_knowledge_source("alex", False, "save", "Shopping", {"path": "notes/shopping.md"})
        self.assertEqual(saved, {"scope": "personal", "path": "notes/shopping.md", "kind": "reference", "description": ""})
        view = tagging.catalog_view("alex")["knowledge"]
        self.assertEqual(view["personal"]["shopping"], saved)
        self.assertEqual(view["family"], {})
        with self.assertRaisesRegex(ValueError, "relative Markdown"):
            tagging.update_knowledge_source("alex", False, "save", "outside", {"path": "../private.md"})
        with self.assertRaisesRegex(ValueError, "relative Markdown"):
            tagging.update_knowledge_source("alex", False, "save", "draft", {"path": "temp_Eingabe.md"})
        with self.assertRaisesRegex(ValueError, "other scope"):
            tagging.update_knowledge_source("alex", True, "save", "shopping", {"path": "notes/shared.md"})

    def test_write_targets_are_separate_and_support_both_file_policies(self):
        saved = tagging.update_write_target("alex", False, "save", "schreibziel-plan", {
            "path": "projects/plan", "file_policy": "all_regular_files",
        })
        self.assertEqual(saved, {"scope": "personal", "path": "projects/plan", "recursive": True, "file_policy": "all_regular_files"})
        self.assertEqual(tagging.catalog_view("alex")["write_targets"]["personal"]["schreibziel-plan"], saved)
        with self.assertRaisesRegex(ValueError, "schreibziel-"):
            tagging.update_write_target("alex", False, "save", "ziel", {"path": "notes/x"})
        with self.assertRaisesRegex(ValueError, "relativer Ordner"):
            tagging.update_write_target("alex", False, "save", "schreibziel-ausbruch", {"path": "../x"})

    def test_external_write_target_keeps_root_id_and_requires_absolute_linux_path(self):
        saved = tagging.update_write_target("alex", False, "save", "schreibziel-dev", {
            "root_id": "dev-projects", "path": "/docker-storage/Journal-Tracker-DEV/FeatureRequests",
            "file_policy": "markdown_only",
        })
        self.assertEqual(saved["scope"], "host")
        self.assertEqual(saved["root_id"], "dev-projects")
        self.assertEqual(tagging.catalog_view("alex")["write_targets"]["host"]["schreibziel-dev"], saved)
        with self.assertRaisesRegex(ValueError, "absoluter Linux-Pfad"):
            tagging.update_write_target("alex", False, "save", "schreibziel-relativ", {"root_id": "dev-projects", "path": "notes/x"})


if __name__ == "__main__":
    unittest.main()
