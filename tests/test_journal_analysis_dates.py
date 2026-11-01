"""Rollierende Zeiträume und Fälligkeit der Journal-Analyse-Jobs."""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import journal_analysis_dates as dates  # noqa: E402


class RangeTests(unittest.TestCase):
    def test_previous_day_is_the_day_before_today(self):
        start, end = dates.resolve_range("previous_day", today=date(2026, 9, 24))
        self.assertEqual((start, end), (date(2026, 9, 23), date(2026, 9, 23)))

    def test_current_week_starts_on_monday_and_ends_today(self):
        # 2026-09-24 ist ein Donnerstag.
        start, end = dates.resolve_range("current_week", today=date(2026, 9, 24))
        self.assertEqual(start, date(2026, 9, 21))
        self.assertEqual(end, date(2026, 9, 24))

    def test_last_week_is_the_full_previous_week(self):
        start, end = dates.resolve_range("last_week", today=date(2026, 9, 24))
        self.assertEqual((start, end), (date(2026, 9, 14), date(2026, 9, 20)))

    def test_current_month_starts_on_the_first(self):
        start, end = dates.resolve_range("current_month", today=date(2026, 9, 24))
        self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 24)))

    def test_last_month_crosses_the_year_boundary(self):
        start, end = dates.resolve_range("last_month", today=date(2026, 1, 10))
        self.assertEqual((start, end), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_custom_range_is_clamped_to_today(self):
        start, end = dates.resolve_range("custom", today=date(2026, 9, 24),
                                         custom_start="2026-09-01", custom_end="2026-10-31")
        self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 24)))

    def test_custom_range_fully_in_the_future_yields_nothing(self):
        self.assertEqual(dates.resolve_range("custom", today=date(2026, 9, 24),
                                             custom_start="2026-10-01",
                                             custom_end="2026-10-31"), (None, None))

    def test_last_days_includes_today(self):
        start, end = dates.resolve_range("last_days", today=date(2026, 10, 1), range_days=2)
        self.assertEqual((start, end), (date(2026, 9, 30), date(2026, 10, 1)))
        start, end = dates.resolve_range("last_days", today=date(2026, 10, 1), range_days=3)
        self.assertEqual(start, date(2026, 9, 29))
        with self.assertRaises(ValueError):
            dates.resolve_range("last_days", today=date(2026, 10, 1))

    def test_custom_range_needs_both_ends(self):
        with self.assertRaises(ValueError):
            dates.resolve_range("custom", today=date(2026, 9, 24), custom_start="2026-09-01")

    def test_days_in_range_is_inclusive_and_ascending(self):
        days = dates.days_in_range(date(2026, 9, 21), date(2026, 9, 24))
        self.assertEqual([day.isoformat() for day in days],
                         ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"])


class DueTests(unittest.TestCase):
    def test_manual_jobs_never_run_automatically(self):
        job = {"enabled": True, "schedule_mode": "manual"}
        self.assertFalse(dates.is_due(job, datetime(2026, 9, 24, 23, 59)))

    def test_daily_job_runs_once_after_the_time(self):
        job = {"enabled": True, "schedule_mode": "daily", "schedule_time": "06:00"}
        self.assertFalse(dates.is_due(job, datetime(2026, 9, 24, 5, 59)))
        self.assertTrue(dates.is_due(job, datetime(2026, 9, 24, 6, 0)))
        self.assertFalse(dates.is_due(job, datetime(2026, 9, 24, 9, 0),
                                      datetime(2026, 9, 24, 6, 1)))
        self.assertTrue(dates.is_due(job, datetime(2026, 9, 25, 6, 0),
                                     datetime(2026, 9, 24, 6, 1)))

    def test_interval_job_uses_the_last_run(self):
        job = {"enabled": True, "schedule_mode": "interval", "schedule_interval_hours": 6}
        self.assertTrue(dates.is_due(job, datetime(2026, 9, 24, 12, 0)))
        self.assertFalse(dates.is_due(job, datetime(2026, 9, 24, 12, 0),
                                      datetime(2026, 9, 24, 9, 0)))
        self.assertTrue(dates.is_due(job, datetime(2026, 9, 24, 15, 0),
                                     datetime(2026, 9, 24, 9, 0)))

    def test_custom_weekly_job_runs_on_friday_evening(self):
        # 2026-10-02 ist ein Freitag.
        job = {"enabled": True, "schedule_mode": "custom", "schedule_time": "19:00",
               "schedule_rule": {"kind": "weekly", "weekdays": [4]}}
        self.assertFalse(dates.is_due(job, datetime(2026, 10, 2, 18, 59)))
        self.assertTrue(dates.is_due(job, datetime(2026, 10, 2, 19, 0)))
        self.assertFalse(dates.is_due(job, datetime(2026, 10, 2, 21, 0),
                                      datetime(2026, 10, 2, 19, 1)))
        self.assertFalse(dates.is_due(job, datetime(2026, 10, 8, 23, 0),
                                      datetime(2026, 10, 2, 19, 1)))
        self.assertTrue(dates.is_due(job, datetime(2026, 10, 9, 19, 5),
                                     datetime(2026, 10, 2, 19, 1)))

    def test_custom_job_without_run_waits_for_its_next_date(self):
        job = {"enabled": True, "schedule_mode": "custom", "schedule_time": "19:00",
               "schedule_rule": {"kind": "weekly", "weekdays": [4]}}
        # Samstag nach einem Freitag ohne bisherigen Lauf: nicht sofort nachholen.
        self.assertFalse(dates.is_due(job, datetime(2026, 10, 3, 9, 0)))

    def test_custom_job_catches_up_a_missed_date_once(self):
        job = {"enabled": True, "schedule_mode": "custom", "schedule_time": "19:00",
               "schedule_rule": {"kind": "weekly", "weekdays": [4]}}
        self.assertTrue(dates.is_due(job, datetime(2026, 10, 10, 9, 0),
                                     datetime(2026, 10, 2, 19, 1)))

    def test_custom_monthly_job_runs_on_the_first(self):
        job = {"enabled": True, "schedule_mode": "custom", "schedule_time": "06:00",
               "schedule_rule": {"kind": "monthly", "days": [1]}}
        self.assertTrue(dates.is_due(job, datetime(2026, 11, 1, 6, 0),
                                     datetime(2026, 10, 1, 6, 2)))
        self.assertFalse(dates.is_due(job, datetime(2026, 11, 20, 6, 0),
                                      datetime(2026, 11, 1, 6, 2)))

    def test_month_day_beyond_month_end_is_the_last_day(self):
        rule = {"kind": "monthly", "days": [31]}
        self.assertTrue(dates.matches_rule(rule, date(2026, 2, 28)))
        self.assertTrue(dates.matches_rule(rule, date(2026, 4, 30)))
        self.assertFalse(dates.matches_rule(rule, date(2026, 4, 29)))

    def test_custom_job_compares_timezone_aware_values(self):
        tz = timezone(timedelta(hours=2))
        job = {"enabled": True, "schedule_mode": "custom", "schedule_time": "19:00",
               "schedule_rule": {"kind": "weekly", "weekdays": [4]}}
        self.assertFalse(dates.is_due(job, datetime(2026, 10, 2, 19, 30, tzinfo=tz),
                                      datetime(2026, 10, 2, 17, 5, tzinfo=timezone.utc)))
        self.assertTrue(dates.is_due(job, datetime(2026, 10, 2, 19, 30, tzinfo=tz),
                                     datetime(2026, 10, 2, 16, 55, tzinfo=timezone.utc)))

    def test_disabled_job_never_runs(self):
        job = {"enabled": False, "schedule_mode": "daily", "schedule_time": "00:00"}
        self.assertFalse(dates.is_due(job, datetime(2026, 9, 24, 23, 0)))


if __name__ == "__main__":
    unittest.main()