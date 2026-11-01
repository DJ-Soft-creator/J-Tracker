import json
import re
import tempfile
import types
import unittest
from pathlib import Path

import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))
import historical_tagging
import tagging


class HistoricalTaggingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old_data_dir, self.old_family_dir = tagging.DATA_DIR, tagging.FAMILY_DIR
        tagging.DATA_DIR = self.root
        tagging.FAMILY_DIR = self.root / "family"

    def tearDown(self):
        tagging.DATA_DIR, tagging.FAMILY_DIR = self.old_data_dir, self.old_family_dir
        self.temp_dir.cleanup()

    def test_uses_configured_prompts_and_replaces_placeholders(self):
        path = self.root / "alex/2026/08/04/Journal_2026-08-04.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "# Journal\n\n___\n\n"
            "## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\nA thought\n\n___\n",
            encoding="utf-8",
        )
        tagging.update_catalog("alex", False, "approve", "focus")
        captured = {}

        def call_ai(provider, ai_function, user_prompt):
            captured.update(provider=provider, ai_function=ai_function, user_prompt=user_prompt)
            return '{"blocks":{"2026-08-04 12:00:00":["focus"]},"proposed_tags":[]}'

        report = historical_tagging.run_historical_tagging(
            "alex",
            "2026-08-04",
            "2026-08-04",
            {"id": "lm_test"},
            {
                "system_prompt": "Custom system prompt",
                "user_prompt_template": "Known={canonical_tags_json}\nContent={journal_body}",
                "max_tokens": 1234,
                "temperature": 0.1,
            },
            call_ai,
        )

        self.assertEqual(report["processed"], 1)
        self.assertEqual(report["errors"], [])
        self.assertEqual(captured["ai_function"]["system_prompt"], "Custom system prompt")
        self.assertEqual(captured["ai_function"]["max_tokens"], 1234)
        self.assertEqual(captured["ai_function"]["temperature"], 0.1)
        self.assertTrue(captured["ai_function"]["require_content"])
        response_format = captured["ai_function"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        schema = response_format["json_schema"]["schema"]
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(schema["required"], ["blocks", "proposed_tags"])
        self.assertFalse(schema["additionalProperties"])
        blocks_schema = schema["properties"]["blocks"]
        self.assertEqual(
            blocks_schema["properties"],
            {"2026-08-04 12:00:00": {"type": "array", "items": {"type": "string"}}},
        )
        self.assertEqual(blocks_schema["required"], ["2026-08-04 12:00:00"])
        self.assertFalse(blocks_schema["additionalProperties"])
        self.assertIn('Known=["focus"]', captured["user_prompt"])
        self.assertIn("Content=## Note", captured["user_prompt"])
        self.assertNotIn("# Journal", captured["user_prompt"])
        self.assertNotIn("{journal_body}", captured["user_prompt"])
        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"], {"2026-08-04 12:00:00": ["focus"]})
        # The block carried no hashtag of its own, so the whole assignment is
        # machine origin and the file is stamped as classified.
        self.assertEqual(footer["ai"], {"2026-08-04 12:00:00": ["focus"]})
        self.assertTrue(footer["ai_run"].startswith("20"))
        self.assertEqual(report["proposals"], [])

    PROMPTS = {
        "system_prompt": "Classify",
        "user_prompt_template": "Known={canonical_tags_json} Content={journal_body}",
    }

    def _journal(self, day, body):
        path = self.root / "alex/{}/Journal_{}.md".format(day.replace("-", "/"), day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def _run(self, answers, start="2026-08-01", end="2026-08-31", **options):
        calls = []

        def call_ai(provider, ai_function, user_prompt):
            calls.append(user_prompt)
            self._last_prompt = user_prompt
            return answers[len(calls) - 1]

        report = historical_tagging.run_historical_tagging(
            "alex", start, end, {"id": "lm_test"}, self.PROMPTS, call_ai, **options,
        )
        report["calls"] = len(calls)
        return report

    def test_a_hashtag_the_user_typed_is_never_recorded_as_machine_origin(self):
        path = self._journal(
            "2026-08-04",
            "___\n\n## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\nEigener Text #meins\n\n___\n",
        )
        tagging.update_catalog("alex", False, "approve", "focus")

        self._run(['{"blocks":{"2026-08-04 12:00:00":["focus","meins"]},"proposed_tags":[]}'])

        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"], {"2026-08-04 12:00:00": ["focus", "meins"]})
        self.assertEqual(footer["ai"], {"2026-08-04 12:00:00": ["focus"]})

    def test_a_tag_the_model_proposes_can_be_assigned_in_the_same_run(self):
        path = self._journal(
            "2026-08-04",
            "___\n\n## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\nEin Gedanke\n\n___\n",
        )

        self._run(['{"blocks":{"2026-08-04 12:00:00":["frisch"]},"proposed_tags":["frisch"]}'])

        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"], {"2026-08-04 12:00:00": ["frisch"]})
        self.assertIn("frisch", tagging.read_catalog("alex")["proposals"])

    def test_missing_mode_resumes_instead_of_classifying_a_file_twice(self):
        self._journal("2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n")
        self._journal("2026-08-05", "___\n\n## B | Datum & Uhrzeit: 2026-08-05 12:00:00\nZwei\n\n___\n")
        answers = [
            '{"blocks":{"2026-08-04 12:00:00":["frisch"]},"proposed_tags":["frisch"]}',
            '{"blocks":{"2026-08-05 12:00:00":["frisch"]},"proposed_tags":["frisch"]}',
        ]

        first = self._run(answers, mode="missing", max_files=1)
        second = self._run(answers[1:], mode="missing", max_files=1)
        third = self._run([], mode="missing", max_files=1)

        self.assertEqual((first["processed"], first["remaining"], first["next_date"]), (1, 1, "2026-08-05"))
        self.assertEqual((second["processed"], second["skipped"], second["calls"]), (1, 1, 1))
        self.assertEqual((third["processed"], third["skipped"], third["calls"]), (0, 2, 0))

    def test_missing_mode_revisits_a_stamped_day_with_an_untagged_block(self):
        """Ein beim ersten Lauf ausgelassener Eintrag bleibt nicht fuer immer leer."""
        path = self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n\n"
            "## B | Datum & Uhrzeit: 2026-08-04 13:00:00\nZwei\n\n___\n",
        )
        self._run(
            ['{"blocks":{"2026-08-04 12:00:00":["frisch"],"2026-08-04 13:00:00":[]},"proposed_tags":["frisch"]}'],
            mode="missing",
        )
        _, stamped = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertTrue(tagging.footer_ai_run(stamped))
        self.assertNotIn("2026-08-04 13:00:00", stamped["blocks"])

        # Nur der offene Block geht erneut an das Modell; der getaggte behaelt,
        # was er hat.
        report = self._run(
            ['{"blocks":{"2026-08-04 13:00:00":["spaet"]},"proposed_tags":[]}'],
            mode="missing",
        )

        self.assertEqual((report["processed"], report["skipped"], report["calls"]), (1, 0, 1))
        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"]["2026-08-04 13:00:00"], ["spaet"])
        self.assertEqual(footer["blocks"]["2026-08-04 12:00:00"], ["frisch"])
        self.assertEqual(footer["ai"]["2026-08-04 12:00:00"], ["frisch"])

    def test_a_new_entry_with_its_own_hashtag_still_reaches_the_model(self):
        """Der PROD-Befund: ein Eintrag mit eigenem Hashtag galt als erledigt."""
        path = self._journal(
            "2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n",
        )
        self._run(['{"blocks":{"2026-08-04 12:00:00":["frisch"]},"proposed_tags":["frisch"]}'],
                  mode="missing")
        content = path.read_text(encoding="utf-8")
        body, footer = tagging.strip_footer(content)
        body = body.rstrip() + "\n\n## B | Datum & Uhrzeit: 2026-08-04 18:00:00\nZwei #meins\n\n___\n"
        path.write_text(tagging.refresh_journal_footer("alex", body, "2026-08-04", footer), encoding="utf-8")

        report = self._run(['{"blocks":{"2026-08-04 18:00:00":["frisch"]},"proposed_tags":[]}'],
                           mode="missing")

        self.assertEqual((report["processed"], report["calls"]), (1, 1))
        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"]["2026-08-04 18:00:00"], ["frisch", "meins"])
        self.assertEqual(footer["ai"]["2026-08-04 18:00:00"], ["frisch"])
        self.assertEqual(footer["ai_seen"], ["2026-08-04 12:00:00", "2026-08-04 18:00:00"])
        # Danach ist der Tag erledigt.
        self.assertEqual(self._run([], mode="missing")["calls"], 0)

    def test_an_old_footer_counts_entries_written_after_its_stamp_as_new(self):
        """Fussnoten ohne ``ai_seen``: was nach dem Stempel geschrieben wurde, ist neu."""
        path = self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 08:00:00\nEins #alt\n\n___\n\n"
            "## B | Datum & Uhrzeit: 2026-08-04 20:00:00\nZwei #neu\n\n___\n",
        )
        content = path.read_text(encoding="utf-8")
        body, _ = tagging.strip_footer(content)
        tagging.propose_tags("alex", ["alt", "neu"])
        catalogs = [tagging.read_catalog("alex"), tagging.read_catalog(family=True)]
        blocks = tagging.journal_blocks(body, "2026-08-04")
        # Stempel 10:00 UTC = 12:00 in Berlin: A lag davor, B kam spaeter dazu.
        footer = tagging.render_footer(blocks, catalogs, {}, "2026-08-04T10:00:00+00:00")
        path.write_text(body.rstrip() + "\n\n" + footer, encoding="utf-8")

        report = self._run(['{"blocks":{"2026-08-04 20:00:00":["abend"]},"proposed_tags":[]}'],
                           mode="missing")

        self.assertEqual((report["processed"], report["calls"]), (1, 1))
        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"]["2026-08-04 08:00:00"], ["alt"])
        self.assertEqual(footer["blocks"]["2026-08-04 20:00:00"], ["abend", "neu"])

    def test_established_hashtags_lead_the_prompt_and_action_tags_stay_out(self):
        self._journal("2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n")
        tagging.update_catalog("alex", False, "approve", "selten")
        tagging.update_catalog("alex", False, "approve", "oft")
        index = {"version": 1, "references": [
            {"source": "personal", "user_id": "alex", "tags": ["oft", "beliebt"]} for _ in range(4)
        ] + [{"source": "personal", "user_id": "jemand", "tags": ["fremd"]} for _ in range(5)]}
        original = tagging.get_index
        tagging.get_index = lambda: index
        try:
            report = self._run(['{"blocks":{"2026-08-04 12:00:00":[]},"proposed_tags":[]}'])
        finally:
            tagging.get_index = original
        self.assertEqual(report["errors"], [])
        prompt = self._last_prompt
        self.assertIn('Known=["beliebt", "oft", "selten"]', prompt)
        self.assertIn("beliebt (4), oft (4)", prompt)
        self.assertNotIn("fremd", prompt)

    def test_a_fully_tagged_stamped_day_is_not_sent_again(self):
        self._journal("2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n")
        answer = '{"blocks":{"2026-08-04 12:00:00":["frisch"]},"proposed_tags":["frisch"]}'
        self._run([answer], mode="missing")

        report = self._run([], mode="missing")

        self.assertEqual((report["processed"], report["skipped"], report["calls"]), (0, 1, 0))

    def test_an_ordinary_save_keeps_what_the_model_has_seen(self):
        path = self._journal("2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n")
        self._run(['{"blocks":{"2026-08-04 12:00:00":[]},"proposed_tags":[]}'], mode="missing")

        refreshed = tagging.refresh_journal_footer("alex", path.read_text(encoding="utf-8"), "2026-08-04")

        _, footer = tagging.strip_footer(refreshed)
        self.assertEqual(footer["ai_seen"], ["2026-08-04 12:00:00"])

    def test_a_batch_names_the_next_day_because_refresh_never_advances_by_itself(self):
        """A caller has to move start_date along; refresh leaves every day due."""
        self._journal("2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n")
        self._journal("2026-08-05", "___\n\n## B | Datum & Uhrzeit: 2026-08-05 12:00:00\nZwei\n\n___\n")

        def call_ai(provider, ai_function, user_prompt):
            anchor = "2026-08-04 12:00:00" if "Eins" in user_prompt else "2026-08-05 12:00:00"
            return '{"blocks":{"%s":["frisch"]},"proposed_tags":["frisch"]}' % anchor

        def run(start="2026-08-01", **options):
            return historical_tagging.run_historical_tagging(
                "alex", start, "2026-08-31", {"id": "lm_test"}, self.PROMPTS, call_ai, **options,
            )

        first = run(mode="refresh", max_files=1)
        second = run(mode="refresh", max_files=1)
        advanced = run("2026-08-05", mode="refresh", max_files=1)

        self.assertEqual((first["files"][0]["date"], first["next_date"]), ("2026-08-04", "2026-08-05"))
        # Same call again, same day again: only the cursor gets the caller on.
        self.assertEqual(second["files"][0]["date"], "2026-08-04")
        self.assertEqual((advanced["files"][0]["date"], advanced["next_date"]), ("2026-08-05", ""))

    def test_refresh_replaces_the_models_own_tags_and_keeps_the_users(self):
        path = self._journal(
            "2026-08-04",
            "___\n\n## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\nEigener Text #meins\n\n___\n",
        )
        self._run(['{"blocks":{"2026-08-04 12:00:00":["alt","meins"]},"proposed_tags":["alt"]}'])

        self._run(['{"blocks":{"2026-08-04 12:00:00":["neu","meins"]},"proposed_tags":["neu"]}'], mode="refresh")

        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"], {"2026-08-04 12:00:00": ["meins", "neu"]})
        self.assertEqual(footer["ai"], {"2026-08-04 12:00:00": ["neu"]})

    def _take_back(self, path, day, anchor, tags):
        """Mirror "Alle Hashtags bearbeiten": the user removes a model's hashtags."""
        body, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        changed, removed = tagging.reject_ai_tags(footer, [anchor], tags)
        path.write_text(
            tagging.refresh_journal_footer("alex", body, day, existing_footer=changed),
            encoding="utf-8",
        )
        return removed

    def test_a_hashtag_the_user_took_back_is_not_assigned_again(self):
        path = self._journal(
            "2026-08-04",
            "___\n\n## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\nEigener Text\n\n___\n",
        )
        self._run(['{"blocks":{"2026-08-04 12:00:00":["falsch","gut"]},"proposed_tags":["falsch","gut"]}'])
        self.assertEqual(self._take_back(path, "2026-08-04", "2026-08-04 12:00:00", ["falsch"]), ["falsch"])

        self._run(['{"blocks":{"2026-08-04 12:00:00":["falsch","gut"]},"proposed_tags":[]}'], mode="refresh")

        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"], {"2026-08-04 12:00:00": ["gut"]})
        self.assertEqual(footer["ai"], {"2026-08-04 12:00:00": ["gut"]})
        self.assertEqual(footer["ai_rejected"], {"2026-08-04 12:00:00": ["falsch"]})

    def test_a_block_emptied_by_the_user_gets_no_second_chance(self):
        path = self._journal(
            "2026-08-04",
            "___\n\n## Note | Datum & Uhrzeit: 2026-08-04 12:00:00\nEigener Text\n\n___\n",
        )
        self._run(['{"blocks":{"2026-08-04 12:00:00":["falsch"]},"proposed_tags":["falsch"]}'])
        self._take_back(path, "2026-08-04", "2026-08-04 12:00:00", ["falsch"])

        report = self._run([], mode="missing")

        self.assertEqual((report["processed"], report["skipped"], report["calls"]), (0, 1, 0))

    def test_stale_mode_only_revisits_files_classified_before_a_date(self):
        self._journal("2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n")
        self._run(['{"blocks":{"2026-08-04 12:00:00":["frisch"]},"proposed_tags":["frisch"]}'])

        untouched = self._run([], mode="stale", stale_before="2020-01-01")
        revisited = self._run(
            ['{"blocks":{"2026-08-04 12:00:00":["frisch"]},"proposed_tags":[]}'],
            mode="stale", stale_before="2999-01-01",
        )

        self.assertEqual((untouched["processed"], untouched["skipped"]), (0, 1))
        self.assertEqual(revisited["processed"], 1)

    def test_the_report_names_which_block_received_which_hashtag(self):
        self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEigener Text #meins\n\n___\n\n"
            "## B | Datum & Uhrzeit: 2026-08-04 15:30:00\nZweiter Eintrag\n\n___\n",
        )

        report = self._run(['{"blocks":{"2026-08-04 12:00:00":["frisch","meins"],'
                            '"2026-08-04 15:30:00":[]},"proposed_tags":["frisch"]}'])

        self.assertEqual(report["files"], [{
            "date": "2026-08-04", "file": "Journal_2026-08-04.md", "status": "processed",
            "blocks": [
                {"anchor": "2026-08-04 12:00:00", "time": "12:00:00", "scope": "block",
                 "ai_tags": ["frisch"], "own_tags": ["meins"]},
                {"anchor": "2026-08-04 15:30:00", "time": "15:30:00", "scope": "block",
                 "ai_tags": [], "own_tags": []},
            ],
        }])

    def test_a_hashtag_the_model_only_used_on_a_block_still_survives_the_run(self):
        """Local models tag a block with a word they never repeat in proposed_tags.

        Filtering those out left a run reporting success while its hashtags
        existed nowhere: not in the footer, not in the catalog, not in a filter.
        """
        path = self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEin Gedanke\n\n___\n",
        )

        report = self._run(['{"blocks":{"2026-08-04 12:00:00":["neuestag"]},"proposed_tags":[]}'])

        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"], {"2026-08-04 12:00:00": ["neuestag"]})
        self.assertEqual(footer["ai"], {"2026-08-04 12:00:00": ["neuestag"]})
        self.assertEqual(tagging.read_catalog("alex")["proposals"], ["neuestag"])
        self.assertEqual(report["proposals"], ["neuestag"])
        self.assertEqual(report["files"][0]["blocks"][0]["ai_tags"], ["neuestag"])

    def test_only_hashtags_new_to_the_catalog_are_reported_as_proposals(self):
        """A proposal that already waited in the catalog is not news again."""
        self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEin Gedanke #selbst\n\n___\n",
        )
        tagging.propose_tags("alex", ["alterhut"])

        report = self._run([
            '{"blocks":{"2026-08-04 12:00:00":["alterhut","frisch","selbst"]},"proposed_tags":["frisch"]}'
        ])

        self.assertEqual(report["proposals"], ["frisch"])
        self.assertEqual(
            sorted(tagging.read_catalog("alex")["proposals"]), ["alterhut", "frisch", "selbst"],
        )

    def test_a_hashtag_the_user_blocked_stays_out_even_when_the_model_uses_it(self):
        path = self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEin Gedanke\n\n___\n",
        )
        tagging.update_catalog("alex", False, "block", "unerwuenscht")

        report = self._run(['{"blocks":{"2026-08-04 12:00:00":["unerwuenscht"]},"proposed_tags":[]}'])

        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"], {})
        self.assertEqual(report["proposals"], [])

    def test_a_globally_excluded_hashtag_is_never_assigned_in_any_mode(self):
        tagging.update_catalog("alex", False, "approve", "info")
        tagging.update_catalog("alex", False, "alias", "infos", "info")
        tagging.update_catalog("alex", False, "ai_exclude", "#Info")
        tagging.update_catalog("alex", False, "ai_exclude", "sammel")
        self.assertEqual(tagging.read_catalog("alex")["ai_excluded"], ["info", "sammel"])

        for day, mode, options in (
            ("2026-08-04", "refresh", {}),
            ("2026-08-05", "stale", {"stale_before": "2099-01-01"}),
            ("2026-08-06", "missing", {}),
        ):
            with self.subTest(mode=mode):
                anchor = day + " 12:00:00"
                path = self._journal(day, "___\n\n## A | Datum & Uhrzeit: {}\nEin Gedanke #info\n\n___\n".format(anchor))
                answer = '{"blocks":{"%s":["info","infos","gut","sammel"]},"proposed_tags":["sammel"]}' % anchor

                report = self._run([answer], start=day, end=day, mode=mode, **options)

                _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
                # Das selbst geschriebene #info bleibt, nur die KI vergibt es nicht.
                self.assertEqual(footer["blocks"], {anchor: ["gut", "info"]})
                self.assertEqual(footer["ai"], {anchor: ["gut"]})
                self.assertNotIn("sammel", report["proposals"])
        self.assertNotIn("sammel", tagging.read_catalog("alex")["proposals"])

    def test_excluding_a_hashtag_drops_what_an_earlier_run_assigned(self):
        path = self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 08:00:00\nEins\n\n"
            "___\n\n## B | Datum & Uhrzeit: 2026-08-04 09:00:00\nZwei #sammel\n\n___\n",
        )
        self._run(['{"blocks":{"2026-08-04 08:00:00":["sammel","gut"],'
                   '"2026-08-04 09:00:00":["gut"]},"proposed_tags":["sammel","gut"]}'])
        tagging.update_catalog("alex", False, "ai_exclude", "sammel")
        body, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        body += "\n## C | Datum & Uhrzeit: 2026-08-04 20:00:00\nDrei\n\n___\n"
        path.write_text(tagging.refresh_journal_footer("alex", body, "2026-08-04", existing_footer=footer), encoding="utf-8")

        self._run(['{"blocks":{"2026-08-04 20:00:00":["sammel"]},"proposed_tags":[]}'], mode="missing")

        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["blocks"]["2026-08-04 08:00:00"], ["gut"])
        # Selbst geschrieben: bleibt, obwohl ausgeschlossen.
        self.assertEqual(footer["blocks"]["2026-08-04 09:00:00"], ["gut", "sammel"])
        self.assertNotIn("2026-08-04 20:00:00", footer["blocks"])
        self.assertEqual(footer["ai"]["2026-08-04 08:00:00"], ["gut"])

    def test_an_excluded_hashtag_is_not_recommended_in_the_prompt(self):
        self._journal("2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n")
        tagging.update_catalog("alex", False, "approve", "oft")
        tagging.update_catalog("alex", False, "approve", "info")
        tagging.update_catalog("alex", False, "ai_exclude", "info")

        self._run(['{"blocks":{"2026-08-04 12:00:00":[]},"proposed_tags":[]}'])

        self.assertIn('Known=["oft"]', self._last_prompt)

    def test_a_failing_journal_is_named_with_its_date(self):
        self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n",
        )

        report = self._run(['{"blocks":{"2026-08-04 09:00:00":["frisch"]},"proposed_tags":[]}'])

        self.assertEqual(report["errors"][0]["date"], "2026-08-04")
        self.assertEqual(report["files"][0]["status"], "error")
        self.assertIn("2026-08-04 12:00:00", report["files"][0]["error"])

    def test_an_unexpected_provider_error_is_named_instead_of_ending_the_run(self):
        self._journal("2026-08-04", "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n")
        self._journal("2026-08-05", "___\n\n## B | Datum & Uhrzeit: 2026-08-05 12:00:00\nZwei\n\n___\n")

        def call_ai(provider, ai_function, user_prompt):
            if "Eins" in user_prompt:
                raise RuntimeError("provider exploded")
            return '{"blocks":{"2026-08-05 12:00:00":["frisch"]},"proposed_tags":["frisch"]}'

        report = historical_tagging.run_historical_tagging(
            "alex", "2026-08-01", "2026-08-31", {"id": "lm_test"}, self.PROMPTS, call_ai,
        )

        self.assertEqual(report["errors"][0]["error"], "RuntimeError: provider exploded")
        self.assertEqual(report["processed"], 1)

    def test_a_day_with_nothing_in_it_is_named_rather_than_only_counted(self):
        # Free text without separators is no longer empty: it is a legacy day
        # and gets classified as a whole. Only a day with no content at all
        # leaves the model nothing to see.
        self._journal("2026-08-04", "# Journal 2026-08-04\n\n___\n\n___\n")

        report = self._run([])

        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["files"][0]["status"], "empty")

    def test_preview_reports_the_assignment_without_touching_the_file(self):
        path = self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n",
        )
        before = path.read_text(encoding="utf-8")

        report = self._run(
            ['{"blocks":{"2026-08-04 12:00:00":["frisch"]},"proposed_tags":["frisch"]}'],
            preview=True,
        )

        self.assertEqual(report["files"][0]["status"], "preview")
        self.assertEqual(report["files"][0]["blocks"][0]["ai_tags"], ["frisch"])
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_previewed_day_is_still_pending_for_the_real_run(self):
        self._journal(
            "2026-08-04",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-04 12:00:00\nEins\n\n___\n",
        )
        answer = '{"blocks":{"2026-08-04 12:00:00":["frisch"]},"proposed_tags":["frisch"]}'
        self._run([answer], mode="missing", preview=True)

        report = self._run([answer], mode="missing")

        self.assertEqual(report["processed"], 1)

    def test_an_unknown_mode_is_refused_before_any_model_call(self):
        with self.assertRaises(ValueError):
            self._run([], mode="halbwegs")
        with self.assertRaises(ValueError):
            self._run([], mode="stale")

    def test_rejects_partial_classification_without_rewriting_journal(self):
        path = self.root / "alex/2026/08/04/Journal_2026-08-04.md"
        path.parent.mkdir(parents=True)
        original = (
            "# Journal\n\n___\n\n"
            "## First | Datum & Uhrzeit: 2026-08-04 12:00:00\nFirst\n\n___\n\n"
            "## Second | Datum & Uhrzeit: 2026-08-04 13:00:00\nSecond\n\n___\n"
        )
        path.write_text(original, encoding="utf-8")

        report = historical_tagging.run_historical_tagging(
            "alex",
            "2026-08-04",
            "2026-08-04",
            {"id": "lm_test"},
            {
                "system_prompt": "System",
                "user_prompt_template": "{canonical_tags_json}\n{journal_body}",
            },
            lambda *_: '{"blocks":{"2026-08-04 12:00:00":[]},"proposed_tags":[]}',
        )

        self.assertEqual(report["processed"], 0)
        self.assertIn("missing: ['2026-08-04 13:00:00']", report["errors"][0]["error"])
        self.assertIn("unknown: none", report["errors"][0]["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejects_duplicate_timestamps_before_calling_model(self):
        path = self.root / "alex/2026/08/04/Journal_2026-08-04.md"
        path.parent.mkdir(parents=True)
        original = (
            "___\n\n## First | Datum & Uhrzeit: 2026-08-04 12:00:00\nFirst\n\n___\n\n"
            "## Second | Datum & Uhrzeit: 2026-08-04 12:00:00\nSecond\n\n___\n"
        )
        path.write_text(original, encoding="utf-8")
        call_count = 0

        def call_ai(*_):
            nonlocal call_count
            call_count += 1
            return "{}"

        report = historical_tagging.run_historical_tagging(
            "alex", "2026-08-04", "2026-08-04", {"id": "lm_test"},
            {
                "system_prompt": "System",
                "user_prompt_template": "{canonical_tags_json}\n{journal_body}",
            },
            call_ai,
        )

        self.assertEqual(call_count, 0)
        self.assertIn("duplicate timestamps", report["errors"][0]["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_requires_both_user_prompt_placeholders(self):
        with self.assertRaisesRegex(ValueError, "canonical_tags_json"):
            historical_tagging.run_historical_tagging(
                "alex", "2026-08-04", "2026-08-04", {"id": "lm_test"},
                {"system_prompt": "System", "user_prompt_template": "Only {journal_body}"},
                lambda *_: "",
            )

    def test_rejects_duplicate_timestamp_keys_in_model_json(self):
        response = (
            '{"blocks":{"2026-08-04 12:00:00":[],"2026-08-04 12:00:00":["focus"]},'
            '"proposed_tags":[]}'
        )

        with self.assertRaisesRegex(ValueError, "duplicate JSON key: 2026-08-04 12:00:00"):
            historical_tagging._parse_model_response(response)

    def test_prompt_and_schema_exclude_unrecognised_timestamps(self):
        path = self.root / "alex/2026/08/04/Journal_2026-08-04.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "# Journal\n\n___\n\n"
            "## Valid | Datum & Uhrzeit: 2026-08-04 12:00:00\nKeep me\n\n___\n\n"
            "## Ambiguous | Datum & Uhrzeit: 2026-08-04 22:20:58\n"
            "Referenced: Datum & Uhrzeit: 2026-08-04 22:32:38\nIgnore me\n\n___\n",
            encoding="utf-8",
        )
        captured = {}

        def call_ai(_provider, ai_function, prompt):
            captured["ai_function"] = ai_function
            captured["prompt"] = prompt
            return '{"blocks":{"2026-08-04 12:00:00":[]},"proposed_tags":[]}'

        report = historical_tagging.run_historical_tagging(
            "alex", "2026-08-04", "2026-08-04", {"id": "lm_test"},
            {
                "system_prompt": "System",
                "user_prompt_template": "{canonical_tags_json}\n{journal_body}",
            },
            call_ai,
        )

        self.assertEqual(report["processed"], 1)
        self.assertNotIn("2026-08-04 22:20:58", captured["prompt"])
        self.assertNotIn("2026-08-04 22:32:38", captured["prompt"])
        blocks_schema = captured["ai_function"]["response_format"]["json_schema"]["schema"]["properties"]["blocks"]
        self.assertEqual(list(blocks_schema["properties"]), ["2026-08-04 12:00:00"])
        self.assertEqual(blocks_schema["required"], ["2026-08-04 12:00:00"])
        self.assertFalse(blocks_schema["additionalProperties"])

    def test_a_legacy_day_without_any_time_is_classified_as_a_whole(self):
        """Old journals were the point of the run and must not stay untouched."""
        path = self.root / "alex/2025/03/04/Journal_2025-03-04.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Journal\n\n___\nNur Text, keine Uhrzeit\n___\n", encoding="utf-8")
        tagging.update_catalog("alex", False, "approve", "focus")
        bodies = []

        def call_ai(provider, ai_function, user_prompt):
            bodies.append(user_prompt)
            return '{"blocks":{"2025-03-04 00:00:00":["focus"]},"proposed_tags":[]}'

        report = historical_tagging.run_historical_tagging(
            "alex", "2025-03-04", "2025-03-04", {"id": "lm_test"},
            {"system_prompt": "s", "user_prompt_template": "{canonical_tags_json}{journal_body}"},
            call_ai,
        )

        self.assertEqual(report["processed"], 1)
        self.assertEqual(report["errors"], [])
        self.assertIn("Nur Text, keine Uhrzeit", bodies[0])
        self.assertEqual(report["files"][0]["blocks"][0]["scope"], "day")
        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["ai"], {"2025-03-04 00:00:00": ["focus"]})

    def test_a_legacy_day_with_clock_stamps_is_classified_per_block(self):
        path = self.root / "alex/2025/03/05/Journal_2025-03-05.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "# Journal\n\n15:52:56\n\n___\nThema: Backup! Time:15:53:02\n___\n",
            encoding="utf-8",
        )
        tagging.update_catalog("alex", False, "approve", "focus")

        def call_ai(provider, ai_function, user_prompt):
            return (
                '{"blocks":{"2025-03-05 15:52:56":[],"2025-03-05 15:53:02":["focus"]},'
                '"proposed_tags":[]}'
            )

        report = historical_tagging.run_historical_tagging(
            "alex", "2025-03-05", "2025-03-05", {"id": "lm_test"},
            {"system_prompt": "s", "user_prompt_template": "{canonical_tags_json}{journal_body}"},
            call_ai,
        )

        self.assertEqual(report["processed"], 1)
        self.assertEqual(
            [block["scope"] for block in report["files"][0]["blocks"]], ["block", "block"],
        )
        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["ai"], {"2025-03-05 15:53:02": ["focus"]})


class _FakeHandle:
    """Was der Lauf von seinem Handle braucht — ohne Datei und ohne Thread."""

    def __init__(self, user_id="alex", cancel_after=None):
        self.user_id = user_id
        self.entries = []
        self.progress_calls = []
        self._cancel_after = cancel_after
        self._checks = 0

    @property
    def cancelled(self):
        self._checks += 1
        return self._cancel_after is not None and self._checks > self._cancel_after

    def progress(self, **values):
        self.progress_calls.append(values)

    def log(self, entry):
        self.entries.append(entry)


class ServerSideRunTests(unittest.TestCase):
    """Der Lauf über einen Zeitraum gehört dem Server, nicht dem Browser."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old_data_dir, self.old_family_dir = tagging.DATA_DIR, tagging.FAMILY_DIR
        tagging.DATA_DIR = self.root
        tagging.FAMILY_DIR = self.root / "family"
        self.calls = []
        self.old_core = historical_tagging._core_module
        historical_tagging._core_module = lambda: self.core
        self.core = types.SimpleNamespace(
            DATA_DIR=self.root,
            load_config=lambda: {
                "ai_providers": [{"id": "lm_test", "model": "local"}],
                "historical_tagging_ai": {
                    "system_prompt": "s",
                    "user_prompt_template": "{canonical_tags_json}{journal_body}",
                },
            },
            _call_ai_api=self._call_ai,
            brain_module=types.SimpleNamespace(enqueue_rebuild=lambda user_id: None),
        )
        # Die Verschlagwortung liest ihre Verbindung aus der gespeicherten
        # Einstellung, nicht mehr aus ``ai_providers``.
        historical_tagging.save_settings(
            "alex", {"base_url": "http://127.0.0.1:1234", "model": "local"},
        )

    def tearDown(self):
        historical_tagging._core_module = self.old_core
        tagging.DATA_DIR, tagging.FAMILY_DIR = self.old_data_dir, self.old_family_dir
        self.temp_dir.cleanup()

    def _call_ai(self, provider, ai_function, prompt):
        self.calls.append(prompt)
        return self._empty_answer(prompt)

    @staticmethod
    def _empty_answer(prompt):
        """Antwort ohne neue Hashtags — aber mit genau den Zeitstempeln des Tages.

        Der Lauf verwirft jede Antwort, deren Anker nicht zum Journal passen; ein
        pauschales ``{"blocks":{}}`` waere fuer ihn ein Fehler, kein Ergebnis.
        """
        anchors = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", prompt)
        blocks = {anchor: [] for anchor in dict.fromkeys(anchors)}
        return json.dumps({"blocks": blocks, "proposed_tags": []})

    def _journal(self, date, anchor_time="12:00:00"):
        path = self.root / "alex/{}/Journal_{}.md".format(date.replace("-", "/"), date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Journal\n\n___\n\n## Note | Datum & Uhrzeit: {} {}\nEin Gedanke\n\n___\n".format(
                date, anchor_time,
            ),
            encoding="utf-8",
        )
        return path

    def test_settings_keep_the_daily_hour_but_never_expose_the_key(self):
        saved = historical_tagging.save_settings("alex", {
            "base_url": "http://127.0.0.1:1234", "model": "local",
            "api_key": "secret", "daily": True, "daily_hour": 22,
        })
        loaded = historical_tagging.load_settings("alex")

        # Die alten Felder werden zum taeglichen Job um diese Stunde.
        self.assertEqual((loaded["enabled"], loaded["schedule_mode"], loaded["schedule_time"]),
                         (True, "daily", "22:00"))
        self.assertEqual(loaded["api_key"], "secret")
        self.assertEqual(historical_tagging.provider_from_settings(saved)["api_url"],
                         "http://127.0.0.1:1234/v1/chat/completions")
        self.assertNotIn("api_key", historical_tagging.public_settings(loaded))

    def test_settings_reject_an_hour_outside_the_day(self):
        with self.assertRaises(ValueError):
            historical_tagging.save_settings("alex", {
                "base_url": "http://127.0.0.1:1234", "model": "local", "daily_hour": 24,
            })

    def test_the_job_can_change_its_schedule_without_resending_the_connection(self):
        saved = historical_tagging.save_settings("alex", {
            "enabled": True, "schedule_mode": "custom", "schedule_time": "06:30",
            "schedule_rule": {"kind": "weekly", "weekdays": [0, 4]}, "range_days": 3,
            "mode": "refresh", "notify": {"errors": False, "proposals": True},
        })

        self.assertEqual(saved["model"], "local")
        public = historical_tagging.public_settings(historical_tagging.load_settings("alex"))
        self.assertEqual(public["schedule_rule"], {"kind": "weekly", "weekdays": [0, 4]})
        self.assertEqual(public["notify"], {"errors": False, "results": False, "proposals": True})
        self.assertEqual((public["range_days"], public["mode"]), (3, "refresh"))

    def test_an_active_schedule_needs_a_connection(self):
        historical_tagging._settings_path("alex").unlink()
        with self.assertRaises(ValueError):
            historical_tagging.save_settings("alex", {"enabled": True, "schedule_mode": "daily"})

    def test_a_finished_run_files_the_notices_its_job_asks_for(self):
        historical_tagging.save_settings("alex", {"notify": {"errors": True, "results": True}})
        historical_tagging.on_run_finished({
            "user_id": "alex", "status": "done", "trigger": "manual", "params": {},
            "result": {"processed": 2, "failed": 1, "proposals": ["neu"]},
        })
        historical_tagging.on_run_finished({
            "user_id": "alex", "status": "failed", "trigger": "schedule", "error": "weg",
            "params": {"defer_failure_notice": True}, "result": {},
        })

        events = historical_tagging.tagging_job.list_events(self.root, "alex")
        self.assertEqual([(item["kind"], item["count"]) for item in events],
                         [("tagging_error", 1), ("tagging_result", 2)])

    def test_the_whole_range_runs_in_one_go_without_a_browser(self):
        """Früher trieb JavaScript die Schleife; jetzt läuft sie hier durch."""
        for date in ("2026-08-01", "2026-08-02", "2026-08-03"):
            self._journal(date)
        handle = _FakeHandle()

        result = historical_tagging.run_handler(handle, {
            "start_date": "2026-08-01", "end_date": "2026-08-03",
            "mode": "missing",
        })

        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([entry["date"] for entry in handle.entries],
                         ["2026-08-01", "2026-08-02", "2026-08-03"])

    def test_an_unreachable_model_stops_the_run_instead_of_failing_every_day(self):
        """Ist das lokale Modell aus, wäre jeder weitere Tag derselbe Fehler."""
        for date in ("2026-08-01", "2026-08-02", "2026-08-03"):
            self._journal(date)

        def dead_endpoint(provider, ai_function, prompt):
            self.calls.append(prompt)
            raise ConnectionError("LM Studio is not reachable: connection refused")

        self.core._call_ai_api = dead_endpoint
        handle = _FakeHandle()

        with self.assertRaises(historical_tagging.background_runs.RunError) as caught:
            historical_tagging.run_handler(handle, {
                "start_date": "2026-08-01", "end_date": "2026-08-03",
                "mode": "missing",
            })

        self.assertIn("nicht erreichbar", str(caught.exception))
        self.assertEqual(len(self.calls), 1)

    def test_a_broken_journal_does_not_stop_the_run(self):
        """Ein krummes Journal ist kein Grund, den ganzen Zeitraum abzubrechen."""
        for date in ("2026-08-01", "2026-08-02"):
            self._journal(date)

        def half_broken(provider, ai_function, prompt):
            self.calls.append(prompt)
            if len(self.calls) == 1:
                return "kein JSON"
            return self._empty_answer(prompt)

        self.core._call_ai_api = half_broken
        handle = _FakeHandle()

        result = historical_tagging.run_handler(handle, {
            "start_date": "2026-08-01", "end_date": "2026-08-02",
            "mode": "missing",
        })

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["processed"], 1)

    def test_a_cancelled_run_leaves_the_rest_of_the_range_untouched(self):
        for date in ("2026-08-01", "2026-08-02", "2026-08-03"):
            self._journal(date)
        handle = _FakeHandle(cancel_after=1)

        result = historical_tagging.run_handler(handle, {
            "start_date": "2026-08-01", "end_date": "2026-08-03",
            "mode": "missing",
        })

        self.assertLess(result["processed"], 3)
        self.assertIn("abgebrochen", result["stopped"])

    def test_a_missing_connection_is_named_before_anything_runs(self):
        self._journal("2026-08-01")
        # Ohne gespeicherte Verbindung darf der Lauf nicht erst beim ersten Tag
        # scheitern und auch nicht stillschweigend irgendetwas anfassen.
        historical_tagging._settings_path("alex").unlink()
        handle = _FakeHandle()

        with self.assertRaises(historical_tagging.background_runs.RunError):
            historical_tagging.run_handler(handle, {
                "start_date": "2026-08-01", "end_date": "2026-08-01",
                "mode": "missing",
            })
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
