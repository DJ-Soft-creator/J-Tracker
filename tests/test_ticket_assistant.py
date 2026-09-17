"""Ticket-Assistent: Fragmente sammeln, gruppieren, nach festen Kriterien bewerten."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "assistant-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-assistant-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import tagging
    import ticket_assistant


def _journal(day, blocks):
    """Ein Tagesjournal im kanonischen Blockformat.

    Ein Block liegt zwischen zwei `___` und traegt genau einen Zeitanker im
    Format, das tagging._ANCHOR_RE erwartet.
    """
    parts = [f"# Journal {day}\n"]
    for hour, text in blocks:
        parts.append(f"___\n\nDatum & Uhrzeit: {day} {hour}:00:00\n{text}\n")
    parts.append("___\n")
    return "\n".join(parts)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class FragmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.previous = (main.DATA_DIR, tagging.DATA_DIR)
        main.DATA_DIR = self.data
        tagging.DATA_DIR = self.data
        self.user = self.data / "user-1"

    def tearDown(self):
        main.DATA_DIR, tagging.DATA_DIR = self.previous
        self.temp.cleanup()

    def _write(self, day, text):
        year, month, date = day.split("-")
        directory = self.user / year / month / date
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"Journal_{day}.md").write_text(text, encoding="utf-8")

    def test_only_blocks_with_product_hashtags_are_collected(self):
        self._write("2026-08-22", _journal("2026-08-22", [
            ("10", "#feature-request Der Heute-Tab braucht einen Vortag-Button."),
            ("11", "Heute war das Wetter gut."),
        ]))
        fragments = ticket_assistant.collect_fragments("user-1", "2026-08-01", "2026-08-31")
        self.assertEqual(len(fragments), 1)
        self.assertIn("Vortag-Button", fragments[0]["text"])
        self.assertIn("feature-request", fragments[0]["tags"])

    def test_the_date_range_is_respected(self):
        self._write("2026-07-01", _journal("2026-07-01", [("10", "#jtracker alt")]))
        self._write("2026-08-22", _journal("2026-08-22", [("10", "#jtracker neu")]))
        fragments = ticket_assistant.collect_fragments("user-1", "2026-08-01", "2026-08-31")
        self.assertEqual([entry["date"] for entry in fragments], ["2026-08-22"])

    def test_fragments_are_ordered_oldest_first(self):
        self._write("2026-08-22", _journal("2026-08-22", [("14", "#jtracker spaeter")]))
        self._write("2026-08-20", _journal("2026-08-20", [("09", "#jtracker frueher")]))
        fragments = ticket_assistant.collect_fragments("user-1", "2026-08-01", "2026-08-31")
        self.assertEqual([entry["date"] for entry in fragments], ["2026-08-20", "2026-08-22"])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.fragments = [
            {"date": "2026-08-20", "anchor": "2026-08-20 09:00:00", "tags": [], "text": "eins"},
            {"date": "2026-08-22", "anchor": "2026-08-22 14:00:00", "tags": [], "text": "zwei"},
        ]

    def _parse(self, payload):
        return ticket_assistant.parse_response(json.dumps(payload), self.fragments)

    def test_a_proposal_is_mature_only_when_every_criterion_holds(self):
        proposals = self._parse({"vorhaben": [
            {"titel": "Vollständig", "fragmente": [1],
             "kriterien": {"ziel": True, "bereich": True, "abgrenzung": True}},
            {"titel": "Unvollständig", "fragmente": [2],
             "kriterien": {"ziel": True, "bereich": True, "abgrenzung": False}},
        ]})
        self.assertTrue(proposals[0]["mature"])
        self.assertFalse(proposals[1]["mature"])

    def test_a_missing_criterion_counts_as_not_met(self):
        proposals = self._parse({"vorhaben": [
            {"titel": "Lückenhaft", "fragmente": [1], "kriterien": {"ziel": True}}]})
        self.assertEqual(proposals[0]["criteria"],
                         {"ziel": True, "bereich": False, "abgrenzung": False})
        self.assertFalse(proposals[0]["mature"])

    def test_unknown_fragment_numbers_are_dropped(self):
        proposals = self._parse({"vorhaben": [
            {"titel": "Erfunden", "fragmente": [1, 99], "kriterien": {}}]})
        self.assertEqual(len(proposals[0]["fragments"]), 1)

    def test_a_proposal_without_any_valid_fragment_is_skipped(self):
        proposals = self._parse({"vorhaben": [
            {"titel": "Leer", "fragmente": [99], "kriterien": {}}]})
        self.assertEqual(proposals, [])

    def test_a_fenced_answer_is_still_understood(self):
        payload = '```json\n{"vorhaben": [{"titel": "X", "fragmente": [1], "kriterien": {}}]}\n```'
        proposals = ticket_assistant.parse_response(payload, self.fragments)
        self.assertEqual(proposals[0]["title"], "X")

    def test_a_broken_answer_raises_a_readable_error(self):
        with self.assertRaises(ValueError):
            ticket_assistant.parse_response("kein json", self.fragments)
        with self.assertRaises(ValueError):
            ticket_assistant.parse_response('{"etwas": []}', self.fragments)

    def test_the_prompt_names_every_criterion(self):
        prompt = ticket_assistant.build_prompt(self.fragments)
        for key in ticket_assistant.CRITERIA:
            self.assertIn(key, prompt)
        self.assertIn("[1]", prompt)
        self.assertIn("Erfinde nichts dazu", prompt)


if __name__ == "__main__":
    unittest.main()
