"""Feste Erkennung von Erinnerungen im Journaltext (ohne KI, ohne Datenbank)."""

import sys
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import journal_reminders as jr  # noqa: E402


# Ein Mittwoch: "nächste Woche Mittwoch" und "Mittwoch" fallen auseinander.
DAY = date(2026, 10, 21)


def found(text, day=DAY):
    return [(entry["at"].strftime("%Y-%m-%d %H:%M"), entry["text"])
            for entry in jr.find_reminders(text, day)]


class FindRemindersTests(unittest.TestCase):
    def test_the_two_everyday_cases(self):
        self.assertEqual(found("Erinnere mich morgen 10 Uhr daran, bei Thomas anzurufen."),
                         [("2026-10-22 10:00", "Bei Thomas anzurufen")])
        self.assertEqual(found("Erinnerung: nächste Woche Mittwoch 13 Uhr Zahnarzt anrufen"),
                         [("2026-10-28 13:00", "Zahnarzt anrufen")])

    def test_relative_days_count_from_the_journal_day(self):
        cases = {
            "Erinnere mich übermorgen abends an den Müll": "2026-10-23 18:00",
            "Erinnere mich in zwei Wochen an die Rechnung": "2026-11-04 09:00",
            "Erinnere mich in 3 Tagen an die Post": "2026-10-24 09:00",
            "Erinnere mich Freitag halb 8 an das Training": "2026-10-23 07:30",
            "Erinnere mich Mittwoch an den Termin": "2026-10-28 09:00",
            "Erinnere mich Mittwoch nächste Woche um 8 abends an den Anruf": "2026-10-28 20:00",
            "Erinnere mich übernächste Woche Montag an die Steuer": "2026-11-02 09:00",
            "Erinnere mich nächste Woche an das Angebot": "2026-10-26 09:00",
            "Erinnere mich am Wochenende an den Rasen": "2026-10-24 09:00",
            "Morgen früh Brötchen holen, nicht vergessen!": "2026-10-22 08:00",
        }
        for sentence, expected in cases.items():
            with self.subTest(sentence=sentence):
                self.assertEqual([at for at, _text in found(sentence)], [expected])

    def test_absolute_dates_and_times(self):
        self.assertEqual(found("- [ ] erinnere mich am 24.10. um 9:30 an die Steuer #finanzen"),
                         [("2026-10-24 09:30", "Die Steuer")])
        self.assertEqual(found("Erinnerung am Sonntag, 25. Oktober 2026 um 11:00 Brunch"),
                         [("2026-10-25 11:00", "Brunch")])
        self.assertEqual(found("Erinnere mich 2026-10-30T18:00 an die Deko"),
                         [("2026-10-30 18:00", "Die Deko")])
        # Ein vergangenes Datum ohne Jahr meint das naechste Jahr.
        self.assertEqual(found("Erinnere mich am 2.1. an die Inventur")[0][0], "2027-01-02 09:00")

    def test_today_needs_a_time(self):
        self.assertEqual(found("Erinnere mich heute um 15 Uhr an das Paket."),
                         [("2026-10-21 15:00", "Das Paket")])
        self.assertEqual(found("Erinnerung an Oma, die heute 90 geworden wäre."), [])

    def test_no_reminder_without_trigger_or_for_past_and_done_items(self):
        self.assertEqual(found("Morgen um 10 Uhr Zahnarzt."), [])
        self.assertEqual(found("Ich habe mich heute an den Urlaub erinnert."), [])
        self.assertEqual(found("- [x] erinnere mich morgen 10 Uhr an X"), [])
        self.assertEqual(found("Heute morgen war schön."), [])

    def test_each_sentence_is_its_own_reminder(self):
        text = ("Heute war viel los. Erinnere mich morgen 10 Uhr an Anna.\n"
                "Erinnere mich am 24.10. an Ben. Danach Kaffee.")
        self.assertEqual(found(text), [("2026-10-22 10:00", "Anna"), ("2026-10-24 09:00", "Ben")])


class ParseMomentTests(unittest.TestCase):
    def test_without_relative_words_only_absolute_dates_count(self):
        moment, _spans, explicit = jr.parse_moment("Mittwoch 22.10. 13 Uhr: Zahnarzt", DAY,
                                                   relative=False)
        self.assertEqual(moment, datetime(2026, 10, 22, 13, 0))
        self.assertTrue(explicit)
        self.assertIsNone(jr.parse_moment("Morgen 10 Uhr X anrufen", DAY, relative=False))

    def test_a_time_after_a_date_is_not_mistaken_for_a_date(self):
        moment, _spans, _explicit = jr.parse_moment("am 23.10. um 13.10 Uhr", DAY)
        self.assertEqual(moment, datetime(2026, 10, 23, 13, 10))


class CompareTests(unittest.TestCase):
    def test_similar_and_related(self):
        self.assertTrue(jr.similar("Recherche Thema X", "recherche thema x!"))
        self.assertFalse(jr.similar("Aktion aus 2026-09-22", "Aktion aus 2026-09-23"))
        self.assertTrue(jr.related("Thomas anrufen", "Bei Thomas anzurufen"))
        self.assertFalse(jr.related("Paket holen", "Steuer machen"))

    def test_calendar_block_names_the_weekdays(self):
        block = jr.calendar_block(DAY)
        self.assertIn("Journaltag: Mittwoch, 2026-10-21 (KW 43)", block)
        self.assertIn("morgen = Donnerstag 2026-10-22", block)
        self.assertIn("Nächste Woche: Montag 2026-10-26 bis Sonntag 2026-11-01", block)


if __name__ == "__main__":
    unittest.main()
