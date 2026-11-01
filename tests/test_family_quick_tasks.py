"""Schnell-Erfassung im Aufgaben-Editor: jede Zeile eine Aufgabe mit eigener Person/Datum/Status."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "static" / "family-quick-tasks.js"


class FamilyQuickTasksWiringTests(unittest.TestCase):
    def test_both_editors_save_through_the_parser(self):
        for name in ("desktop-app-features.js", "mobile-app-features.js"):
            source = (ROOT / "app" / "static" / name).read_text(encoding="utf-8")
            self.assertIn("window.journlFamilyQuickTasks?.parse(bulkEl.value", source, name)
            self.assertIn("task.user = item.user || ''", source, name)
            self.assertIn("journlFamilyQuickTasks.createPlans(plans)", source, name)

    def test_both_shells_load_the_module_after_the_board(self):
        for name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertLess(template.index("filename='family-board.js'"),
                            template.index("filename='family-quick-tasks.js'"), name)


@unittest.skipUnless(shutil.which("node"), "node fehlt (im DEV-Image normal)")
class FamilyQuickTasksParserTests(unittest.TestCase):
    def parse(self, text, full=True):
        script = (
            "global.window={};global.document={addEventListener(){}};"
            f"require({str(MODULE)!r});"
            "const users=[{id:'u-kim',display:'Kim'},{id:'u-alex',display:'Alexander'}];"
            f"console.log(JSON.stringify(window.journlFamilyQuickTasks.parse({text!r},"
            "{users,full:" + ("true" if full else "false") + ",defaultUser:'u-alex',today:'2026-10-02T10:00:00'})));"
        )
        import json
        return json.loads(subprocess.check_output(["node", "-e", script], text=True))

    def test_placeholder_uses_the_real_people_of_the_project(self):
        script = (
            "global.window={};global.document={addEventListener(){}};"
            f"require({str(MODULE)!r});const q=window.journlFamilyQuickTasks;"
            "console.log(JSON.stringify([q.placeholder([{id:'a',display:'admin-dev'},{id:'b',display:'user2-dev'}]),"
            "q.placeholder([])]));"
        )
        import json
        named, fallback = json.loads(subprocess.check_output(["node", "-e", script], text=True))
        self.assertIn("@admin-dev morgen", named)
        self.assertIn("@user2-dev 12.10.", named)
        self.assertIn("@Kim", fallback)

    def test_each_line_gets_its_own_person_date_and_status(self):
        tasks = self.parse("Müll @Kim morgen\n- [x] Steuer @alex 12.10.\nArzt in 2 wochen\n01.01. Neujahr")
        self.assertEqual([(t["title"], t["user"], t["target_date"], t["completed"]) for t in tasks], [
            ("Müll", "u-kim", "2026-10-03", False),
            ("Steuer", "u-alex", "2026-10-12", True),
            ("Arzt", "u-alex", "2026-10-16", False),
            ("Neujahr", "u-alex", "2027-01-01", False),
        ])

    def test_relative_dates(self):
        # Heute ist Freitag, 2.10.2026.
        lines = ["A naechsten Mittwoch", "B nächsten freitag", "C freitag", "D übernächsten Mo",
                 "E nächste Woche", "F nächsten Monat", "G Ende der Woche", "H Ende des Monats",
                 "I in einer Woche", "J in 2 Monaten", "K am 12.10."]
        tasks = self.parse("\n".join(lines))
        self.assertEqual([(t["title"], t["target_date"]) for t in tasks], [
            ("A", "2026-10-07"), ("B", "2026-10-09"), ("C", "2026-10-09"), ("D", "2026-10-12"),
            ("E", "2026-10-05"), ("F", "2026-11-01"), ("G", "2026-10-02"), ("H", "2026-10-31"),
            ("I", "2026-10-09"), ("J", "2026-12-02"), ("K", "2026-10-12"),
        ])
        self.assertTrue(all("recurrence" not in t for t in tasks))

    def test_repetitions_become_planner_entries(self):
        tasks = self.parse("Müll @Kim jeden Freitag\nGießen täglich\nBad putzen freitags\n"
                           "Miete monatlich\nAltpapier alle 2 Wochen ab 14.10.\nSport wöchentlich")
        self.assertEqual([(t["title"], t["recurrence"], t["target_date"], t["user"]) for t in tasks], [
            ("Müll", "weekly_friday", "2026-10-02", "u-kim"),
            ("Gießen", "daily", "2026-10-02", "u-alex"),
            ("Bad putzen", "weekly_friday", "2026-10-02", "u-alex"),
            ("Miete", "monthly_first", "2026-10-02", "u-alex"),
            ("Altpapier", "biweekly", "2026-10-14", "u-alex"),
            ("Sport", "weekly_friday", "2026-10-02", "u-alex"),
        ])
        self.assertEqual(tasks[0]["recurrenceLabel"], "Jeden Freitag")

    def test_a_repetition_without_a_person_is_flagged(self):
        script = (
            "global.window={};global.document={addEventListener(){}};"
            f"require({str(MODULE)!r});"
            "console.log(JSON.stringify(window.journlFamilyQuickTasks.parse('Blumen jeden tag',"
            "{users:[],full:true,today:'2026-10-02T10:00:00'})));"
        )
        import json
        task = json.loads(subprocess.check_output(["node", "-e", script], text=True))[0]
        self.assertEqual(task["planError"], "Person fehlt")

    def test_unknown_people_stay_visible_and_shopping_lists_are_left_alone(self):
        self.assertEqual(self.parse("Fahrrad @Niemand")[0]["unknownUser"], "Niemand")
        self.assertEqual(self.parse("Milch @Kim morgen", full=False), [{"title": "Milch @Kim morgen", "completed": False}])


if __name__ == "__main__":
    unittest.main()
