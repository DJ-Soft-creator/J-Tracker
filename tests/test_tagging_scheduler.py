"""Der Zeitplan startet den Job „Hashtags nachtragen“ einmal je Termin.

Der Sidecar pollt alle paar Minuten. Dieser Test haelt fest, dass ein Termin nur
einmal laeuft, dass der Lauf im Protokoll (``background_runs``) landet, dass ein
Fehlschlag spaeter wiederholt wird und erst der letzte Versuch eine
Fehlermeldung erzeugt.
"""

import importlib.util
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "tagging_scheduler", ROOT / "scripts" / "tagging-scheduler.py")
tagging_scheduler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tagging_scheduler)


class TaggingSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.started = []
        self.fail = []

        self.main = tagging_scheduler.main
        self.saved_main = {name: getattr(self.main, name, None) for name in ("get_all_users", "DATA_DIR")}
        self.main.DATA_DIR = self.data
        self.main.get_all_users = lambda: [{"id": "alex"}]

        self.ht = tagging_scheduler.historical_tagging
        self.saved_ht = {name: getattr(self.ht, name) for name in ("load_settings", "_core_module")}
        self.settings = {
            "base_url": "http://127.0.0.1:1234", "model": "local", "api_key": "",
            **self.ht.tagging_job.stored_job({"enabled": True, "schedule_time": "05:00",
                                              "range_days": 7, "notify": {"errors": True}}),
        }
        self.ht.load_settings = lambda user_id: dict(self.settings)
        self.ht._core_module = lambda: types.SimpleNamespace(DATA_DIR=self.data)

        self.br = tagging_scheduler.background_runs
        self.saved_br = {"_core": self.br._core, "handler": self.br._handlers.get(self.ht.RUN_KIND)}
        self.br._core = lambda: types.SimpleNamespace(DATA_DIR=self.data)

        def fake_handler(handle, params):
            self.started.append((params["start_date"], params["end_date"], params["mode"]))
            if self.fail and self.fail.pop(0):
                raise self.br.RunError("Modell nicht erreichbar")
            return {"processed": 1, "skipped": 0, "failed": 0, "proposals": []}

        self.br._handlers[self.ht.RUN_KIND] = fake_handler

    def tearDown(self):
        for name, value in self.saved_main.items():
            setattr(self.main, name, value)
        for name, value in self.saved_ht.items():
            setattr(self.ht, name, value)
        self.br._core = self.saved_br["_core"]
        self.br._handlers[self.ht.RUN_KIND] = self.saved_br["handler"]
        self.temp.cleanup()

    def _events(self):
        return self.ht.tagging_job.list_events(self.data, "alex")

    def test_each_user_is_started_only_once_per_day(self):
        now = datetime(2026, 9, 27, 5, 0)

        before = tagging_scheduler.run_once(now=now.replace(hour=4))
        first = tagging_scheduler.run_once(now=now)
        second = tagging_scheduler.run_once(now=now.replace(minute=30))
        next_day = tagging_scheduler.run_once(now=now + timedelta(days=1))

        self.assertEqual((before, first, second, next_day), (0, 1, 0, 1))
        self.assertEqual({mode for *_, mode in self.started}, {"missing"})

    def test_the_run_lands_in_the_log_as_a_scheduled_run(self):
        tagging_scheduler.run_once(now=datetime(2026, 9, 27, 5, 0))

        runs = self.br.listing("alex", self.ht.RUN_KIND)
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["status"], runs[0]["trigger"]), ("done", "schedule"))

    def test_the_range_covers_the_last_days_including_today(self):
        tagging_scheduler.run_once(now=datetime(2026, 9, 27, 5, 0))

        self.assertEqual(self.started[0][:2], ("2026-09-21", "2026-09-27"))

    def test_a_failed_run_is_retried_and_only_the_last_attempt_is_reported(self):
        self.fail = [True, True, True]
        now = datetime(2026, 9, 27, 5, 0)

        self.assertEqual(tagging_scheduler.run_once(now=now), 1)
        self.assertEqual(tagging_scheduler.run_once(now=now + timedelta(minutes=10)), 0)
        self.assertEqual(self._events(), [])
        self.assertEqual(tagging_scheduler.run_once(now=now + timedelta(minutes=31)), 1)
        self.assertEqual(tagging_scheduler.run_once(now=now + timedelta(minutes=62)), 1)

        self.assertEqual([item["kind"] for item in self._events()], ["tagging_error"])
        # Der Termin ist damit abgeschlossen; heute startet nichts mehr.
        self.assertEqual(tagging_scheduler.run_once(now=now + timedelta(hours=3)), 0)

    def test_a_retry_that_succeeds_reports_nothing(self):
        self.fail = [True]
        now = datetime(2026, 9, 27, 5, 0)

        tagging_scheduler.run_once(now=now)
        tagging_scheduler.run_once(now=now + timedelta(minutes=31))

        self.assertEqual(len(self.started), 2)
        self.assertEqual(self._events(), [])

    def test_a_paused_or_manual_job_never_starts(self):
        self.settings["enabled"] = False
        self.assertEqual(tagging_scheduler.run_once(now=datetime(2026, 9, 27, 5, 0)), 0)
        self.settings.update(enabled=True, schedule_mode="manual")
        self.assertEqual(tagging_scheduler.run_once(now=datetime(2026, 9, 27, 5, 0)), 0)

    def test_an_old_daily_stamp_keeps_today_from_running_twice(self):
        config = self.data / "alex" / ".config"
        config.mkdir(parents=True)
        (config / "historical-tagging-daily.json").write_text('{"last_run": "2026-09-27"}', encoding="utf-8")

        self.assertEqual(tagging_scheduler.run_once(now=datetime(2026, 9, 27, 5, 0)), 0)


if __name__ == "__main__":
    unittest.main()
