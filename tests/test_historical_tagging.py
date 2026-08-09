import tempfile
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
        self.assertEqual(footer, {"blocks": {"2026-08-04 12:00:00": ["focus"]}})
        self.assertEqual(report["proposals"], [])

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


if __name__ == "__main__":
    unittest.main()
