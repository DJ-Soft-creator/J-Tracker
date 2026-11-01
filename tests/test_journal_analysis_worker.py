"""Tageskette des Analyse-Workers, ohne Netz und ohne Datenbank."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import journal_analysis_worker as worker  # noqa: E402


class FakeRuns:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.completed = []
        self.failed = []
        self.notified = []
        self.handoff = []
        self.stored = []

    def list_runs(self, owner_id, *, job_id, limit, engine=None):
        return self.existing

    def complete_run(self, owner_id, job_id, day, *, result, summary, state, source_hash,
                     engine=None, data_root=None, now=None, notify=True):
        self.completed.append({"day": day.isoformat(), "result": result, "summary": summary,
                               "state": state, "hash": source_hash})

    def fail_run(self, owner_id, job_id, day, *, error_code, error_message,
                 engine=None, data_root=None, now=None):
        self.failed.append({"day": day.isoformat(), "error": error_message})

    def notify_run(self, owner_id, job, *, day, title, body, run_id=None,
                   engine=None, data_root=None, now=None, notify_at=None):
        self.notified.append({"day": day.isoformat() if hasattr(day, "isoformat") else str(day),
                              "title": title, "body": body, "at": notify_at})
        return True

    def notify_reminder(self, owner_id, job, *, day, run_id, title, body, notify_at=None,
                        engine=None, data_root=None, now=None, any_job=False):
        self.notified.append({"day": day, "title": title, "body": body, "at": notify_at,
                              "any_job": any_job, "job": job.get("id")})
        return True

    def recent_reminders(self, owner_id, *, start, end, engine=None):
        return list(self.stored)

    def set_handoff_summary(self, owner_id, job_id, text, *, engine=None, now=None,
                            data_root=None):
        self.handoff.append(text)
        return text


class ParseTests(unittest.TestCase):
    def test_plain_json_object(self):
        result, summary, state, reminders = worker.parse_response(
            json.dumps({"result": "R", "summary": "S", "state": {"open_tasks": ["x"]}}))
        self.assertEqual((result, summary), ("R", "S"))
        self.assertEqual(state, {"open_tasks": ["x"]})
        self.assertEqual(reminders, [])

    def test_json_inside_a_code_fence(self):
        text = '```json\n{"result": "R", "summary": "S", "state": {}}\n```'
        result, summary, state, reminders = worker.parse_response(text)
        self.assertEqual((result, summary, state), ("R", "S", {}))
        self.assertEqual(reminders, [])

    def test_json_embedded_in_prose_is_still_found(self):
        text = 'Hier das Ergebnis:\n{"result": "R", "summary": "S", "state": {"a": 1}}\nEnde.'
        result, summary, state, reminders = worker.parse_response(text)
        self.assertEqual((result, summary, state), ("R", "S", {"a": 1}))
        self.assertEqual(reminders, [])

    def test_plain_text_falls_back_to_result_without_state(self):
        result, summary, state, reminders = worker.parse_response("Nur Fließtext")
        self.assertEqual(result, "Nur Fließtext")
        self.assertEqual(summary, "Nur Fließtext")
        self.assertIsNone(state)
        self.assertEqual(reminders, [])

    def test_new_reminders_only_in_the_state_still_become_events(self):
        """Fuellt das Modell nur ``state.reminders``, zaehlen die neuen Eintraege."""
        previous = {"reminders": [{"text": "Zahnarzt", "at": "2026-09-30T09:00"}]}
        new_state = {"reminders": [
            {"text": "Zahnarzt", "at": "2026-09-30T09:00"},   # fortgeschrieben
            {"text": "Paket abholen", "at": "2026-10-03"},   # neu
            {"text": "Ohne Termin"},                           # ohne Zeitpunkt
            "nur ein Text",
        ]}
        events = worker.new_state_reminders(previous, new_state)
        self.assertEqual([item["text"] for item in events], ["Paket abholen"])
        self.assertEqual(events[0]["at"].strftime("%Y-%m-%d %H:%M"), "2026-10-03 09:00")
        self.assertEqual(worker.new_state_reminders(None, None), [])
        self.assertEqual(worker.new_state_reminders({}, {"reminders": "kaputt"}), [])

    def test_sentences_in_the_state_become_events_via_the_rule_parser(self):
        """PROD-Modelle schreiben das Gedaechtnis als Saetze, nicht als Objekte."""
        events = worker.new_state_reminders(
            {"reminders": ["Alt: Freitag Muell"]},
            {"reminders": ["Alt: Freitag Muell", "Mittwoch 23.09. 13 Uhr: Zahnarzt", "Ohne Termin"]},
            date(2026, 9, 22))
        self.assertEqual([entry["text"] for entry in events], ["Zahnarzt"])
        self.assertEqual(events[0]["at"].strftime("%Y-%m-%dT%H:%M"), "2026-09-23T13:00")

    def test_past_reminders_are_pruned_from_the_state(self):
        state = {"open_tasks": ["a"], "reminders": [
            {"text": "alt", "at": "2026-09-01T09:00"}, "Zahnarzt am 01.09.2026",
            "Morgen X anrufen", {"text": "neu", "at": "2026-09-30T09:00"}]}
        pruned = worker.prune_state_reminders(state, date(2026, 9, 22))
        self.assertEqual(pruned["reminders"], ["Morgen X anrufen", {"text": "neu", "at": "2026-09-30T09:00"}])
        self.assertEqual(pruned["open_tasks"], ["a"])
        many = {"reminders": [f"Punkt {index}" for index in range(30)]}
        self.assertEqual(len(worker.prune_state_reminders(many, date(2026, 9, 22))["reminders"]),
                         worker.MAX_STATE_REMINDERS)

    def test_reminders_are_extracted_with_an_absolute_time(self):
        payload = {"result": "R", "summary": "S", "state": {},
                   "reminders": [{"text": "Recherche Thema X", "at": "2026-09-29T09:00"},
                                  {"text": "Termin ohne Zeit", "at": "quatsch"}]}
        _result, _summary, _state, reminders = worker.parse_response(json.dumps(payload))
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "Recherche Thema X")
        self.assertEqual(reminders[0]["at"].strftime("%Y-%m-%dT%H:%M"), "2026-09-29T09:00")

    def test_a_plain_date_gets_the_default_notify_time(self):
        payload = {"result": "R", "reminders": [{"text": "Zahltag", "at": "2026-10-01"}]}
        _result, _summary, _state, reminders = worker.parse_response(json.dumps(payload))
        self.assertEqual(reminders[0]["at"].strftime("%H:%M"), "09:00")


class PromptTests(unittest.TestCase):
    def test_free_prompt_still_gets_day_summary_state_and_sources(self):
        _system, text = worker.build_prompt("Pruefe die Funktion.", "2026-09-14", "S", {"a": 1},
                                            "JOURNALTEXT")
        self.assertIn("Pruefe die Funktion.", text)
        self.assertIn("JOURNALTEXT", text)
        self.assertIn("2026-09-14", text)
        self.assertIn("Bisherige Zusammenfassung", text)
        self.assertIn("## Quellen des Tages", text)

    def test_template_with_placeholders_is_not_duplicated(self):
        _system, text = worker.build_prompt("Tag {day}: {sources}", "2026-09-14", "S", {},
                                            "JOURNALTEXT")
        self.assertEqual(text.count("JOURNALTEXT"), 1)
        self.assertNotIn("## Quellen des Tages", text)

    def test_the_prompt_carries_a_calendar_and_the_known_reminders(self):
        known = [{"text": "Thomas anrufen", "at": datetime(2026, 9, 23, 10, 0)}]
        _system, text = worker.build_prompt("Tag {day}", "2026-09-22", "S", {}, "J",
                                            known_reminders=known)
        self.assertIn("Journaltag: Dienstag, 2026-09-22", text)
        self.assertIn("Nächste Woche: Montag 2026-09-28", text)
        self.assertIn("- 2026-09-23T10:00 – Thomas anrufen", text)

    def test_journal_is_an_alias_for_the_source_block(self):
        _system, text = worker.build_prompt("Lies {journal}", "2026-09-14", "S", {}, "JOURNALTEXT")
        self.assertIn("JOURNALTEXT", text)
        self.assertNotIn("## Quellen des Tages", text)


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.job = {
            "id": "job-1", "owner_id": "david", "name": "Test",
            "date_range_mode": "custom", "custom_start": "2026-09-22",
            "custom_end": "2026-09-23", "extra_sources": [], "summary": "", "state": {},
        }
        self._write_day("2026-09-22", "Tag eins")
        self._write_day("2026-09-23", "Tag zwei")
        self.original_runs = worker.runs
        self.fake = FakeRuns()
        worker.runs = self.fake
        self.prompts = []

    def tearDown(self):
        worker.runs = self.original_runs
        self.temp.cleanup()

    def _write_day(self, day, text):
        value = date.fromisoformat(day)
        directory = self.root / "david" / value.strftime("%Y") / value.strftime("%m") / value.strftime("%d")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"Journal_{day}.md").write_text(text, encoding="utf-8")

    def _call_ai(self, counter):
        def call(provider, ai_function, user_message):
            self.prompts.append(user_message)
            index = len(self.prompts)
            return json.dumps({"result": f"R{index}", "summary": f"S{index}",
                               "state": {"open_tasks": [f"t{index}"]}})
        return call

    def test_each_day_is_analyzed_and_the_summary_is_carried_forward(self):
        report = worker.run_job("david", self.job, today=date(2026, 9, 24),
                                call_ai=self._call_ai(1), provider={}, data_root=self.root)
        self.assertEqual(report["processed"], 2)
        self.assertEqual([item["day"] for item in self.fake.completed],
                         ["2026-09-22", "2026-09-23"])
        # Der zweite Tag sieht die Zusammenfassung des ersten.
        self.assertIn("S1", self.prompts[1])
        self.assertIn("t1", self.prompts[1])
        self.assertEqual(self.fake.completed[1]["summary"], "S2")
        self.assertTrue(self.fake.completed[0]["hash"])

    def test_a_completed_day_is_skipped_and_its_context_reused(self):
        self.fake.existing = [{"run_date": "2026-09-22", "status": "completed", "stale": False,
                               "summary": "vorher", "state": {"open_tasks": ["alt"]}}]
        report = worker.run_job("david", self.job, today=date(2026, 9, 24),
                                call_ai=self._call_ai(1), provider={}, data_root=self.root)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["processed"], 1)
        self.assertEqual(len(self.prompts), 1)
        self.assertIn("vorher", self.prompts[0])
        self.assertIn("alt", self.prompts[0])

    def test_a_stale_day_is_analyzed_again(self):
        self.fake.existing = [{"run_date": "2026-09-22", "status": "completed", "stale": True,
                               "summary": "alt", "state": {}}]
        report = worker.run_job("david", self.job, today=date(2026, 9, 24),
                                call_ai=self._call_ai(2), provider={}, data_root=self.root)
        self.assertEqual(report["processed"], 2)
        self.assertEqual(self.fake.completed[0]["day"], "2026-09-22")

    def test_an_empty_day_is_completed_without_a_model_call(self):
        self._write_day("2026-09-23", "   \n")
        report = worker.run_job("david", self.job, today=date(2026, 9, 24),
                                call_ai=self._call_ai(1), provider={}, data_root=self.root)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual((report["processed"], report["empty"]), (1, 1))
        self.assertEqual(self.fake.completed[1]["result"], worker.EMPTY_DAY_RESULT)
        # Zusammenfassung und Zustand laufen unveraendert weiter.
        self.assertEqual(self.fake.completed[1]["summary"], "S1")

    def test_a_changed_day_is_analyzed_again_an_unchanged_one_not(self):
        unchanged = worker.sources_module.content_hash({"journal": "Tag eins"})
        outdated = worker.sources_module.content_hash({"journal": "Tag zwei, vor dem Nachtrag"})
        self.fake.existing = [
            {"run_date": "2026-09-22", "status": "completed", "stale": False,
             "summary": "vorher", "state": {}, "source_hash": unchanged},
            {"run_date": "2026-09-23", "status": "completed", "stale": False,
             "summary": "alt", "state": {}, "source_hash": outdated},
        ]
        report = worker.run_job("david", self.job, today=date(2026, 9, 24),
                                call_ai=self._call_ai(1), provider={}, data_root=self.root)
        self.assertEqual((report["skipped"], report["processed"]), (1, 1))
        self.assertEqual(report["days"][1], {"day": "2026-09-23", "status": "completed",
                                             "reason": "changed"})
        self.assertIn("vorher", self.prompts[0])

    def test_output_modes_are_applied_per_day(self):
        calls = []
        original_journal = worker.output.write_journal_entry
        original_family = worker.output.write_family_note
        worker.output.write_journal_entry = lambda owner, day, job, result, **k: calls.append(
            ("journal", day.isoformat()))
        worker.output.write_family_note = lambda target, job, day, result, **k: calls.append(
            ("family", day.isoformat()))
        try:
            job = dict(self.job, output_journal=True, output_family=True,
                       family_target="Notizen.md")
            report = worker.run_job("david", job, today=date(2026, 9, 24),
                                    call_ai=self._call_ai(1), provider={}, data_root=self.root)
        finally:
            worker.output.write_journal_entry = original_journal
            worker.output.write_family_note = original_family
        self.assertEqual([kind for kind, _ in calls], ["journal", "family", "journal", "family"])
        self.assertNotIn("output_errors", report)

    def test_an_output_error_does_not_fail_the_run(self):
        def boom(*args, **kwargs):
            raise OSError("kein Zugriff")

        original = worker.output.write_journal_entry
        worker.output.write_journal_entry = boom
        try:
            report = worker.run_job("david", dict(self.job, output_journal=True),
                                    today=date(2026, 9, 24), call_ai=self._call_ai(1),
                                    provider={}, data_root=self.root)
        finally:
            worker.output.write_journal_entry = original
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["processed"], 2)
        self.assertEqual(len(report["output_errors"]), 2)

    def test_force_reanalyzes_completed_days(self):
        self.fake.existing = [{"run_date": "2026-09-22", "status": "completed", "stale": False,
                               "summary": "alt", "state": {}}]
        report = worker.run_job("david", self.job, today=date(2026, 9, 24),
                                call_ai=self._call_ai(1), provider={}, data_root=self.root,
                                force=True)
        self.assertEqual(report["processed"], 2)
        self.assertEqual(report["skipped"], 0)

    def test_one_notification_at_the_end_of_a_multi_day_run(self):
        worker.run_job("david", dict(self.job, output_notification=True, output_run_summary=True),
                       today=date(2026, 9, 24), call_ai=self._call_ai(1), provider={},
                       data_root=self.root)
        self.assertEqual(len(self.fake.notified), 1)
        self.assertIn("2026-09-22", self.fake.notified[0]["title"])
        self.assertIn("2026-09-23", self.fake.notified[0]["title"])
        # Eine Synthese, keine Tagesauflistung: der abschliessende Modellaufruf
        # (dritter Aufruf) liefert S3.
        body = self.fake.notified[0]["body"]
        self.assertEqual(body, "S3")
        self.assertNotIn("### 2026-09-22", body)

    def test_final_summary_falls_back_to_the_rolling_summary_on_error(self):
        calls = {"n": 0}

        def call(provider, ai_function, user_message):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise ConnectionError("summary failed")
            return json.dumps({"result": f"R{calls['n']}", "summary": f"S{calls['n']}", "state": {}})

        worker.run_job("david", dict(self.job, output_notification=True, output_run_summary=True),
                       today=date(2026, 9, 24), call_ai=call, provider={}, data_root=self.root)
        self.assertEqual(self.fake.notified[0]["body"], "S2")

    def test_summary_switch_off_uses_the_rolling_summary_without_extra_call(self):
        report = worker.run_job("david", dict(self.job, output_notification=True,
                                               output_run_summary=False),
                                today=date(2026, 9, 24), call_ai=self._call_ai(1), provider={},
                                data_root=self.root)
        self.assertEqual(report["processed"], 2)
        # Nur die zwei Tagesaufrufe, kein dritter.
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(self.fake.notified[0]["body"], "S2")

    def test_reminders_create_one_scheduled_notification_per_event(self):
        def call(provider, ai_function, user_message):
            first = len(self.prompts) == 0
            reminders = ([{"text": "Recherche Thema X", "at": "2026-09-29T09:00"}]
                         if first else
                         [{"text": "Zahltag", "at": "2026-09-24T14:00"},
                          {"text": "Ohne Zeit", "at": "kaputt"}])
            self.prompts.append(user_message)
            return json.dumps({"result": "R", "summary": "S", "state": {},
                               "reminders": reminders})

        report = worker.run_job("david", dict(self.job, output_notification=True,
                                               output_run_summary=False),
                                today=date(2026, 9, 24), call_ai=call, provider={},
                                data_root=self.root)
        # Zeitlich weit auseinander -> je Ereignis eine Meldung, chronologisch;
        # kein Rückfall auf die Zusammenfassung.
        self.assertEqual([entry["day"] for entry in self.fake.notified],
                         ["2026-09-23", "2026-09-22"])
        self.assertEqual(report["notified"], 2)
        times = [entry["at"].strftime("%Y-%m-%dT%H:%M") for entry in self.fake.notified]
        self.assertEqual(times, ["2026-09-24T14:00", "2026-09-29T09:00"])
        self.assertNotIn("Ohne Zeit", [entry["body"] for entry in self.fake.notified])

    def test_reminders_only_in_the_carried_state_notify_once_each(self):
        """Ein Modell, das nur ``state.reminders`` fortschreibt, meldet jede Erinnerung einmal."""
        def call(provider, ai_function, user_message):
            first = len(self.prompts) == 0
            carried = [{"text": "Recherche Thema X", "at": "2026-09-29T09:00"}]
            if not first:
                carried = carried + [{"text": "Zahltag", "at": "2026-09-26"}]
            self.prompts.append(user_message)
            return json.dumps({"result": "R", "summary": "S", "state": {"reminders": carried}})

        worker.run_job("david", dict(self.job, output_notification=True, output_run_summary=False),
                       today=date(2026, 9, 24), call_ai=call, provider={}, data_root=self.root)
        self.assertEqual([entry["body"] for entry in self.fake.notified],
                         ["Zahltag", "Recherche Thema X"])

    def test_reminders_carry_the_source_day_and_run_backend_attribute(self):
        def call(provider, ai_function, user_message):
            day = "2026-09-22" if not self.prompts else "2026-09-23"
            self.prompts.append(user_message)
            return json.dumps({"result": "R", "summary": "S", "state": {},
                               "reminders": [{"text": f"Aktion aus {day}",
                                                "at": "2026-10-01T08:30"}]})

        worker.run_job("david", dict(self.job, output_notification=True,
                                      output_run_summary=False),
                       today=date(2026, 9, 24), call_ai=call, provider={},
                       data_root=self.root)
        # Gleicher Zeitpunkt -> eine gebündelte Meldung über beide Quelltage.
        self.assertEqual([entry["title"] for entry in self.fake.notified],
                         ["Test · 2026-09-22 – 2026-09-23"])
        self.assertEqual(self.fake.notified[0]["day"], "2026-09-23")
        self.assertEqual(self.fake.notified[0]["body"],
                         "• 08:30 – Aktion aus 2026-09-22\n• 08:30 – Aktion aus 2026-09-23")

    def test_reminders_close_together_are_bundled_and_night_waits_for_morning(self):
        def call(provider, ai_function, user_message):
            reminders = [] if self.prompts else [
                {"text": "A", "at": "2026-10-01T09:00"},
                {"text": "B", "at": "2026-10-01T10:30"},
                {"text": "C", "at": "2026-10-01T12:00"},
                {"text": "Nachts", "at": "2026-10-01T23:15"},
                {"text": "Frueh", "at": "2026-10-02T06:00"},
            ]
            self.prompts.append(user_message)
            return json.dumps({"result": "R", "summary": "S", "state": {},
                               "reminders": reminders})

        report = worker.run_job("david", dict(self.job, output_notification=True,
                                               output_run_summary=False),
                                today=date(2026, 9, 24), call_ai=call, provider={},
                                data_root=self.root)
        self.assertEqual(report["notified"], 3)
        times = [entry["at"].strftime("%Y-%m-%dT%H:%M") for entry in self.fake.notified]
        self.assertEqual(times, ["2026-10-01T09:00", "2026-10-01T12:00", "2026-10-02T07:00"])
        self.assertEqual([entry["body"] for entry in self.fake.notified], [
            "• 09:00 – A\n• 10:30 – B",
            "C",
            "• 01.10. 23:15 – Nachts\n• 06:00 – Frueh",
        ])

    def test_an_explicit_journal_reminder_is_scheduled_without_the_model(self):
        self._write_day("2026-09-22", "Erinnere mich morgen 10 Uhr daran, Thomas anzurufen.")

        def call(provider, ai_function, user_message):
            self.prompts.append(user_message)
            # Das Modell spricht die feste Erinnerung nach - sie darf nicht doppelt kommen.
            return json.dumps({"result": "R", "summary": "S", "state": {}, "reminders": [
                {"text": "Thomas anrufen", "at": "2026-09-23T10:00"}] if len(self.prompts) == 1 else []})

        report = worker.run_job("david", dict(self.job, output_notification=True,
                                               output_run_summary=False),
                                today=date(2026, 9, 24), call_ai=call, provider={},
                                data_root=self.root, now=datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc))
        rule = [entry for entry in self.fake.notified if entry["title"].startswith("Journal-Erinnerung")]
        self.assertEqual(len(rule), 1)
        self.assertEqual(rule[0]["body"], "Thomas anzurufen")
        self.assertEqual(rule[0]["at"].strftime("%Y-%m-%dT%H:%M"), "2026-09-23T10:00")
        self.assertTrue(rule[0]["any_job"])
        self.assertIn("## Bereits angelegte Erinnerungen", self.prompts[0])
        self.assertEqual(report["journal_reminders"], 1)
        self.assertNotIn("Thomas anrufen", [entry["body"] for entry in self.fake.notified])

    def test_a_reminder_already_delivered_from_another_day_is_not_repeated(self):
        self.fake.stored = [{"run_date": "2026-09-20", "body": "Recherche Thema X",
                             "notify_at": "2026-09-29T09:00:00+02:00"}]

        def call(provider, ai_function, user_message):
            self.prompts.append(user_message)
            return json.dumps({"result": "R", "summary": "S", "state": {}, "reminders": [
                {"text": "Recherche Thema X", "at": "2026-09-29T09:00"}]})

        report = worker.run_job("david", dict(self.job, output_notification=True,
                                               output_run_summary=False),
                                today=date(2026, 9, 24), call_ai=call, provider={},
                                data_root=self.root)
        self.assertEqual(report["notified"], 0)
        self.assertEqual(self.fake.notified, [])

    def test_the_scan_plans_todays_reminders_on_the_previous_day_job(self):
        self._write_day("2026-09-23", "Einkaufen. Erinnere mich heute um 15 Uhr an das Paket.")
        jobs = [
            {"id": "woche", "owner_id": "david", "output_notification": True,
             "date_range_mode": "current_week", "created_at": "2026-01-01"},
            {"id": "vortag", "owner_id": "david", "output_notification": True,
             "date_range_mode": "previous_day", "created_at": "2026-02-01"},
            {"id": "still", "owner_id": "anna", "output_notification": False},
        ]
        with mock.patch.object(worker.ja, "list_enabled_jobs", return_value=jobs):
            created = worker.scan_journal_reminders(
                now=datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc), data_root=self.root)
        self.assertEqual(created, 1)
        self.assertEqual(self.fake.notified[0]["job"], "vortag")
        self.assertEqual(self.fake.notified[0]["body"], "Das Paket")
        self.assertEqual(self.fake.notified[0]["title"], "Journal-Erinnerung · 2026-09-23")

    def test_the_scan_skips_reminders_long_past(self):
        self._write_day("2026-09-23", "Erinnere mich heute um 9 Uhr an das Paket.")
        jobs = [{"id": "vortag", "owner_id": "david", "output_notification": True}]
        with mock.patch.object(worker.ja, "list_enabled_jobs", return_value=jobs):
            created = worker.scan_journal_reminders(
                now=datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc), data_root=self.root)
        self.assertEqual(created, 0)

    def test_carry_summary_stores_and_reuses_the_handoff(self):
        worker.run_job("david", dict(self.job, carry_summary=True, output_notification=False,
                                      output_run_summary=True),
                       today=date(2026, 9, 24), call_ai=self._call_ai(1), provider={},
                       data_root=self.root)
        self.assertEqual(self.fake.handoff, ["S3"])
        self.prompts.clear()
        worker.run_job("david", dict(self.job, carry_summary=True, output_notification=False,
                                      output_run_summary=False, handoff_summary="UEBERGABE"),
                       today=date(2026, 9, 24), call_ai=self._call_ai(9), provider={},
                       data_root=self.root)
        self.assertIn("UEBERGABE", self.prompts[0])

    def test_notes_output_receives_the_run_summary(self):
        calls = []
        original = worker.output.write_notes_entry
        worker.output.write_notes_entry = lambda owner, job, days, body, **k: calls.append(body)
        try:
            worker.run_job("david", dict(self.job, output_notes=True, output_run_summary=True,
                                          output_notification=False),
                           today=date(2026, 9, 24), call_ai=self._call_ai(1), provider={},
                           data_root=self.root)
        finally:
            worker.output.write_notes_entry = original
        self.assertEqual(calls, ["S3"])

    def test_without_notification_mode_no_run_notification_is_created(self):
        worker.run_job("david", dict(self.job, output_notification=False),
                       today=date(2026, 9, 24), call_ai=self._call_ai(1), provider={},
                       data_root=self.root)
        self.assertEqual(self.fake.notified, [])

    def test_the_job_model_overrides_the_provider_model(self):
        seen = []

        def call(provider, ai_function, user_message):
            seen.append(provider.get("model"))
            return json.dumps({"result": "R", "summary": "S", "state": {}})

        worker.run_job("david", dict(self.job, model="gewaehlt"), today=date(2026, 9, 24),
                       call_ai=call, provider={"model": "anbieter"}, data_root=self.root)
        self.assertTrue(seen)
        self.assertTrue(all(model == "gewaehlt" for model in seen), seen)

    def test_without_a_job_model_the_provider_model_is_used(self):
        seen = []

        def call(provider, ai_function, user_message):
            seen.append(provider.get("model"))
            return json.dumps({"result": "R", "summary": "S", "state": {}})

        worker.run_job("david", self.job, today=date(2026, 9, 24), call_ai=call,
                       provider={"model": "anbieter"}, data_root=self.root)
        self.assertTrue(all(model == "anbieter" for model in seen), seen)

    def test_the_provider_token_budget_is_used(self):
        seen = []

        def call(provider, ai_function, user_message):
            seen.append(ai_function.get("max_tokens"))
            return json.dumps({"result": "R", "summary": "S", "state": {}})

        worker.run_job("david", self.job, today=date(2026, 9, 24), call_ai=call,
                       provider={"model": "m", "max_tokens": 12345}, data_root=self.root)
        self.assertTrue(all(budget == 12345 for budget in seen), seen)

    def test_the_analysis_allows_a_longer_model_call(self):
        seen = []

        def call(provider, ai_function, user_message):
            seen.append((ai_function.get("stream"), ai_function.get("timeout"),
                         ai_function.get("max_duration"), ai_function.get("require_content")))
            return json.dumps({"result": "R", "summary": "S", "state": {}})

        worker.run_job("david", self.job, today=date(2026, 9, 24), call_ai=call,
                       provider={"model": "m"}, data_root=self.root)
        # Gestreamt: der Timeout ist die Leerlaufgrenze, die Gesamtdauer eigen.
        # require_content: Denktext ohne Antwort ist kein Analyseergebnis.
        expected = (True, worker.AI_REQUEST_TIMEOUT, worker.AI_MAX_DURATION, True)
        self.assertTrue(seen and all(item == expected for item in seen), seen)

    def test_the_total_limit_is_never_below_the_idle_limit(self):
        with mock.patch.dict(os.environ, {"JOURNAL_ANALYSIS_AI_MAX_DURATION": "7200"}):
            self.assertEqual(worker._ai_max_duration(), 7200)
        with mock.patch.dict(os.environ, {"JOURNAL_ANALYSIS_AI_MAX_DURATION": "60"}):
            self.assertEqual(worker._ai_max_duration(), worker.AI_REQUEST_TIMEOUT)

    def test_the_analysis_timeout_is_configurable(self):
        with mock.patch.dict(os.environ, {"JOURNAL_ANALYSIS_AI_TIMEOUT": "600"}):
            self.assertEqual(worker._ai_timeout(), 600)
        # Ungueltige oder zu kleine Werte fallen auf den Standard zurueck.
        for value in ("", "abc", "-5", "10"):
            with mock.patch.dict(os.environ, {"JOURNAL_ANALYSIS_AI_TIMEOUT": value}):
                self.assertEqual(worker._ai_timeout(), worker.AI_REQUEST_TIMEOUT)

    def test_a_second_run_is_rejected_while_the_lock_is_held(self):
        import fcntl
        import journal_analysis_store as store

        directory = store.job_root("david", "job-1", root=self.root)
        directory.mkdir(parents=True, exist_ok=True)
        handle = open(directory / ".run.lock", "a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(worker.JobBusyError):
                worker.run_job("david", self.job, today=date(2026, 9, 24),
                               call_ai=self._call_ai(1), provider={}, data_root=self.root)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_cancellation_stops_the_chain_before_the_first_day(self):
        report = worker.run_job("david", self.job, today=date(2026, 9, 24),
                                call_ai=self._call_ai(1), provider={}, data_root=self.root,
                                is_cancelled=lambda: True)
        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(report["processed"], 0)
        self.assertEqual(self.fake.completed, [])

    def test_a_provider_error_stops_the_chain(self):
        def boom(provider, ai_function, user_message):
            raise ConnectionError("Endpunkt weg")

        report = worker.run_job("david", self.job, today=date(2026, 9, 24),
                                call_ai=boom, provider={}, data_root=self.root)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed"], 1)
        self.assertEqual(self.fake.completed, [])
        self.assertEqual(self.fake.failed[0]["day"], "2026-09-22")


if __name__ == "__main__":
    unittest.main()