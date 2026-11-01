"""Kleine Bedien-Details im Schreiben- und Familie-Tab (statische Verdrahtung)."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def _read(name):
    return (STATIC / name).read_text(encoding="utf-8")


class WriteFamilyDetailsFrontendTests(unittest.TestCase):
    def test_both_shells_load_the_new_modules(self):
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn("filename='write-list-continue.js'", template, template_name)
            self.assertLess(template.index("filename='journal-analysis.js'"),
                            template.index("filename='analysis-notify-trigger.js'"), template_name)

    def test_lists_continue_on_enter_and_end_on_an_empty_item(self):
        source = _read("write-list-continue.js")
        self.assertIn("'beforeinput'", source)
        self.assertIn("insertLineBreak", source)
        self.assertIn("simple-input", source)
        self.assertIn("Number(numbered[1]) + 1", source)

    def test_mobile_opens_notifications_only_on_long_press(self):
        mobile = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        line = mobile[mobile.index('id="analysis-notify"'):]
        line = line[:line.index(">")]
        self.assertIn('data-open-on="longpress"', line)
        self.assertIn("pointer-events-none", line)
        desktop = (ROOT / "app" / "templates" / "desktop.html").read_text(encoding="utf-8")
        self.assertNotIn('data-open-on="longpress"', desktop)
        trigger = _read("analysis-notify-trigger.js")
        self.assertIn("'dblclick'", trigger)
        self.assertIn("'pointerdown'", trigger)
        self.assertIn("inset 0 0 0 2px", trigger)
        # Mobil nur ein Indikator: Rand statt Linie.
        self.assertIn("line.style.visibility = 'hidden'", trigger)
        analysis = _read("journal-analysis-notify.js")
        self.assertIn("notifyLine.dataset.openOn !== 'longpress'", analysis)
        self.assertIn("journl:analysis-notify", analysis)

    def test_checking_a_family_task_asks_first(self):
        for name in ("desktop-app-features.js", "mobile-app-features.js"):
            source = _read(name)
            check = source[source.index("async function checkFamilyTask"):]
            check = check[:check.index("apiFetch(")]
            self.assertIn("window.confirm(", check, name)
            self.assertIn("checkbox.checked &&", check, name)

    def test_recently_completed_tasks_can_be_reset_from_the_archive(self):
        source = _read("family-board.js")
        self.assertIn("recently_completed", source)
        self.assertIn("familyBoardReopenTask", source)
        self.assertIn("completed: false", source)


if __name__ == "__main__":
    unittest.main()
