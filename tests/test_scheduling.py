import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))

from scheduling import (  # noqa: E402
    complete_recurring_task,
    is_due_on,
    materialize_due_tasks,
    next_due_date,
    parse_planner,
    parse_recurring_tasks,
    read_text_file,
    serialize_planner,
)


class SchedulingTests(unittest.TestCase):
    def test_legacy_planner_entry_uses_created_date_as_anchor(self):
        content = (
            "- id: task-001 | title: Müll rausbringen | user: user-1 | "
            "recurrence: weekly_monday | created_at: 2026-07-13T10:00:00+02:00\n"
        )

        items = parse_planner(content)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["start_date"], "2026-07-13")
        self.assertTrue(items[0]["active"])

    def test_legacy_empty_checkbox_and_trailing_metadata_are_parsed(self):
        content = (
            "- [] id: task-001-2026-07-13 | title: Müll rausbringen | user: user-1 | "
            "target-date: 2026-07-13 | source: recurring | recurrence: weekly_monday | "
            "created_at: 2026-07-13T06:00:00+02:00\n"
        )

        tasks = parse_recurring_tasks(content)

        self.assertEqual(len(tasks), 1)
        self.assertFalse(tasks[0]["completed"])
        self.assertEqual(tasks[0]["created_at"], "2026-07-13T06:00:00+02:00")

    def test_recurrence_respects_start_date(self):
        weekly = {
            "recurrence": "weekly_monday",
            "start_date": "2026-07-14",
            "active": True,
        }
        biweekly = {
            "recurrence": "biweekly",
            "start_date": "2026-07-13",
            "active": True,
        }

        self.assertFalse(is_due_on(weekly, date(2026, 7, 13)))
        self.assertTrue(is_due_on(weekly, date(2026, 7, 20)))
        self.assertTrue(is_due_on(biweekly, date(2026, 7, 13)))
        self.assertFalse(is_due_on(biweekly, date(2026, 7, 20)))
        self.assertTrue(is_due_on(biweekly, date(2026, 7, 27)))

    def test_next_due_dates(self):
        self.assertEqual(
            next_due_date(
                {"recurrence": "once", "start_date": "2026-07-27", "active": True},
                date(2026, 7, 25),
            ),
            date(2026, 7, 27),
        )
        self.assertEqual(
            next_due_date(
                {"recurrence": "weekly_monday", "start_date": "2026-07-25", "active": True},
                date(2026, 7, 25),
            ),
            date(2026, 7, 27),
        )
        self.assertEqual(
            next_due_date(
                {"recurrence": "monthly_first", "start_date": "2026-07-25", "active": True},
                date(2026, 7, 25),
            ),
            date(2026, 8, 1),
        )

    def test_materialization_is_idempotent_and_canonical(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planner_path = root / "planner" / "recurring.md"
            tasks_path = root / "Familien-Aufgaben.md"
            planner_path.parent.mkdir(parents=True)
            planner_path.write_text(serialize_planner([{
                "id": "task-001",
                "title": "Müll rausbringen",
                "user": "user-1",
                "recurrence": "weekly_monday",
                "start_date": "2026-07-13",
                "active": True,
                "created_at": "2026-07-01T10:00:00+02:00",
                "created_by": "parent-1",
            }]), encoding="utf-8")
            now = datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)

            first = materialize_due_tasks(planner_path, tasks_path, now)
            second = materialize_due_tasks(planner_path, tasks_path, now)
            content = read_text_file(tasks_path)

            self.assertEqual(first["added"], 1)
            self.assertEqual(second["added"], 0)
            self.assertEqual(len(parse_recurring_tasks(content)), 1)
            self.assertIn("- [ ] id: task-001-2026-07-13", content)
            self.assertIn("plan_id: task-001", content)

    def test_missed_one_time_task_is_materialized_with_original_due_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planner_path = root / "planner" / "recurring.md"
            tasks_path = root / "Familien-Aufgaben.md"
            planner_path.parent.mkdir(parents=True)
            planner_path.write_text(serialize_planner([{
                "id": "task-once",
                "title": "Einmalige Aufgabe",
                "user": "user-1",
                "recurrence": "once",
                "start_date": "2026-07-20",
                "active": True,
                "created_at": "2026-07-01T10:00:00+02:00",
            }]), encoding="utf-8")

            result = materialize_due_tasks(
                planner_path,
                tasks_path,
                datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc),
            )
            task = parse_recurring_tasks(read_text_file(tasks_path))[0]

            self.assertEqual(result["added"], 1)
            self.assertEqual(task["id"], "task-once-2026-07-20")
            self.assertEqual(task["target_date"], "2026-07-20")

    def test_scheduler_cli_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            planner_path = data_dir / "family" / "planner" / "recurring.md"
            planner_path.parent.mkdir(parents=True)
            try:
                from zoneinfo import ZoneInfo

                today = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
            except ImportError:
                today = datetime.now(timezone.utc).date().isoformat()
            planner_path.write_text(serialize_planner([{
                "id": "task-cli",
                "title": "CLI Aufgabe",
                "user": "user-1",
                "recurrence": "daily",
                "start_date": today,
                "active": True,
                "created_at": today + "T00:00:00+02:00",
            }]), encoding="utf-8")
            env = {**os.environ, "DATA_DIR": str(data_dir), "TZ": "Europe/Berlin"}

            first = subprocess.run(
                [sys.executable, str(ROOT_DIR / "scripts" / "scheduler.py")],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            second = subprocess.run(
                [sys.executable, str(ROOT_DIR / "scripts" / "scheduler.py")],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            tasks = parse_recurring_tasks(
                (data_dir / "family" / "Familien-Aufgaben.md").read_text(encoding="utf-8")
            )
            self.assertEqual(len(tasks), 1)

    def test_first_completion_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tasks_path = Path(temp_dir) / "Familien-Aufgaben.md"
            tasks_path.write_text(
                "- [ ] id: task-001-2026-07-13 | title: Müll | user: child-1 | "
                "target-date: 2026-07-13 | source: recurring | recurrence: weekly_monday\n",
                encoding="utf-8",
            )

            first = complete_recurring_task(
                tasks_path, "task-001-2026-07-13", "user-a", "2026-07-13T08:00:00+02:00"
            )
            second = complete_recurring_task(
                tasks_path, "task-001-2026-07-13", "user-b", "2026-07-13T08:00:01+02:00"
            )
            task = parse_recurring_tasks(read_text_file(tasks_path))[0]

            self.assertFalse(first["already_completed"])
            self.assertTrue(second["already_completed"])
            self.assertEqual(task["completed_by"], "user-a")

    def test_concurrent_completion_keeps_one_winner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tasks_path = Path(temp_dir) / "Familien-Aufgaben.md"
            tasks_path.write_text(
                "- [ ] id: task-001-2026-07-13 | title: Müll | user: child-1 | "
                "target-date: 2026-07-13 | source: recurring | recurrence: weekly_monday\n",
                encoding="utf-8",
            )

            def complete(user):
                return complete_recurring_task(
                    tasks_path,
                    "task-001-2026-07-13",
                    user,
                    "2026-07-13T08:00:00+02:00",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(complete, ("user-a", "user-b")))
            task = parse_recurring_tasks(read_text_file(tasks_path))[0]

            self.assertEqual(sum(not result["already_completed"] for result in results), 1)
            self.assertIn(task["completed_by"], {"user-a", "user-b"})


if __name__ == "__main__":
    unittest.main()
