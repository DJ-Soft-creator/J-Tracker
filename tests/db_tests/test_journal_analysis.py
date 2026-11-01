"""DB-Integrationstests der Journal-Analyse-Verwaltung (Etappe 2)."""

import os
import sys
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
import journal_analysis as ja  # noqa: E402
import journal_analysis_runs as ja_runs  # noqa: E402
import journal_analysis_store as store  # noqa: E402


DATABASE_URL = os.environ["DATABASE_URL"]


class JournalAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", DATABASE_URL)
        command.upgrade(config, "head")

    def setUp(self):
        self.engine = create_engine(DATABASE_URL)
        self.owner = f"ja-{uuid.uuid4().hex[:12]}"
        self.temp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.temp.name)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _create(self, **overrides):
        values = {"name": "Pendel-Check", "date_range_mode": "previous_day"}
        values.update(overrides)
        return ja.create_job(self.owner, values, engine=self.engine, data_root=self.data_root)

    def test_create_job_defaults_and_file_export(self):
        job = self._create()
        self.assertEqual(job["slug"], "pendel-check")
        self.assertEqual(job["schedule_mode"], "daily")
        self.assertEqual(job["schedule_time"], "06:00")
        self.assertTrue(job["output_notification"])
        self.assertEqual(job["job_version"], 1)
        exported = store.read_job(self.owner, "pendel-check", root=self.data_root)
        self.assertEqual(exported["name"], "Pendel-Check")

    def test_slug_collision_gets_a_suffix(self):
        first = self._create(name="Gleicher Name")
        second = self._create(name="Gleicher Name")
        self.assertNotEqual(first["slug"], second["slug"])

    def test_custom_range_and_family_target_are_validated(self):
        with self.assertRaises(ja.JournalAnalysisError):
            self._create(date_range_mode="custom")
        with self.assertRaises(ja.JournalAnalysisError):
            self._create(date_range_mode="custom", custom_start="2026-09-10", custom_end="2026-09-01")
        with self.assertRaises(ja.JournalAnalysisError):
            self._create(output_family=True)
        job = self._create(date_range_mode="custom", custom_start="2026-09-01",
                           custom_end="2026-09-10", output_family=True, family_target="familie/notiz.md")
        self.assertEqual(job["family_target"], "familie/notiz.md")

    def test_last_days_range_and_day_sources_are_validated(self):
        job = self._create(date_range_mode="last_days",
                           extra_sources=["/W-Tracker-data/{YYYY}/{MM}/{DD}/Weedtracker_{day}.md"])
        self.assertEqual(job["range_days"], 2)
        self.assertEqual(job["extra_sources"], ["W-Tracker-data/{YYYY}/{MM}/{DD}/Weedtracker_{day}.md"])
        updated = ja.update_job(self.owner, job["id"], {"range_days": 5},
                                engine=self.engine, data_root=self.data_root)
        self.assertEqual(updated["range_days"], 5)
        with self.assertRaises(ja.JournalAnalysisError):
            self._create(date_range_mode="last_days", range_days=0)
        with self.assertRaises(ja.JournalAnalysisError):
            self._create(extra_sources=["Tracker/{JJJJ}.md"])

    def test_custom_schedule_rule_is_validated_and_stored(self):
        with self.assertRaises(ja.JournalAnalysisError):
            self._create(schedule_mode="custom")
        with self.assertRaises(ja.JournalAnalysisError):
            self._create(schedule_mode="custom", schedule_rule={"kind": "weekly", "weekdays": [7]})
        job = self._create(schedule_mode="custom", schedule_time="19:00",
                           schedule_rule={"kind": "weekly", "weekdays": [4, "4"]})
        self.assertEqual(job["schedule_rule"], {"kind": "weekly", "weekdays": [4]})
        updated = ja.update_job(self.owner, job["id"],
                                {"schedule_rule": {"kind": "monthly", "days": [1]}},
                                engine=self.engine, data_root=self.data_root)
        self.assertEqual(updated["schedule_rule"], {"kind": "monthly", "days": [1]})
        self.assertEqual(updated["job_version"], 1)
        daily = ja.update_job(self.owner, job["id"], {"schedule_mode": "daily"},
                              engine=self.engine, data_root=self.data_root)
        self.assertIsNone(daily["schedule_rule"])

    def test_definition_change_bumps_version_and_marks_runs_stale(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-20", engine=self.engine, data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-20", result="alt", summary="s",
                        state={"a": 1}, engine=self.engine, data_root=self.data_root)
        updated = ja.update_job(self.owner, job["id"], {"model": "neues-modell"},
                                engine=self.engine, data_root=self.data_root)
        self.assertEqual(updated["job_version"], 2)
        runs = ja_runs.list_runs(self.owner, job_id=job["id"], engine=self.engine)
        self.assertTrue(runs[0]["stale"])
        self.assertEqual(runs[0]["stale_reason"], "job_changed")

    def test_name_change_does_not_bump_version(self):
        job = self._create()
        updated = ja.update_job(self.owner, job["id"], {"name": "Anderer Name"},
                                engine=self.engine, data_root=self.data_root)
        self.assertEqual(updated["job_version"], 1)

    def test_prompt_change_marks_using_job_stale(self):
        template = ja.create_prompt_template(self.owner, "Standard", "Fasse zusammen",
                                             engine=self.engine)
        job = self._create(prompt_template_id=template["id"])
        ja_runs.begin_run(self.owner, job["id"], "2026-09-20", engine=self.engine, data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-20", result="x", summary="s",
                        engine=self.engine, data_root=self.data_root)
        ja.update_prompt_template(self.owner, template["id"], body="Fasse kuerzer zusammen",
                                  engine=self.engine)
        refreshed = ja.get_job(self.owner, job["id"], engine=self.engine)
        self.assertEqual(refreshed["job_version"], 2)
        runs = ja_runs.list_runs(self.owner, job_id=job["id"], engine=self.engine)
        self.assertEqual(runs[0]["stale_reason"], "prompt_changed")

    def test_prompt_delete_in_use_is_rejected(self):
        template = ja.create_prompt_template(self.owner, "In Use", "Text", engine=self.engine)
        self._create(prompt_template_id=template["id"])
        with self.assertRaises(ja.JournalAnalysisError):
            ja.delete_prompt_template(self.owner, template["id"], engine=self.engine)

    def test_run_flow_updates_state_and_notification(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine, data_root=self.data_root)
        run = ja_runs.complete_run(self.owner, job["id"], "2026-09-23",
                              result="Offene Aufgabe: X", summary="Zusammenfassung",
                              state={"open_tasks": ["X"]},
                              engine=self.engine, data_root=self.data_root)
        self.assertEqual(run["status"], "completed")
        refreshed = ja.get_job(self.owner, job["id"], engine=self.engine)
        self.assertEqual(refreshed["summary"], "Zusammenfassung")
        self.assertEqual(refreshed["state"], {"open_tasks": ["X"]})
        self.assertEqual(refreshed["last_run_date"], "2026-09-23")
        notifications = ja.list_notifications(self.owner, status="open", engine=self.engine)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["kind"], "analysis")
        # Export enthält Lauf, Summary und Benachrichtigung.
        self.assertEqual(store.read_summary(self.owner, job["slug"], root=self.data_root),
                         "Zusammenfassung")
        self.assertEqual(len(store.list_runs(self.owner, job["slug"], root=self.data_root)), 1)
        self.assertEqual(len(store.read_notifications(self.owner, job["slug"], root=self.data_root)), 1)

    def test_rerunning_a_failed_day_clears_the_old_error_and_uses_the_new_version(self):
        job = self._create()
        ja_runs.fail_run(self.owner, job["id"], "2026-09-23", error_code="X",
                         error_message="kaputt", engine=self.engine, data_root=self.data_root)
        updated = ja.update_job(self.owner, job["id"], {"model": "neu"}, engine=self.engine,
                                data_root=self.data_root)
        self.assertEqual(updated["job_version"], 2)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="ok", engine=self.engine,
                             data_root=self.data_root, notify=False)
        run = ja_runs.list_runs(self.owner, job_id=job["id"], engine=self.engine)[0]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["job_version"], 2)
        self.assertEqual(run["error_message"], "")
        self.assertIsNotNone(run["started_at"])

    def test_output_flags_and_handoff_summary(self):
        job = self._create(output_run_summary=False, carry_summary=True, output_notes=True,
                           notes_target="bericht.md")
        self.assertFalse(job["output_run_summary"])
        self.assertTrue(job["carry_summary"])
        self.assertTrue(job["output_notes"])
        self.assertEqual(job["notes_target"], "bericht.md")
        ja_runs.set_handoff_summary(self.owner, job["id"], "erste", engine=self.engine,
                                    data_root=self.data_root)
        ja_runs.set_handoff_summary(self.owner, job["id"], "zweite", engine=self.engine,
                                    data_root=self.data_root)
        refreshed = ja.get_job(self.owner, job["id"], engine=self.engine)
        self.assertEqual(refreshed["handoff_summary"], "zweite")

    def test_run_level_notification_is_created_once(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine,
                          data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="x", engine=self.engine,
                             data_root=self.data_root, notify=False)
        self.assertEqual(ja.list_notifications(self.owner, engine=self.engine), [])
        ja_runs.notify_run(self.owner, job, day=date(2026, 9, 30), title="Monat",
                           body="Zusammenfassung", engine=self.engine, data_root=self.data_root)
        items = ja.list_notifications(self.owner, engine=self.engine)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "summary")

    def test_scheduled_notification_is_hidden_until_due(self):
        job = self._create()
        future = datetime.now(timezone.utc) + timedelta(days=1)
        ja_runs.notify_reminder(self.owner, job, day=date(2026, 9, 23), run_id=None,
                                title="Recherche", body="Thema X", notify_at=future,
                                engine=self.engine, data_root=self.data_root)
        self.assertEqual(len(ja.list_notifications(self.owner, engine=self.engine)), 1)
        stored = ja.list_notifications(self.owner, engine=self.engine)[0]
        self.assertEqual(stored["kind"], "reminder")
        self.assertIsNotNone(stored["notify_at"])
        # Vor Fälligkeit: unsichtbar für die aktive Ansicht …
        due = ja.list_notifications(self.owner, statuses=["open", "ignored"], due_only=True,
                                    engine=self.engine)
        self.assertEqual(due, [])
        # … und nach Fälligkeit sichtbar.
        later = ja.list_notifications(self.owner, statuses=["open", "ignored"], due_only=True,
                                      now=future + timedelta(seconds=1), engine=self.engine)
        self.assertEqual(len(later), 1)
        self.assertEqual(later[0]["id"], stored["id"])

    def test_notify_reminder_deduplicates_identical_events(self):
        job = self._create()
        future = datetime.now(timezone.utc) + timedelta(days=1)
        first = ja_runs.notify_reminder(self.owner, job, day=date(2026, 9, 23), run_id=None,
                                        title="Recherche", body="Thema X", notify_at=future,
                                        engine=self.engine, data_root=self.data_root)
        second = ja_runs.notify_reminder(
            self.owner, job, day=date(2026, 9, 23), run_id=None, title="Recherche",
            body="Thema X", notify_at=future + timedelta(hours=1),
            engine=self.engine, data_root=self.data_root)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(ja.list_notifications(self.owner, engine=self.engine)), 1)

    def test_journal_reminders_are_unique_per_owner_across_jobs(self):
        first_job = self._create()
        second_job = self._create(name="Wochenblick", date_range_mode="current_week")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        values = {"day": date(2026, 9, 23), "run_id": None, "title": "Journal-Erinnerung · 2026-09-23",
                  "body": "Thomas anrufen", "notify_at": future, "engine": self.engine,
                  "data_root": self.data_root, "any_job": True}
        self.assertTrue(ja_runs.notify_reminder(self.owner, first_job, **values))
        self.assertFalse(ja_runs.notify_reminder(self.owner, second_job, **values))
        # Ohne any_job bleibt es bei der bisherigen Pruefung je Job.
        self.assertTrue(ja_runs.notify_reminder(self.owner, second_job, **{**values, "any_job": False}))

    def test_recent_reminders_lists_due_reminders_of_all_jobs(self):
        job = self._create()
        future = datetime.now(timezone.utc) + timedelta(days=2)
        ja_runs.notify_reminder(self.owner, job, day=date(2026, 9, 23), run_id=None, title="T",
                                body="Recherche", notify_at=future, engine=self.engine,
                                data_root=self.data_root)
        rows = ja_runs.recent_reminders(self.owner, start=future - timedelta(hours=1),
                                        end=future + timedelta(hours=1), engine=self.engine)
        self.assertEqual([row["body"] for row in rows], ["Recherche"])
        self.assertEqual(ja_runs.recent_reminders(self.owner, start=future + timedelta(hours=1),
                                                  end=future + timedelta(hours=2), engine=self.engine), [])

    def test_complete_run_without_notification_mode_creates_none(self):
        job = self._create(output_notification=False)
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine, data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="x", engine=self.engine,
                        data_root=self.data_root)
        self.assertEqual(ja.list_notifications(self.owner, engine=self.engine), [])

    def test_failed_run_records_error_notification(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine, data_root=self.data_root)
        run = ja_runs.fail_run(self.owner, job["id"], "2026-09-23", error_code="llm_unreachable",
                          error_message="Endpunkt nicht erreichbar", engine=self.engine,
                          data_root=self.data_root)
        self.assertEqual(run["status"], "failed")
        notifications = ja.list_notifications(self.owner, engine=self.engine)
        self.assertEqual(notifications[0]["kind"], "error")

    def test_notification_status_transitions(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine, data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="x", engine=self.engine,
                        data_root=self.data_root)
        item = ja.list_notifications(self.owner, engine=self.engine)[0]
        ja.set_notification_status(self.owner, item["id"], "ignored", engine=self.engine,
                                   data_root=self.data_root)
        reopened = ja.list_notifications(self.owner, status="ignored", engine=self.engine)
        self.assertEqual(len(reopened), 1)
        with self.assertRaises(ja.JournalAnalysisError):
            ja.set_notification_status(self.owner, item["id"], "bogus", engine=self.engine)

    def test_active_notifications_include_open_and_ignored_but_not_done(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine,
                          data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="x", engine=self.engine,
                             data_root=self.data_root)
        item = ja.list_notifications(self.owner, engine=self.engine)[0]
        ja.set_notification_status(self.owner, item["id"], "ignored", engine=self.engine)
        active = ja.list_notifications(self.owner, statuses=["open", "ignored"], engine=self.engine)
        self.assertEqual([entry["id"] for entry in active], [item["id"]])
        ja.set_notification_status(self.owner, item["id"], "done", engine=self.engine)
        self.assertEqual(
            ja.list_notifications(self.owner, statuses=["open", "ignored"], engine=self.engine), [])

    def test_old_notifications_fall_out_of_the_lists(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine,
                          data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="x", engine=self.engine,
                             data_root=self.data_root)
        item = ja.list_notifications(self.owner, engine=self.engine)[0]
        later = item["created_at"]
        later = (datetime.fromisoformat(later) if isinstance(later, str) else later) + timedelta(days=31)
        self.assertEqual(ja.list_notifications(self.owner, max_age_days=30, now=later,
                                               engine=self.engine), [])
        # Ohne Altersgrenze (Protokoll) bleibt der Hinweis erhalten.
        self.assertEqual(len(ja.list_notifications(self.owner, now=later, engine=self.engine)), 1)

    def test_close_due_notifications_skips_planned_ones(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine,
                          data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="x", engine=self.engine,
                             data_root=self.data_root)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        ja_runs.notify_reminder(self.owner, job, day=date(2026, 9, 23), run_id=None,
                                title="Recherche", body="Thema X", notify_at=future,
                                engine=self.engine, data_root=self.data_root)
        self.assertEqual(ja.close_due_notifications(self.owner, engine=self.engine,
                                                    data_root=self.data_root), 1)
        remaining = ja.list_notifications(self.owner, statuses=["open", "ignored"], engine=self.engine)
        self.assertEqual([entry["kind"] for entry in remaining], ["reminder"])
        self.assertEqual(len(ja.list_notifications(self.owner, status="done", engine=self.engine)), 1)

    def test_mark_run_stale_is_manual_only(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine, data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="x", engine=self.engine,
                        data_root=self.data_root)
        self.assertTrue(ja_runs.mark_run_stale(self.owner, job["id"], "2026-09-23",
                                          engine=self.engine))
        runs = ja_runs.list_runs(self.owner, job_id=job["id"], engine=self.engine)
        self.assertEqual(runs[0]["stale_reason"], "source_changed")

    def test_delete_job_removes_runs_and_notifications(self):
        job = self._create()
        ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine, data_root=self.data_root)
        ja_runs.complete_run(self.owner, job["id"], "2026-09-23", result="x", engine=self.engine,
                        data_root=self.data_root)
        slug = ja.delete_job(self.owner, job["id"], engine=self.engine)
        self.assertEqual(slug, job["slug"])
        self.assertEqual(ja.list_jobs(self.owner, engine=self.engine), [])
        self.assertEqual(ja.list_notifications(self.owner, engine=self.engine), [])
        self.assertEqual(ja_runs.list_runs(self.owner, engine=self.engine), [])

    def test_list_enabled_jobs_excludes_disabled_ones(self):
        enabled = self._create(name="Aktiv")
        disabled = self._create(name="Inaktiv", enabled=False)
        ids = {job["id"] for job in ja.list_enabled_jobs(engine=self.engine)}
        self.assertIn(enabled["id"], ids)
        self.assertNotIn(disabled["id"], ids)

    def test_foreign_owner_cannot_touch_a_job(self):
        job = self._create()
        with self.assertRaises(ja.JobNotFound):
            ja.get_job("someone-else", job["id"], engine=self.engine)

    def test_run_is_idempotent_per_job_and_date(self):
        job = self._create()
        first = ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine,
                             data_root=self.data_root)
        second = ja_runs.begin_run(self.owner, job["id"], "2026-09-23", engine=self.engine,
                              data_root=self.data_root)
        self.assertEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()