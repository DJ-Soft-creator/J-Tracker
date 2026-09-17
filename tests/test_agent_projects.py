"""Agent-Projekte: store, guards, job contract and Brain reopen path."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "agent-projects-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-agent-projects-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import agent_project_jobs
    import agent_projects
    import brain
    import main
    import tagging


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class AgentProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "data"
        self.data_dir.mkdir(parents=True)
        self.project_root = Path(self.temp.name) / "code"
        (self.project_root / "journl" / "app").mkdir(parents=True)
        (self.project_root / "journl" / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
        self.readonly_root = Path(self.temp.name) / "docs"
        (self.readonly_root / "manuals").mkdir(parents=True)

        self.old = (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR,
            brain.DATA_DIR, brain.FAMILY_DIR, main.ai_sessions_module.DATA_DIR,
            agent_projects.DATA_DIR, agent_project_jobs.DATA_DIR,
        )
        main.DATA_DIR = self.data_dir
        main.USERS_PATH = self.data_dir / "users.json"
        main.CONFIG_PATH = self.data_dir / "config.json"
        tagging.DATA_DIR, tagging.FAMILY_DIR = self.data_dir, self.data_dir / "family"
        brain.DATA_DIR, brain.FAMILY_DIR = self.data_dir, self.data_dir / "family"
        main.ai_sessions_module.DATA_DIR = self.data_dir
        agent_projects.DATA_DIR = self.data_dir
        agent_project_jobs.DATA_DIR = self.data_dir
        # Project submissions enqueue a Brain rebuild in production.  The
        # process-wide index worker outlives this temporary data directory in a
        # unit-test process, so disable only that asynchronous side effect here.
        self.old_enqueue_rebuild = main.brain_module.enqueue_rebuild
        main.brain_module.enqueue_rebuild = lambda _user_id: None

        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "user-a", "username": "Alex", "password": "test"}]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({
            "templates": [{"id": "schnell", "label": "Schnell", "type": "simple"}]}), encoding="utf-8")
        (self.data_dir / "host_worker.json").write_text(json.dumps({
            "schema": 1,
            "external_write_roots": [{"id": "code", "path": str(self.project_root), "label": "Code"}],
            "external_read_roots": [{"id": "docs", "path": str(self.readonly_root), "label": "Handbücher"}],
        }), encoding="utf-8")

        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf-test"

    def tearDown(self):
        (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR,
         brain.DATA_DIR, brain.FAMILY_DIR, main.ai_sessions_module.DATA_DIR,
         agent_projects.DATA_DIR, agent_project_jobs.DATA_DIR) = self.old
        main.brain_module.enqueue_rebuild = self.old_enqueue_rebuild
        self.temp.cleanup()

    # ─── helpers ──────────────────────────────────────────────────────
    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def _connection(self, **overrides):
        payload = {"label": "LM Studio", "kind": "lmstudio", "base_url": "http://127.0.0.1:1234",
                   "runner": "opencode"}
        payload.update(overrides)
        response = self.client.post("/api/agent-projects/connections", headers=self.headers,
                                    json={"action": "save", "connection": payload})
        return response

    def _project(self, connection_id, **overrides):
        payload = {"label": "Journl", "root_id": "code", "subpath": "journl", "mode": "write",
                   "connection_id": connection_id, "model": "qwen3-coder"}
        payload.update(overrides)
        return self.client.post("/api/agent-projects/projects", headers=self.headers,
                                json={"action": "save", "project": payload})

    def _ready_project(self):
        connection = self._connection().get_json()["connection"]
        project = self._project(connection["id"]).get_json()["project"]
        return connection, project

    # ─── roots and folder browsing ────────────────────────────────────
    def test_bootstrap_exposes_host_roots_with_write_flag(self):
        payload = self.client.get("/api/agent-projects/bootstrap").get_json()
        roots = {root["id"]: root for root in payload["roots"]}
        self.assertTrue(payload["roots_configured"])
        self.assertTrue(roots["code"]["writable"])
        self.assertFalse(roots["docs"]["writable"])

    def test_folder_listing_walks_only_inside_the_root(self):
        payload = self.client.get("/api/agent-projects/folders?root_id=code").get_json()
        self.assertEqual([item["name"] for item in payload["folders"]], ["journl"])
        nested = self.client.get("/api/agent-projects/folders?root_id=code&subpath=journl").get_json()
        self.assertEqual([item["name"] for item in nested["folders"]], ["app"])
        self.assertEqual(nested["parent"], "")

    def test_folder_listing_rejects_traversal(self):
        response = self.client.get("/api/agent-projects/folders?root_id=code&subpath=../docs")
        self.assertEqual(response.status_code, 400)

    def test_unknown_root_is_rejected(self):
        response = self.client.get("/api/agent-projects/folders?root_id=nope")
        self.assertEqual(response.status_code, 400)

    def test_write_mode_requires_a_writable_root(self):
        with self.assertRaises(agent_projects.AgentProjectError):
            agent_projects.resolve_project_dir("docs", "manuals", "write")
        self.assertTrue(agent_projects.resolve_project_dir("docs", "manuals", "read")["path"].is_dir())

    def test_symlinked_project_folder_is_refused(self):
        link = self.project_root / "escape"
        link.symlink_to(self.readonly_root)
        with self.assertRaises(agent_projects.AgentProjectError):
            agent_projects.resolve_project_dir("code", "escape", "read")

    # ─── connections ──────────────────────────────────────────────────
    def test_connection_round_trip_and_default_provider(self):
        connection = self._connection().get_json()["connection"]
        self.assertEqual(connection["provider_id"], "lmstudio")
        self.assertEqual(connection["base_url"], "http://127.0.0.1:1234")
        listed = self.client.get("/api/agent-projects/bootstrap").get_json()["connections"]
        self.assertEqual([item["id"] for item in listed], [connection["id"]])

    def test_public_endpoints_are_refused(self):
        for url in ("http://93.184.216.34:1234", "http://169.254.169.254", "https://example.com"):
            with self.subTest(url=url):
                response = self._connection(base_url=url)
                self.assertEqual(response.status_code, 400)

    def test_endpoint_path_and_credentials_are_refused(self):
        for url in ("http://127.0.0.1:1234/v1/chat/completions", "http://user:pw@127.0.0.1:1234"):
            with self.subTest(url=url):
                self.assertEqual(self._connection(base_url=url).status_code, 400)

    def test_unknown_runner_is_refused(self):
        self.assertEqual(self._connection(runner="bash").status_code, 400)

    def test_model_discovery_reads_lmstudio_and_ollama_shapes(self):
        connection = self._connection().get_json()["connection"]
        payload = json.dumps({"data": [{"id": "qwen3-coder"}, {"id": "gemma"}]}).encode()
        with mock.patch.object(agent_projects, "_fetch_json", return_value=json.loads(payload)):
            found = self.client.get(
                f"/api/agent-projects/models?connection_id={connection['id']}").get_json()
        self.assertEqual(found["models"], ["gemma", "qwen3-coder"])
        with mock.patch.object(agent_projects, "_fetch_json",
                               return_value={"models": [{"model": "llama3.2:3b"}]}):
            self.assertEqual(agent_projects.discover_models("ollama", "http://127.0.0.1:11434"),
                             ["llama3.2:3b"])

    def test_model_discovery_failure_is_a_bad_gateway(self):
        connection = self._connection().get_json()["connection"]
        with mock.patch.object(agent_projects, "_fetch_json",
                               side_effect=agent_projects.AgentProjectError("down")):
            response = self.client.get(
                f"/api/agent-projects/models?connection_id={connection['id']}")
        self.assertEqual(response.status_code, 502)

    # ─── filesystem date picker ───────────────────────────────────────
    def test_files_for_date_lists_the_journal_and_that_day_notes(self):
        day = self.data_dir / "user-a" / "2026" / "08" / "16"
        day.mkdir(parents=True)
        (day / "Journal_2026-08-16.md").write_text("Morgennotiz\n", encoding="utf-8")
        notes = self.data_dir / "user-a" / "notes"
        notes.mkdir(parents=True)
        (notes / "Idee.md").write_text("Idee\n", encoding="utf-8")
        os.utime(notes / "Idee.md", (1786_000_000, 1786_000_000))
        payload = self.client.get("/api/agent-projects/files?date=2026-08-16").get_json()
        self.assertEqual(payload["files"][0]["path"], "2026/08/16/Journal_2026-08-16.md")
        self.assertEqual(payload["files"][0]["kind"], "journal")

    def test_files_endpoint_rejects_a_broken_date(self):
        self.assertEqual(self.client.get("/api/agent-projects/files?date=16.08.2026").status_code, 400)

    def test_context_files_stay_inside_the_user_directory(self):
        with self.assertRaises(agent_projects.AgentProjectError):
            agent_projects.read_context_files("user-a", ["../user-b/secret.md"])

    # ─── projects ─────────────────────────────────────────────────────
    def test_project_requires_an_existing_connection(self):
        response = self._project("deadbeefcafe")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Verbindung", response.get_json()["error"])

    def test_project_round_trip_reports_folder_and_runner(self):
        _connection, project = self._ready_project()
        self.assertEqual(project["root_label"], "Code")
        self.assertEqual(project["runner"], "opencode")
        self.assertEqual(project["mode"], "write")
        self.assertEqual(project["problems"], [])

    def test_project_in_a_read_only_root_cannot_be_writable(self):
        connection = self._connection().get_json()["connection"]
        response = self._project(connection["id"], root_id="docs", subpath="manuals", mode="execute")
        self.assertEqual(response.status_code, 400)

    def test_deleting_a_connection_marks_dependent_projects(self):
        connection, project = self._ready_project()
        self.client.post("/api/agent-projects/connections", headers=self.headers,
                         json={"action": "delete", "id": connection["id"]})
        reloaded = self.client.get("/api/agent-projects/bootstrap").get_json()["projects"][0]
        self.assertEqual(reloaded["id"], project["id"])
        self.assertTrue(reloaded["problems"])

    # ─── job contract ─────────────────────────────────────────────────
    def test_submit_writes_a_project_job_and_a_session_document(self):
        _connection, project = self._ready_project()
        response = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                    headers=self.headers, json={"instruction": "Räume app/ auf."})
        self.assertEqual(response.status_code, 202)
        queued = response.get_json()
        job = json.loads((self.data_dir / "user-a" / "ai_jobs" / f"{queued['job_id']}.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(job["job_kind"], "agent_project")
        self.assertEqual(job["agent"], "opencode")
        self.assertEqual(job["model"], "lmstudio/qwen3-coder")
        self.assertEqual(job["project"]["mode"], "write")
        self.assertEqual(job["project"]["path"], str((self.project_root / "journl").resolve()))
        self.assertEqual(job["connection"]["base_url"], "http://127.0.0.1:1234")
        self.assertEqual(job["connection_manifest_path"],
                         f"user-a/ai_jobs/{queued['job_id']}.connection.json")
        session = (self.data_dir / "user-a" / queued["session_path"]).read_text(encoding="utf-8")
        self.assertIn("jt:agent-session-config", session)
        self.assertIn('"kind": "agent-project"', session)
        self.assertIn("Räume app/ auf.", session)

    def test_submit_requires_an_instruction(self):
        _connection, project = self._ready_project()
        response = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                    headers=self.headers, json={"instruction": "   "})
        self.assertEqual(response.status_code, 400)

    def test_submit_needs_csrf(self):
        _connection, project = self._ready_project()
        response = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                    json={"instruction": "Los"})
        self.assertEqual(response.status_code, 403)

    def test_second_turn_reuses_the_session_and_carries_the_runner_session(self):
        _connection, project = self._ready_project()
        first = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                 headers=self.headers, json={"instruction": "Erste Runde"}).get_json()
        session_path = self.data_dir / "user-a" / first["session_path"]
        session_path.write_text(session_path.read_text(encoding="utf-8").replace(
            '"runner_session": ""', '"runner_session": "ses_abc123"'), encoding="utf-8")
        # A finished job must not block the next turn.
        job_path = self.data_dir / "user-a" / "ai_jobs" / f"{first['job_id']}.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["status"] = "completed"
        job_path.write_text(json.dumps(job), encoding="utf-8")

        second = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                  headers=self.headers, json={"instruction": "Zweite Runde"}).get_json()
        self.assertEqual(second["session_id"], first["session_id"])
        second_job = json.loads((self.data_dir / "user-a" / "ai_jobs" / f"{second['job_id']}.json")
                                .read_text(encoding="utf-8"))
        self.assertEqual(second_job["runner_session"], "ses_abc123")

    def test_a_running_turn_blocks_the_next_one(self):
        _connection, project = self._ready_project()
        self.client.post(f"/api/agent-projects/{project['id']}/submit",
                         headers=self.headers, json={"instruction": "Erste Runde"})
        response = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                    headers=self.headers, json={"instruction": "Zweite Runde"})
        self.assertEqual(response.status_code, 409)

    def test_context_selection_becomes_one_immutable_context_file(self):
        connection = self._connection().get_json()["connection"]
        day = self.data_dir / "user-a" / "2026" / "08" / "16"
        day.mkdir(parents=True)
        (day / "Journal_2026-08-16.md").write_text("Heute: Refactoring\n", encoding="utf-8")
        notes = self.data_dir / "user-a" / "notes"
        notes.mkdir(parents=True)
        (notes / "Regeln.md").write_text("Immer Tests schreiben.\n", encoding="utf-8")
        project = self._project(connection["id"], include_today=True, journal_date="2026-08-16",
                                context_files=["notes/Regeln.md"]).get_json()["project"]
        queued = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                  headers=self.headers, json={"instruction": "Los"}).get_json()
        context = (self.data_dir / "user-a" / "ai_jobs" / f"{queued['job_id']}.context.md") \
            .read_text(encoding="utf-8")
        self.assertIn("## Journal 2026-08-16", context)
        self.assertIn("Heute: Refactoring", context)
        self.assertIn("## Datei notes/Regeln.md", context)
        self.assertIn("Immer Tests schreiben.", context)

    def test_prompt_states_the_mode_rules(self):
        connection = self._connection().get_json()["connection"]
        project = self._project(connection["id"], mode="read").get_json()["project"]
        queued = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                  headers=self.headers, json={"instruction": "Analysiere"}).get_json()
        prompt = (self.data_dir / "user-a" / "ai_jobs" / f"{queued['job_id']}.prompt.md") \
            .read_text(encoding="utf-8")
        self.assertIn("ausschließlich LESEND", prompt)
        self.assertIn(str((self.project_root / "journl").resolve()), prompt)

    # ─── transcript and reopen ────────────────────────────────────────
    def test_turns_expose_the_request_and_the_agent_answer(self):
        _connection, project = self._ready_project()
        queued = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                  headers=self.headers, json={"instruction": "Was tut app/?"}).get_json()
        session_path = self.data_dir / "user-a" / queued["session_path"]
        session_path.write_text(session_path.read_text(encoding="utf-8") + (
            "\n\n___\n\n## Agentenantwort | Datum & Uhrzeit: 2026-08-16T12:00:00+02:00\n"
            '<!-- jt:agent-session {"at": "2026-08-16T12:00:00+02:00", "type": "agent-answer"} -->\n\n'
            "app/ enthält die Flask-App.\n\n___\n"), encoding="utf-8")
        payload = self.client.get(f"/api/agent-projects/{project['id']}/turns").get_json()
        kinds = [turn["kind"] for turn in payload["turns"]]
        self.assertEqual(kinds, ["request", "answer"])
        self.assertIn("Flask-App", payload["turns"][1]["body"])

    def test_a_session_resolves_back_to_its_project(self):
        _connection, project = self._ready_project()
        queued = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                  headers=self.headers, json={"instruction": "Los"}).get_json()
        payload = self.client.get(
            f"/api/agent-projects/by-session/{queued['session_id']}").get_json()
        self.assertEqual(payload["project"]["id"], project["id"])
        self.assertEqual(self.client.get(
            "/api/agent-projects/by-session/00000000-0000-0000-0000-000000000000").status_code, 404)

    def test_resetting_a_session_keeps_the_project(self):
        _connection, project = self._ready_project()
        first = self.client.post(f"/api/agent-projects/{project['id']}/submit",
                                 headers=self.headers, json={"instruction": "Los"}).get_json()
        job_path = self.data_dir / "user-a" / "ai_jobs" / f"{first['job_id']}.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["status"] = "completed"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        self.assertEqual(self.client.post(f"/api/agent-projects/{project['id']}/session",
                                          headers=self.headers, json={}).status_code, 200)
        reloaded = self.client.get("/api/agent-projects/bootstrap").get_json()["projects"][0]
        self.assertFalse(reloaded["has_session"])

    # ─── authentication ───────────────────────────────────────────────
    def test_every_endpoint_requires_a_session(self):
        anonymous = main.app.test_client()
        for method, url in (("get", "/api/agent-projects/bootstrap"),
                            ("get", "/api/agent-projects/folders?root_id=code"),
                            ("get", "/api/agent-projects/files?date=2026-08-16"),
                            ("post", "/api/agent-projects/connections")):
            with self.subTest(url=url):
                response = getattr(anonymous, method)(url, json={})
                self.assertEqual(response.status_code, 401)


class AgentProjectPanelSourceTests(unittest.TestCase):
    """Die Oberfläche des Modus — geprüft an der Quelle, ohne Browser."""

    def test_the_mode_can_be_left_without_knowing_the_template_chooser(self):
        source = (ROOT_DIR / "app" / "static" / "agent-projects.js").read_text(encoding="utf-8")
        # Kopfzeile mit Abbrechen liegt fest, der wechselnde Inhalt darunter:
        # sonst verschwindet der Ausgang hinter einer Lade- oder Fehlermeldung.
        self.assertIn("panel.append(head, body)", source)
        self.assertIn("data-leave", source)
        self.assertIn(">Abbrechen</button>", source)
        self.assertIn("head.querySelector('[data-leave]').addEventListener('click', exitMode)", source)
        self.assertIn("chooser.dispatchEvent(new Event('change', { bubbles: true }))", source)
        # Der aus Heute/Brain übernommene Eintragstext bleibt beim Abbrechen
        # nicht als scheinbar neue Notiz in der Box stehen.
        self.assertIn("if (box && injectedText !== null && box.value === injectedText) box.value = '';", source)
        self.assertNotIn("panel.innerHTML", source)


if __name__ == "__main__":
    unittest.main()
