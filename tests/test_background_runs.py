"""Serverseitige Laeufe: Lebenszyklus, Abbruch und die Sicht des Browsers.

Der Kern der Zusage ist: ein Lauf gehoert dem Server, nicht dem Tab, der ihn
gestartet hat. Diese Tests halten genau das fest — er laeuft ohne Browser weiter,
ein zweiter Start derselben Art wird abgewiesen, ein Abbruch wirkt, und ein
Neustart laesst keine ewig laufenden Auftraege zurueck.
"""

import threading
import time

try:
    from .brain_api_case import BrainApiCase, FLASK_AVAILABLE
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase, FLASK_AVAILABLE

if FLASK_AVAILABLE:
    import background_runs


class BackgroundRunCase(BrainApiCase):
    """Registriert Testlaufarten und raeumt sie hinterher wieder ab."""

    def setUp(self):
        super().setUp()
        self._registered = []

    def tearDown(self):
        for kind in self._registered:
            background_runs._handlers.pop(kind, None)
        super().tearDown()

    def _register(self, kind, handler):
        background_runs.register(kind, handler)
        self._registered.append(kind)

    def _await_final(self, run_id, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = background_runs.read("user-a", run_id)
            if record and record["status"] in background_runs.FINAL_STATUSES:
                return record
            time.sleep(0.02)
        self.fail("Der Lauf wurde nicht innerhalb von {}s fertig".format(timeout))


class RunLifecycleTests(BackgroundRunCase):
    def test_a_run_finishes_in_the_worker_without_any_request(self):
        """Kein Browser, kein Request — der Lauf laeuft trotzdem zu Ende."""
        self._register("test_ok", lambda handle, params: {"seen": params["value"]})
        record = background_runs.start("user-a", "test_ok", {"value": 42})

        finished = self._await_final(record["id"])
        self.assertEqual(finished["status"], "done")
        self.assertEqual(finished["result"], {"seen": 42})
        self.assertTrue(finished["finished_at"])

    def test_progress_and_log_survive_in_the_file(self):
        """Fortschritt liegt auf der Platte, nicht im Speicher eines Tabs."""
        def handler(handle, params):
            handle.progress(processed=1, remaining=0)
            handle.log({"date": "2026-08-01", "status": "processed"})
            return {}

        self._register("test_progress", handler)
        record = background_runs.start("user-a", "test_progress", {})
        finished = self._await_final(record["id"])

        self.assertEqual(finished["progress"], {"processed": 1, "remaining": 0})
        self.assertEqual(finished["log"], [{"date": "2026-08-01", "status": "processed"}])

    def test_a_second_run_of_the_same_kind_is_refused(self):
        """Zwei Verschlagwortungslaeufe wuerden dieselben Journale umschreiben."""
        release = threading.Event()
        self._register("test_block", lambda handle, params: release.wait(5) and {})
        first = background_runs.start("user-a", "test_block", {})
        try:
            with self.assertRaises(background_runs.RunConflict):
                background_runs.start("user-a", "test_block", {})
        finally:
            release.set()
        self._await_final(first["id"])

    def test_another_user_may_start_the_same_kind(self):
        release = threading.Event()
        self._register("test_parallel", lambda handle, params: release.wait(5) and {})
        first = background_runs.start("user-a", "test_parallel", {})
        second = background_runs.start("user-b", "test_parallel", {})
        release.set()

        self.assertNotEqual(first["id"], second["id"])
        self._await_final(first["id"])

    def test_cancel_reaches_the_running_handler(self):
        """Der Abbruchwunsch geht ueber die Datei, nicht ueber ein Signal."""
        started, stopped = threading.Event(), threading.Event()

        def handler(handle, params):
            started.set()
            for _ in range(500):
                if handle.cancelled:
                    stopped.set()
                    return {"cancelled": True}
                time.sleep(0.01)
            return {"cancelled": False}

        self._register("test_cancel", handler)
        record = background_runs.start("user-a", "test_cancel", {})
        self.assertTrue(started.wait(5))
        background_runs.cancel("user-a", record["id"])

        finished = self._await_final(record["id"])
        self.assertTrue(stopped.is_set())
        self.assertEqual(finished["status"], "cancelled")
        self.assertTrue(finished["result"]["cancelled"])

    def test_a_failing_handler_reports_its_reason(self):
        def handler(handle, params):
            raise background_runs.RunError("Das lokale Modell war nicht erreichbar.")

        self._register("test_fail", handler)
        record = background_runs.start("user-a", "test_fail", {})

        finished = self._await_final(record["id"])
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "Das lokale Modell war nicht erreichbar.")

    def test_an_unexpected_crash_does_not_kill_the_worker(self):
        self._register("test_crash", lambda handle, params: 1 / 0)
        crashed = background_runs.start("user-a", "test_crash", {})
        self.assertEqual(self._await_final(crashed["id"])["status"], "failed")

        self._register("test_after_crash", lambda handle, params: {"ok": True})
        later = background_runs.start("user-a", "test_after_crash", {})
        self.assertEqual(self._await_final(later["id"])["result"], {"ok": True})

    def test_unknown_kind_is_refused_before_anything_is_written(self):
        with self.assertRaises(background_runs.RunError):
            background_runs.start("user-a", "test_missing_handler", {})
        self.assertIsNone(background_runs.active("user-a"))


class RestartTests(BackgroundRunCase):
    def test_running_runs_are_marked_aborted_after_a_restart(self):
        """Ein Lauf ueberlebt keinen Neustart — und darf danach nicht blockieren."""
        self._register("test_restart", lambda handle, params: {})
        record = background_runs.start("user-a", "test_restart", {})
        self._await_final(record["id"])
        # Zustand kuenstlich auf "laeuft" zuruecksetzen: genau so sieht es aus,
        # wenn der Container mitten im Lauf neu startet.
        background_runs._modify("user-a", record["id"], lambda item: item.update({"status": "running"}))

        self.assertEqual(background_runs.abort_stale_runs(), 1)
        stale = background_runs.read("user-a", record["id"])
        self.assertEqual(stale["status"], "aborted")
        self.assertIn("neu gestartet", stale["error"])
        self.assertIsNone(background_runs.active("user-a", "test_restart"))


class InlineAndHookTests(BackgroundRunCase):
    def tearDown(self):
        for kind in self._registered:
            background_runs._finish_hooks.pop(kind, None)
            background_runs._retention.pop(kind, None)
        super().tearDown()

    def test_an_inline_run_finishes_before_start_returns_and_calls_its_hook(self):
        """So startet ein Sidecar ohne eigenen Arbeiter einen Lauf ins Protokoll."""
        finished = []
        background_runs.register("test_inline", lambda handle, params: {"n": 1},
                                 on_finish=finished.append)
        self._registered.append("test_inline")

        record = background_runs.start("user-a", "test_inline", {}, trigger="schedule", inline=True)

        self.assertEqual((record["status"], record["trigger"], record["result"]), ("done", "schedule", {"n": 1}))
        self.assertEqual([item["id"] for item in finished], [record["id"]])
        self.assertEqual(background_runs.public_run(record)["trigger"], "schedule")

    def test_the_hook_also_hears_about_a_failure(self):
        finished = []

        def broken(handle, params):
            raise background_runs.RunError("weg")

        background_runs.register("test_inline_fail", broken, on_finish=finished.append)
        self._registered.append("test_inline_fail")
        record = background_runs.start("user-a", "test_inline_fail", {}, inline=True)

        self.assertEqual(record["status"], "failed")
        self.assertEqual(finished[0]["error"], "weg")

    def test_an_app_restart_leaves_a_sidecar_run_alone(self):
        background_runs.register("test_inline_keep", lambda handle, params: {})
        self._registered.append("test_inline_keep")
        record = background_runs.start("user-a", "test_inline_keep", {}, inline=True)
        background_runs._modify("user-a", record["id"], lambda item: item.update({"status": "running"}))

        self.assertEqual(background_runs.abort_stale_runs(), 0)
        self.assertEqual(background_runs.abort_stale_runs(runner="inline"), 1)

    def test_a_kind_can_keep_its_runs_longer(self):
        from datetime import datetime, timedelta, timezone

        background_runs.register("test_keep", lambda handle, params: {}, retention_days=30)
        self._registered.append("test_keep")
        record = background_runs.start("user-a", "test_keep", {}, inline=True)
        later = datetime.now(timezone.utc) + timedelta(days=10)

        self.assertEqual(background_runs.cleanup("user-a", now=later), 0)
        self.assertEqual(background_runs.cleanup("user-a", now=later + timedelta(days=25)), 1)
        self.assertIsNone(background_runs.read("user-a", record["id"]))


class PublicViewTests(BackgroundRunCase):
    def test_the_log_is_delivered_from_the_position_the_browser_already_has(self):
        record = {
            "id": "x", "kind": "k", "status": "running", "log_offset": 0,
            "log": [{"n": 0}, {"n": 1}, {"n": 2}],
        }
        view = background_runs.public_run(record, log_from=2)
        self.assertEqual(view["log"], [{"n": 2}])
        self.assertEqual(view["log_from"], 2)
        self.assertEqual(view["log_total"], 3)

    def test_a_capped_log_reports_honest_positions(self):
        """Sind Zeilen der Deckelung zum Opfer gefallen, wird nicht so getan,
        als haette der Browser sie noch bekommen koennen."""
        record = {"id": "x", "kind": "k", "status": "running", "log_offset": 10,
                  "log": [{"n": 10}, {"n": 11}]}
        view = background_runs.public_run(record, log_from=0)
        self.assertEqual(view["log"], [{"n": 10}, {"n": 11}])
        self.assertEqual(view["log_from"], 10)
        self.assertEqual(view["log_total"], 12)

    def test_the_view_never_carries_the_parameters(self):
        """Parameter koennen Anbieterkennungen enthalten; die Anzeige braucht sie nicht."""
        self._register("test_view", lambda handle, params: {})
        record = background_runs.start("user-a", "test_view", {"provider_id": "lm_secret"})
        self._await_final(record["id"])
        view = background_runs.public_run(background_runs.read("user-a", record["id"]))
        self.assertNotIn("params", view)
        self.assertNotIn("lm_secret", str(view))


class RunApiTests(BackgroundRunCase):
    def test_the_browser_can_find_and_cancel_a_running_run(self):
        started = threading.Event()
        release = threading.Event()

        def handler(handle, params):
            started.set()
            while not handle.cancelled and not release.is_set():
                time.sleep(0.01)
            return {}

        self._register("test_api", handler)
        record = background_runs.start("user-a", "test_api", {})
        self.assertTrue(started.wait(5))

        listing = self.client.get("/api/runs?kind=test_api").get_json()["runs"]
        self.assertEqual([item["id"] for item in listing], [record["id"]])
        self.assertTrue(listing[0]["active"])

        detail = self.client.get("/api/runs/{}".format(record["id"]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["status"], "running")

        cancelled = self.client.post("/api/runs/{}/cancel".format(record["id"]), headers=self.headers)
        self.assertEqual(cancelled.status_code, 200)
        release.set()
        self.assertEqual(self._await_final(record["id"])["status"], "cancelled")

    def test_a_finished_run_can_be_discarded_but_a_running_one_cannot(self):
        release = threading.Event()
        self._register("test_discard", lambda handle, params: release.wait(5) and {})
        record = background_runs.start("user-a", "test_discard", {})

        blocked = self.client.delete("/api/runs/{}".format(record["id"]), headers=self.headers)
        self.assertEqual(blocked.status_code, 409)

        release.set()
        self._await_final(record["id"])
        removed = self.client.delete("/api/runs/{}".format(record["id"]), headers=self.headers)
        self.assertEqual(removed.status_code, 200)
        self.assertIsNone(background_runs.read("user-a", record["id"]))

    def test_a_run_of_another_user_is_invisible(self):
        self._register("test_foreign", lambda handle, params: {})
        record = background_runs.start("user-b", "test_foreign", {})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and background_runs.read("user-b", record["id"])["status"] not in background_runs.FINAL_STATUSES:
            time.sleep(0.02)

        self.assertEqual(self.client.get("/api/runs/{}".format(record["id"])).status_code, 404)
        self.assertEqual(self.client.get("/api/runs").get_json()["runs"], [])

    def test_writing_endpoints_need_the_csrf_token(self):
        self._register("test_csrf", lambda handle, params: {})
        record = background_runs.start("user-a", "test_csrf", {})
        self._await_final(record["id"])
        self.assertEqual(self.client.post("/api/runs/{}/cancel".format(record["id"])).status_code, 403)
        self.assertEqual(self.client.delete("/api/runs/{}".format(record["id"])).status_code, 403)

    def test_an_unknown_run_id_is_a_clean_404(self):
        self.assertEqual(self.client.get("/api/runs/not-a-uuid").status_code, 404)
