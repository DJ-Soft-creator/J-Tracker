#!/usr/bin/env python3
"""Materialize due family tasks once or as a daily scheduler process."""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"
sys.path.insert(0, str(APP_DIR if APP_DIR.is_dir() else ROOT_DIR))

from scheduling import (  # noqa: E402
    is_due_on,
    materialize_due_tasks,
    parse_planner,
    parse_recurring_tasks,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
FAMILY_DIR = DATA_DIR / "family"
PLANNER_FILE = FAMILY_DIR / "planner" / "recurring.md"
FAMILY_TASKS_FILE = FAMILY_DIR / "Familien-Aufgaben.md"
TZ_NAME = os.environ.get("TZ", "Europe/Berlin")


def _get_now():
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(TZ_NAME)
    except (ImportError, KeyError):
        tz = timezone(timedelta(hours=2))
    return datetime.now(tz), tz


# Kept as public helpers for existing integrations and tests.
parse_recurring_planner = parse_planner


def is_due_today(item, now):
    return is_due_on(item, now.date())


def _existing_today_ids(content, _today_string):
    return {item["id"] for item in parse_recurring_tasks(content)}


def run(now=None):
    now = now or _get_now()[0]
    logger.info("Scheduler run at %s", now.isoformat())
    if not PLANNER_FILE.exists():
        logger.warning("Planner file missing: %s", PLANNER_FILE)
        return {"date": now.date().isoformat(), "due": 0, "added": 0, "task_ids": []}

    result = materialize_due_tasks(PLANNER_FILE, FAMILY_TASKS_FILE, now)
    for task_id in result["task_ids"]:
        logger.info("Added recurring task: %s", task_id)
    logger.info(
        "Scheduler done. Due %s, added %s new task(s).",
        result["due"],
        result["added"],
    )
    return result


def _seconds_until_next_run(hour):
    now, _ = _get_now()
    next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return max(1, (
        next_run.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    ).total_seconds())


def run_loop(hour):
    logger.info("Starting daily scheduler loop (hour=%02d:00, timezone=%s)", hour, TZ_NAME)
    retry_seconds = max(30, int(os.environ.get("SCHEDULER_RETRY_SECONDS", "300")))
    while True:
        try:
            run()
        except Exception:
            logger.exception("Scheduler run failed")
            logger.info("Retrying scheduler in %s seconds", retry_seconds)
            time.sleep(retry_seconds)
            continue
        sleep_seconds = _seconds_until_next_run(hour)
        logger.info("Next scheduler run in %.0f seconds", sleep_seconds)
        time.sleep(sleep_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true", help="Run now and then once every day")
    parser.add_argument(
        "--hour",
        type=int,
        default=int(os.environ.get("SCHEDULER_HOUR", "6")),
        choices=range(0, 24),
        metavar="0-23",
        help="Local hour for --loop (default: 6)",
    )
    args = parser.parse_args()
    if args.loop:
        run_loop(args.hour)
        return 0
    try:
        run()
        return 0
    except Exception:
        logger.exception("Scheduler run failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
