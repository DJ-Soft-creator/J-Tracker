"""Brain-Chat: Werkzeugaufrufe stehen eingeklappt und einzeln aufklappbar unter der Antwort."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BrainChatStepsFrontendTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "app" / "static" / "brain-chat.js").read_text(encoding="utf-8")

    def test_steps_show_by_default_collapsed_and_live_while_searching(self):
        self.assertIn("localStorage.getItem('journl.brain-chat.steps') !== '0'", self.source)
        self.assertIn("details.open = Boolean(live)", self.source)
        self.assertIn("stepsBlock(entry.steps, entry.truncated, entry.pending)", self.source)
        # Das Lauf-Protokoll liefert die fertigen Schritte schon waehrend der Suche.
        self.assertIn("(run.log || []).filter(", self.source)

    def test_each_call_is_its_own_expandable_item_with_arguments(self):
        self.assertIn("function stepItem(step)", self.source)
        self.assertIn("Object.entries(step.arguments || {})", self.source)
        self.assertIn("search: 'Suche'", self.source)

    def test_model_and_journal_text_never_become_html(self):
        start = self.source.index("function stepItem(step)")
        end = self.source.index("function stepsBlock(")
        self.assertNotIn("innerHTML", self.source[start:end])


if __name__ == "__main__":
    unittest.main()
