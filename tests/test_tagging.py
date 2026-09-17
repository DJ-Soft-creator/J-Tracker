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

    def test_a_quick_notes_machine_tags_survive_a_footer_rewrite(self):
        source = (
            "# Journal 2026-08-04\n\n___\n\n"
            "- 04.08. 09:15:00\nSchnell notiert #eigen\n~~04.08. 09:16:00~~\n\n___\n"
        )
        tagging.propose_tags("alex", ["eigen", "maschine"])
        with_footer = tagging.refresh_journal_footer(
            "alex", source, "2026-08-04", {
                "blocks": {"2026-08-04 09:15:00": ["eigen", "maschine"]},
                "ai": {"2026-08-04 09:15:00": ["maschine"]},
                "ai_run": "2026-08-04T10:00:00+00:00",
            },
        )

        rewritten = tagging.refresh_journal_footer("alex", with_footer, "2026-08-04")
        _, footer = tagging.strip_footer(rewritten)

        self.assertEqual(footer["blocks"], {"2026-08-04 09:15:00": ["eigen", "maschine"]})
        self.assertEqual(footer["ai"], {"2026-08-04 09:15:00": ["maschine"]})

    def test_a_footer_rewrite_without_the_day_is_recognised_as_lossy(self):
        """Guards the reason every journal write path passes its date."""
        source = (
            "# Journal 2026-08-04\n\n___\n\n"
            "- 04.08. 09:15:00\nSchnell notiert #eigen\n~~04.08. 09:16:00~~\n\n___\n"
        )

        self.assertEqual(tagging.journal_blocks(source, "2026-08-04")[0]["anchor"], "2026-08-04 09:15:00")
        self.assertEqual(tagging.journal_blocks(source), [])

    def test_footer_carries_approved_and_proposed_tags_but_not_blocked_ones(self):
        source = (
            "# Journal 2026-08-04\n\n___\n\n"
            "## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\n#Focus #Fresh #Ignored\n\n___\n"
        )
        tagging.propose_tags("alex", ["Focus", "Ignored"])
        tagging.update_catalog("alex", False, "approve", "Focus")
        tagging.update_catalog("alex", False, "block", "Ignored")

        refreshed = tagging.refresh_journal_footer("alex", source)

        body, footer = tagging.strip_footer(refreshed)
        self.assertIn("#Ignored", body)
        self.assertEqual(footer, {"blocks": {"2026-08-04 12:00:00": ["focus", "fresh"]}})

    def test_a_brand_new_tag_reaches_the_index_without_being_approved_first(self):
        path = self.root / "alex/2026/08/04/Journal_2026-08-04.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "# Journal 2026-08-04\n\n___\n\n"
            "## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\n#Fresh\n\n___\n",
            encoding="utf-8",
        )

        tagging.rebuild_index(["alex"])

        self.assertEqual(len(tagging.references_for_tags("alex", ["fresh"])), 1)
        self.assertIn("fresh", tagging.read_catalog("alex")["proposals"])

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

    def test_journal_blocks_recognises_dated_quick_note_timestamp(self):
        source = (
            "___\n\n"
            "- 04.08. 14:59:44\n"
            "#Focus\n"
            "~~04.08. 15:02:10~~\n"
            "\n"
            "___\n"
        )

        blocks = tagging.journal_blocks(source, "2026-08-04")

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["anchor"], "2026-08-04 14:59:44")
        self.assertEqual(blocks[0]["raw_tags"], ["focus"])

    def test_quick_note_started_before_midnight_keeps_its_own_date(self):
        source = (
            "___\n\n"
            "- 03.08. 23:51:07\n"
            "#Focus\n"
            "~~04.08. 00:04:22~~\n"
            "\n"
            "___\n"
        )

        blocks = tagging.journal_blocks(source, "2026-08-04")

        self.assertEqual([block["anchor"] for block in blocks], ["2026-08-03 23:51:07"])

    def test_quick_note_started_in_the_previous_year_rolls_the_year_back(self):
        source = (
            "___\n\n"
            "- 31.12. 23:58:00\n"
            "~~01.01. 00:03:00~~\n"
            "\n"
            "___\n"
        )

        blocks = tagging.journal_blocks(source, "2027-01-01")

        self.assertEqual([block["anchor"] for block in blocks], ["2026-12-31 23:58:00"])

    def test_dated_quick_note_reaches_the_footer(self):
        tagging.update_catalog("alex", False, "approve", "focus")
        source = (
            "# Journal 2026-08-04\n\n___\n\n"
            "- 04.08. 14:59:44\n#Focus\n~~04.08. 15:02:10~~\n\n___\n"
        )

        refreshed = tagging.refresh_journal_footer("alex", source, "2026-08-04")

        _body, footer = tagging.strip_footer(refreshed)
        self.assertEqual(footer, {"blocks": {"2026-08-04 14:59:44": ["focus"]}})

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

    def test_family_tags_are_indexed_from_their_first_rebuild(self):
        """A Family hashtag is searchable right away; approval only curates it."""
        family_file = self.root / "family" / "shared.md"
        family_file.parent.mkdir(parents=True)
        family_file.write_text("# Shared\nA manual #Recipe\n", encoding="utf-8")

        tagging.rebuild_index([])

        self.assertEqual(len(tagging.references_for_tags("alex", ["recipe"])), 1)
        self.assertIn("recipe", tagging.read_catalog(family=True)["proposals"])
        tagging.update_catalog("alex", True, "approve", "recipe")
        tagging.rebuild_index([])
        self.assertEqual(len(tagging.references_for_tags("alex", ["recipe"])), 1)

    def test_a_blocked_family_tag_stays_out_of_the_index(self):
        family_file = self.root / "family" / "shared.md"
        family_file.parent.mkdir(parents=True)
        family_file.write_text("# Shared\nA manual #Recipe\n", encoding="utf-8")
        tagging.rebuild_index([])
        tagging.update_catalog("alex", True, "block", "recipe")

        tagging.rebuild_index([])

        self.assertEqual(tagging.references_for_tags("alex", ["recipe"]), [])

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

    def test_share_workflow_requires_pi_and_keeps_input_policy(self):
        tagging.update_ai_workflow("alex", "save", "ai-share", {
            "agent": "pi", "model": "provider/model", "prompt": "Summarize.",
            "target": "daily_journal", "default_hashtags": ["#wissen", "wissen"],
            "share_inputs": ["url", "file"],
        })
        workflow = tagging.read_catalog("alex")["ai_workflows"]["ai-share"]
        self.assertEqual(workflow["target"], "daily_journal")
        self.assertEqual(workflow["default_hashtags"], ["wissen"])
        self.assertEqual(workflow["share_inputs"], ["url", "file"])
        with self.assertRaisesRegex(ValueError, "Pi agent"):
            tagging.update_ai_workflow("alex", "save", "ai-share", {
                "agent": "opencode", "model": "test", "prompt": "Summarize.",
                "target": "daily_journal",
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

    def test_blocked_proposal_never_returns_even_when_written_again(self):
        tagging.propose_tags("alex", ["Tippfehlar"])
        self.assertIn("tippfehlar", tagging.read_catalog("alex")["proposals"])

        tagging.update_catalog("alex", False, "block", "Tippfehlar")

        catalog = tagging.read_catalog("alex")
        self.assertEqual(catalog["proposals"], [])
        self.assertEqual(catalog["blocked"], ["tippfehlar"])
        tagging.propose_tags("alex", ["tippfehlar", "keeper"])
        catalog = tagging.read_catalog("alex")
        self.assertEqual(catalog["proposals"], ["keeper"])
        self.assertEqual(catalog["blocked"], ["tippfehlar"])

    def test_unblock_offers_the_hashtag_again_without_a_new_journal_write(self):
        tagging.propose_tags("alex", ["idee"])
        tagging.update_catalog("alex", False, "block", "idee")

        tagging.update_catalog("alex", False, "unblock", "idee")

        catalog = tagging.read_catalog("alex")
        self.assertEqual(catalog["proposals"], ["idee"])
        self.assertEqual(catalog["blocked"], [])
        with self.assertRaisesRegex(ValueError, "Ignorierliste"):
            tagging.update_catalog("alex", False, "unblock", "idee")

    def test_blocking_an_approved_hashtag_is_refused(self):
        tagging.update_catalog("alex", False, "approve", "focus")

        with self.assertRaisesRegex(ValueError, "zuerst entfernt"):
            tagging.update_catalog("alex", False, "block", "focus")

        self.assertEqual(tagging.read_catalog("alex")["blocked"], [])

    def test_approving_a_blocked_hashtag_clears_the_ignore_entry(self):
        tagging.propose_tags("alex", ["thema"])
        tagging.update_catalog("alex", False, "block", "thema")

        tagging.update_catalog("alex", False, "approve", "thema")

        catalog = tagging.read_catalog("alex")
        self.assertEqual(catalog["canonical"], ["thema"])
        self.assertEqual(catalog["blocked"], [])

    def test_catalog_written_before_the_ignore_list_stays_readable(self):
        path = self.root / "alex" / "hashtag_catalog.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "version": 1, "canonical": ["focus"], "aliases": {}, "proposals": ["neu"],
            "ai_workflows": {}, "knowledge_sources": {},
        }), encoding="utf-8")

        catalog = tagging.read_catalog("alex")

        self.assertEqual(catalog["canonical"], ["focus"])
        self.assertEqual(catalog["blocked"], [])
        tagging.update_catalog("alex", False, "block", "neu")
        self.assertEqual(tagging.read_catalog("alex")["blocked"], ["neu"])

    # Legacy journals: written before entries carried a "Datum & Uhrzeit" line.
    # The two fallbacks below only ever see a day the current rules leave with
    # no block at all, which is what keeps them away from journals in use.

    LEGACY_DAY = (
        "# Journal 2026-01-01\n"
        "\n"
        "15:52:56 \n"
        "\n"
        "___\n"
        "Thema: Backup! Time:15:53:02 \n"
        "\n"
        "___\n"
        "\n"
        "___\n"
        "## W Mode on\n"
        "**Time**: 19:24:43\n"
        "\n"
        "___\n"
    )

    def test_legacy_clock_stamps_anchor_blocks_on_the_journal_day(self):
        blocks = tagging.journal_blocks(self.LEGACY_DAY, "2026-01-01")

        self.assertEqual(
            [block["anchor"] for block in blocks],
            ["2026-01-01 15:52:56", "2026-01-01 15:53:02", "2026-01-01 19:24:43"],
        )
        self.assertNotIn("scope", blocks[0])

    def test_legacy_entry_before_the_first_separator_still_counts(self):
        """Old days opened with an entry that no separator preceded."""
        blocks = tagging.journal_blocks(self.LEGACY_DAY, "2026-01-01")

        self.assertEqual(blocks[0]["text"].strip(), "15:52:56")

    def test_legacy_blocks_sharing_a_clock_are_dropped_rather_than_guessed(self):
        source = (
            "___\nTime: 10:00:00\nA\n___\nTime: 10:00:00\nB\n___\nTime: 11:00:00\nC\n___\n"
        )

        blocks = tagging.journal_blocks(source, "2026-01-01")

        self.assertEqual([block["anchor"] for block in blocks], ["2026-01-01 11:00:00"])

    def test_a_day_without_any_usable_time_becomes_one_block(self):
        source = (
            "# Journal 2026-01-02\n\n___\nEinfach Text #urlaub\n\n___\nNoch etwas\n___\n"
        )

        blocks = tagging.journal_blocks(source, "2026-01-02")

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["anchor"], "2026-01-02 00:00:00")
        self.assertEqual(blocks[0]["scope"], "day")
        self.assertEqual(blocks[0]["raw_tags"], ["urlaub"])
        self.assertIn("Noch etwas", blocks[0]["text"])

    def test_a_day_with_nothing_but_separators_gets_no_fallback_block(self):
        self.assertEqual(tagging.journal_blocks("# Journal 2026-01-02\n\n___\n\n___\n", "2026-01-02"), [])
        self.assertEqual(tagging.journal_blocks("", "2026-01-02"), [])

    def test_the_current_format_never_reaches_the_legacy_fallbacks(self):
        """A journal in use must not be reinterpreted as a legacy day."""
        source = (
            "# Journal 2026-08-25\n\n___\n\n"
            "## Thema | Datum & Uhrzeit: 2026-08-25 10:11:12\nText mit Time: 09:00:00\n\n___\n"
        )

        blocks = tagging.journal_blocks(source, "2026-08-25")

        self.assertEqual([block["anchor"] for block in blocks], ["2026-08-25 10:11:12"])

    def test_a_day_level_hashtag_survives_the_next_footer_rewrite(self):
        """The reason the fallback lives in journal_blocks and not in the tagging run.

        rebuild_index and render_footer both drop a footer anchor that resolves
        to no block, so a day-level assignment written by the model would be
        gone again after the very next save.
        """
        tagging.propose_tags("alex", ["maschine"])
        source = "# Journal 2026-01-02\n\n___\nEinfach Text\n___\n"
        with_footer = tagging.refresh_journal_footer(
            "alex", source, "2026-01-02", {
                "blocks": {"2026-01-02 00:00:00": ["maschine"]},
                "ai": {"2026-01-02 00:00:00": ["maschine"]},
                "ai_run": "2026-01-02T10:00:00+00:00",
            },
        )

        rewritten = tagging.refresh_journal_footer("alex", with_footer, "2026-01-02")
        _, footer = tagging.strip_footer(rewritten)

        self.assertEqual(footer["blocks"], {"2026-01-02 00:00:00": ["maschine"]})
        self.assertEqual(footer["ai"], {"2026-01-02 00:00:00": ["maschine"]})


if __name__ == "__main__":
    unittest.main()
