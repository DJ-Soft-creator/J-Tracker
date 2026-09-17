"""Einkaufslisten-Agent: Wirkungsbereich, Werkzeuge und Rueckwaertsgang."""

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("PIL") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "shopping-agent-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-shopping-agent-import")
    sys.path.insert(0, str(ROOT / "app"))
    # Ladefolge ist Teil der Architektur: shopping_agent haengt an der
    # fertigen main-Fassade und darf nie vor ihr importiert werden.
    import main
    import brain
    import family
    import tagging
    import background_runs
    import brain_chat
    import shopping_agent


def _completion(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _tool_call(name, arguments, call_id="call-1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class ShoppingAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            family.DATA_DIR, family.FAMILY_DIR, family.PROJECTS_DIR, family.PLANNER_DIR,
            family.ARCHIVE_DIR, family.PLANNER_FILE, family.FAMILY_TASKS_FILE,
            tagging.DATA_DIR, tagging.FAMILY_DIR, tagging.INDEX_PATH, tagging._snapshot,
            brain.DATA_DIR, brain.FAMILY_DIR,
        )
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        family.DATA_DIR = self.data
        family.FAMILY_DIR = self.data / "family"
        family.PROJECTS_DIR = family.FAMILY_DIR / "projects"
        family.PLANNER_DIR = family.FAMILY_DIR / "planner"
        family.ARCHIVE_DIR = family.FAMILY_DIR / "archive"
        family.PLANNER_FILE = family.PLANNER_DIR / "recurring.md"
        family.FAMILY_TASKS_FILE = family.FAMILY_DIR / "Familien-Aufgaben.md"
        tagging.DATA_DIR = self.data
        tagging.FAMILY_DIR = self.data / "family"
        tagging.INDEX_PATH = self.data / "indexes/hashtag_index.json"
        tagging._snapshot = None
        brain.DATA_DIR = self.data
        brain.FAMILY_DIR = self.data / "family"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "user-a", "username": "Alex", "password": "x"},
            {"id": "user-b", "username": "Sam", "password": "x"},
        ]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": []}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        self._login("user-a")
        self._write_list("einkauf", ["Milch", "Apfel", "Tiefkuehlpizza"])
        self._settings("user-a")

    def tearDown(self):
        (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            family.DATA_DIR, family.FAMILY_DIR, family.PROJECTS_DIR, family.PLANNER_DIR,
            family.ARCHIVE_DIR, family.PLANNER_FILE, family.FAMILY_TASKS_FILE,
            tagging.DATA_DIR, tagging.FAMILY_DIR, tagging.INDEX_PATH, tagging._snapshot,
            brain.DATA_DIR, brain.FAMILY_DIR,
        ) = self.old
        self.temp.cleanup()

    # ─── Helfer ──────────────────────────────────────────────────────
    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf"

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf"}

    def _settings(self, user_id, base_url="http://192.168.1.5:8080", model="local"):
        path = self.data / user_id / ".config" / "brain-chat.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": 1, "base_url": base_url, "model": model, "api_key": "",
        }), encoding="utf-8")

    def _write_list(self, project_id, titles, assigned_users=None, template_id="einkaufsliste",
                    extra_tasks=()):
        family._ensure_dirs()
        tasks = [{
            "id": "t{}".format(number), "title": title, "user": "", "target_date": "",
            "completed": False, "created_at": "2026-01-01T10:00:00+00:00", "created_by": "user-a",
        } for number, title in enumerate(titles, 1)]
        tasks.extend(extra_tasks)
        project = {
            "id": project_id, "title": "Einkaufsliste", "type": "bucket",
            "template_id": template_id, "target_file": "einkaufsliste.md",
            "assigned_users": assigned_users or [], "created_at": "2026-01-01T10:00:00+00:00",
            "created_by": "user-a", "updated_at": "", "tasks": tasks, "comments": [],
        }
        (family.PROJECTS_DIR / "{}.md".format(project_id)).write_text(
            family.serialize_project(project), encoding="utf-8")
        return project

    def _titles(self, project_id="einkauf"):
        project = family._load_project(project_id)
        return [t["title"] for t in project["tasks"]]

    def _groups(self, project_id="einkauf"):
        project = family._load_project(project_id)
        return [(t["title"], t.get("gruppe", "")) for t in project["tasks"]]

    def _run(self, name, arguments, user_id="user-a", project_id="einkauf"):
        return shopping_agent.run_tool(user_id, project_id, name, arguments)

    def _knowledge(self, tag="rewe", relative="notes/rewe.md", body="## Gang 1\nObst\n## Gang 2\nMilch\n"):
        root = self.data / "user-a"
        (root / "notes").mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(body, encoding="utf-8")
        tagging.update_knowledge_source(
            "user-a", False, "save", tag, {"kind": "reference", "scope": "personal", "path": relative})

    # ─── Wirkungsbereich ─────────────────────────────────────────────
    def test_agent_cannot_reach_a_list_the_user_may_not_see(self):
        self._write_list("privat", ["Geschenk"], assigned_users=["user-b"])
        with self.assertRaises(shopping_agent.ShoppingAgentError):
            self._run("list_items", {}, project_id="privat")

    def test_agent_refuses_a_project_that_is_not_a_shopping_list(self):
        self._write_list("projekt", ["Etwas"], template_id="projekt")
        family._save_project({**family._load_project("projekt"), "target_file": ""})
        with self.assertRaises(shopping_agent.ShoppingAgentError):
            self._run("list_items", {}, project_id="projekt")

    def test_unknown_tool_is_correctable_for_the_model(self):
        import mcp_tools
        with self.assertRaises(mcp_tools.ToolInputError):
            self._run("delete_everything", {})

    def test_recurring_tasks_stay_out_of_the_agents_reach(self):
        """Der Planer besitzt wiederkehrende Aufgaben, nicht der Agent.

        Eine Projektdatei traegt diese Markierung nie — ``_parse_task_line``
        setzt jede gelesene Aufgabe auf ``manual``. Sie entsteht erst, wenn
        family ein Projekt-Dict aus dem Planer zusammensetzt. Genau dieser
        Dict-Pfad wird hier geprueft, statt eine Persistenz vorzutaeuschen.
        """
        project = {"tasks": [
            {"id": "t1", "title": "Milch"},
            {"id": "r1", "title": "Wocheneinkauf", "source": "recurring"},
            {"id": "r2", "title": "Getraenkekiste", "recurring": True},
        ]}
        self.assertEqual([t["title"] for t in shopping_agent._items_of(project)], ["Milch"])
        self.assertEqual([t["title"] for t in shopping_agent._recurring_of(project)],
                         ["Wocheneinkauf", "Getraenkekiste"])

    # ─── Werkzeuge ───────────────────────────────────────────────────
    def test_list_items_is_numbered_from_one(self):
        listed = self._run("list_items", {})
        self.assertEqual([item["nummer"] for item in listed["artikel"]], [1, 2, 3])
        self.assertEqual(listed["artikel"][0]["titel"], "Milch")

    def test_reorder_applies_groups_and_order(self):
        result = self._run("reorder_items", {"rubriken": [
            {"name": "Obst & Gemuese", "artikel": [2]},
            {"name": "Kuehlung", "artikel": [1]},
            {"name": "Tiefkuehlung", "artikel": [3]},
        ]})
        self.assertEqual(result["sortiert"], 3)
        self.assertEqual(self._groups(), [
            ("Apfel", "Obst & Gemuese"), ("Milch", "Kuehlung"), ("Tiefkuehlpizza", "Tiefkuehlung"),
        ])

    def test_forgotten_items_are_appended_instead_of_lost(self):
        result = self._run("reorder_items", {"rubriken": [{"name": "Kuehlung", "artikel": [1]}]})
        self.assertEqual(result["nachgetragen"], 2)
        self.assertEqual(self._groups(), [
            ("Milch", "Kuehlung"), ("Apfel", "Unsortiert"), ("Tiefkuehlpizza", "Unsortiert"),
        ])

    def test_reorder_rejects_an_invented_item_number(self):
        import mcp_tools
        with self.assertRaises(mcp_tools.ToolInputError) as caught:
            self._run("reorder_items", {"rubriken": [{"name": "Kuehlung", "artikel": [9]}]})
        self.assertIn("keinen Artikel 9", str(caught.exception))
        self.assertEqual(self._titles(), ["Milch", "Apfel", "Tiefkuehlpizza"])

    def test_reorder_ignores_a_duplicated_item_number(self):
        self._run("reorder_items", {"rubriken": [
            {"name": "A", "artikel": [1, 1]}, {"name": "B", "artikel": [1, 2, 3]},
        ]})
        self.assertEqual(self._titles(), ["Milch", "Apfel", "Tiefkuehlpizza"])
        self.assertEqual(dict(self._groups())["Milch"], "A")

    def test_add_and_remove_items(self):
        self._run("add_items", {"titel": ["Butter", "Brot"], "rubrik": "Kuehlung"})
        self.assertEqual(self._titles(), ["Milch", "Apfel", "Tiefkuehlpizza", "Butter", "Brot"])
        removed = self._run("remove_items", {"artikel": [1, 3]})
        self.assertEqual(removed["entfernt"], ["Milch", "Tiefkuehlpizza"])
        self.assertEqual(self._titles(), ["Apfel", "Butter", "Brot"])

    def test_a_title_may_not_break_the_markdown_task_line(self):
        import mcp_tools
        with self.assertRaises(mcp_tools.ToolInputError):
            self._run("add_items", {"titel": ["Milch | title: gehackt"]})
        with self.assertRaises(mcp_tools.ToolInputError):
            self._run("rename_item", {"artikel": 1, "titel": "Zeile\nZweite"})

    def test_rename_and_complete(self):
        renamed = self._run("rename_item", {"artikel": 1, "titel": "Milch 2 Liter"})
        self.assertEqual(renamed["vorher"], "Milch")
        self.assertEqual(self._titles()[0], "Milch 2 Liter")
        self._run("set_completed", {"artikel": [1, 2], "abgehakt": True})
        project = family._load_project("einkauf")
        self.assertEqual([t["completed"] for t in project["tasks"]], [True, True, False])

    def test_writing_tools_keep_the_list_within_its_cap(self):
        import mcp_tools
        with mock.patch.object(shopping_agent, "MAX_ITEMS", 4):
            with self.assertRaises(mcp_tools.ToolInputError):
                self._run("add_items", {"titel": ["A", "B"]})

    # ─── Wissensnotizen ──────────────────────────────────────────────
    def test_knowledge_tags_are_listed_and_read(self):
        self._knowledge()
        listed = self._run("list_knowledge", {})
        self.assertIn("rewe", [item["hashtag"] for item in listed["hashtags"]])
        read = self._run("read_knowledge", {"hashtag": "#rewe"})
        self.assertIn("Gang 1", read["inhalt"])
        self.assertEqual(read["quelle"], "notes/rewe.md")

    def test_unknown_knowledge_tag_is_correctable_for_the_model(self):
        import mcp_tools
        with self.assertRaises(mcp_tools.ToolInputError) as caught:
            self._run("read_knowledge", {"hashtag": "aldi"})
        self.assertIn("list_knowledge", str(caught.exception))

    # ─── Rueckwaertsgang ─────────────────────────────────────────────
    def test_undo_restores_the_state_before_the_run(self):
        profile = shopping_agent._ShoppingProfile("user-a", "einkauf", "Einkaufsliste")
        profile.run_tool("remove_items", {"artikel": [1, 2, 3]})
        self.assertEqual(self._titles(), [])
        shopping_agent.undo("user-a", "einkauf")
        self.assertEqual(self._titles(), ["Milch", "Apfel", "Tiefkuehlpizza"])

    def test_a_read_only_run_keeps_an_older_undo_point(self):
        profile = shopping_agent._ShoppingProfile("user-a", "einkauf", "Einkaufsliste")
        profile.run_tool("remove_items", {"artikel": [1]})
        reader = shopping_agent._ShoppingProfile("user-a", "einkauf", "Einkaufsliste")
        reader.run_tool("list_items", {})
        self.assertFalse(reader.undo_saved)
        shopping_agent.undo("user-a", "einkauf")
        self.assertEqual(self._titles(), ["Milch", "Apfel", "Tiefkuehlpizza"])

    def test_undo_expires_and_is_offered_only_for_its_own_list(self):
        profile = shopping_agent._ShoppingProfile("user-a", "einkauf", "Einkaufsliste")
        profile.run_tool("remove_items", {"artikel": [1]})
        self.assertIsNone(shopping_agent.undo_available("user-a", "andere-liste"))
        path = shopping_agent._undo_path("user-a")
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["saved_at"] = time.time() - shopping_agent.UNDO_MAX_AGE_SECONDS - 10
        path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertIsNone(shopping_agent.undo_available("user-a", "einkauf"))
        with self.assertRaises(shopping_agent.ShoppingAgentError):
            shopping_agent.undo("user-a", "einkauf")

    # ─── Werkzeugschleife ────────────────────────────────────────────
    def test_loop_sorts_a_list_through_the_model(self):
        self._knowledge()
        responses = [
            _completion(tool_calls=[_tool_call("read_knowledge", {"hashtag": "rewe"}, "c1")]),
            _completion(tool_calls=[_tool_call("reorder_items", {"rubriken": [
                {"name": "Gang 1", "artikel": [2]},
                {"name": "Gang 2", "artikel": [1, 3]},
            ]}, "c2")]),
            _completion(content="Nach dem Rewe-Aufbau sortiert."),
        ]
        with mock.patch.object(shopping_agent, "_post", side_effect=responses):
            result = shopping_agent.answer(
                "user-a", "einkauf", [{"role": "user", "content": "sortier nach rewe"}],
                shopping_agent.load_settings("user-a"))
        self.assertEqual(result["answer"], "Nach dem Rewe-Aufbau sortiert.")
        self.assertTrue(result["changed"])
        self.assertTrue(result["can_undo"])
        self.assertEqual([step["tool"] for step in result["steps"]],
                         ["read_knowledge", "reorder_items"])
        self.assertEqual(self._groups(), [
            ("Apfel", "Gang 1"), ("Milch", "Gang 2"), ("Tiefkuehlpizza", "Gang 2"),
        ])

    def test_a_correctable_tool_error_goes_back_to_the_model(self):
        responses = [
            _completion(tool_calls=[_tool_call("reorder_items", {"rubriken": [
                {"name": "X", "artikel": [99]}]}, "c1")]),
            _completion(content="Ich habe nichts geaendert."),
        ]
        with mock.patch.object(shopping_agent, "_post", side_effect=responses):
            result = shopping_agent.answer(
                "user-a", "einkauf", [{"role": "user", "content": "sortier"}],
                shopping_agent.load_settings("user-a"))
        self.assertIn("error", result["steps"][0])
        self.assertFalse(result["changed"])
        self.assertEqual(self._titles(), ["Milch", "Apfel", "Tiefkuehlpizza"])

    def test_a_read_only_answer_reports_no_change(self):
        responses = [
            _completion(tool_calls=[_tool_call("list_items", {}, "c1")]),
            _completion(content="Es sind drei Artikel drauf."),
        ]
        with mock.patch.object(shopping_agent, "_post", side_effect=responses):
            result = shopping_agent.answer(
                "user-a", "einkauf", [{"role": "user", "content": "was steht drauf"}],
                shopping_agent.load_settings("user-a"))
        self.assertFalse(result["changed"])
        self.assertFalse(result["can_undo"])

    def test_an_unconfigured_connection_names_where_to_fix_it(self):
        (self.data / "user-a" / ".config" / "brain-chat.json").unlink()
        with self.assertRaises(shopping_agent.ShoppingAgentError) as caught:
            shopping_agent.answer("user-a", "einkauf", [{"role": "user", "content": "x"}],
                                  shopping_agent.load_settings("user-a"))
        self.assertIn("Chat with your Notes", str(caught.exception))

    # ─── Anbieterauswahl ─────────────────────────────────────────────
    def _config(self, providers):
        main.CONFIG_PATH.write_text(
            json.dumps({"templates": [], "ai_providers": providers}), encoding="utf-8")

    def test_provider_options_hide_keys_and_unresolved_placeholders(self):
        self._config([
            {"id": "lm", "name": "LM Studio", "api_url": "http://192.168.1.5:1234/v1/chat/completions",
             "model": "qwen", "api_key": "secret-local"},
            {"id": "cloud", "api_url": "https://openrouter.ai/api/v1/chat/completions",
             "model": "deepseek", "api_key": "secret-cloud"},
            {"id": "broken", "api_url": "http://{locale_LLM_IP}:1234/v1/chat/completions",
             "model": "x"},
            {"id": "modellos", "api_url": "http://192.168.1.5:1234/v1/chat/completions"},
        ])
        options = shopping_agent.provider_options("user-a")
        self.assertEqual([item["id"] for item in options],
                         [shopping_agent.CHAT_PROVIDER_ID, "lm", "cloud"])
        self.assertEqual([item["label"] for item in options[1:]], ["LM Studio", "cloud"])
        self.assertEqual([item["local"] for item in options], [True, True, False])
        serialised = json.dumps(options)
        self.assertNotIn("secret-local", serialised)
        self.assertNotIn("secret-cloud", serialised)

    def test_resolve_settings_picks_the_named_provider(self):
        self._config([{"id": "cloud", "api_url": "https://openrouter.ai/api/v1/chat/completions",
                       "model": "deepseek", "api_key": "secret-cloud"}])
        settings = shopping_agent.resolve_settings("user-a", "cloud")
        self.assertEqual(settings["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(settings["model"], "deepseek")
        self.assertEqual(settings["api_key"], "secret-cloud")
        import agent_loop
        self.assertEqual(agent_loop.endpoint_url(settings),
                         "https://openrouter.ai/api/v1/chat/completions")

    def test_an_unknown_provider_falls_back_instead_of_failing(self):
        """Ein veralteter Merker im Browser darf den Lauf nicht kosten."""
        settings = shopping_agent.resolve_settings("user-a", "verschwunden")
        self.assertEqual(settings["model"], "local")
        self.assertEqual(settings["base_url"], "http://192.168.1.5:8080")

    def test_the_chat_connection_still_builds_its_own_path(self):
        import agent_loop
        settings = shopping_agent.resolve_settings("user-a")
        self.assertEqual(agent_loop.endpoint_url(settings),
                         "http://192.168.1.5:8080/v1/chat/completions")

    def test_the_run_uses_the_provider_it_was_started_with(self):
        self._config([{"id": "cloud", "api_url": "https://openrouter.ai/api/v1/chat/completions",
                       "model": "deepseek", "api_key": "secret-cloud"}])
        seen = {}

        def fake_post(settings, body):
            seen.update(settings=settings, model=body.get("model"))
            return _completion(content="fertig")

        handle = mock.Mock(user_id="user-a")
        with mock.patch.object(shopping_agent, "_post", side_effect=fake_post):
            shopping_agent.ask_handler(handle, {
                "messages": [{"role": "user", "content": "sortier"}],
                "project_id": "einkauf", "provider_id": "cloud",
            })
        self.assertEqual(seen["model"], "deepseek")
        self.assertEqual(seen["settings"]["api_key"], "secret-cloud")

    # ─── HTTP ────────────────────────────────────────────────────────
    def test_state_reports_connection_knowledge_and_undo(self):
        self._knowledge()
        response = self.client.get("/api/family/shopping-agent/einkauf/state")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["default_provider"], shopping_agent.CHAT_PROVIDER_ID)
        self.assertEqual([item["id"] for item in payload["providers"]],
                         [shopping_agent.CHAT_PROVIDER_ID])
        self.assertIn("rewe", [item["hashtag"] for item in payload["knowledge"]])
        self.assertFalse(payload["can_undo"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_state_is_denied_for_a_foreign_list(self):
        self._write_list("privat", ["Geschenk"], assigned_users=["user-b"])
        self.assertEqual(
            self.client.get("/api/family/shopping-agent/privat/state").status_code, 404)

    def test_ask_requires_a_login_and_a_csrf_token(self):
        body = {"messages": [{"role": "user", "content": "sortier"}]}
        self.assertEqual(self.client.post(
            "/api/family/shopping-agent/einkauf/ask", json=body).status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.post(
            "/api/family/shopping-agent/einkauf/ask", json=body, headers=self.headers,
        ).status_code, 401)

    def test_ask_queues_a_run_instead_of_answering_inline(self):
        with mock.patch.object(background_runs, "_spawn", return_value=None):
            response = self.client.post(
                "/api/family/shopping-agent/einkauf/ask", headers=self.headers,
                json={"messages": [{"role": "user", "content": "sortier nach rewe"}]})
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        run = response.get_json()["run"]
        self.assertEqual(run["kind"], shopping_agent.RUN_KIND)
        record = background_runs.read("user-a", run["id"])
        self.assertEqual(record["params"]["project_id"], "einkauf")
        self.assertEqual(record["params"]["provider_id"], "")

    def test_ask_rejects_an_empty_history(self):
        response = self.client.post(
            "/api/family/shopping-agent/einkauf/ask", headers=self.headers, json={"messages": []})
        self.assertEqual(response.status_code, 400)

    def test_undo_endpoint_restores_and_then_reports_nothing_to_undo(self):
        profile = shopping_agent._ShoppingProfile("user-a", "einkauf", "Einkaufsliste")
        profile.run_tool("remove_items", {"artikel": [1]})
        response = self.client.post(
            "/api/family/shopping-agent/einkauf/undo", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self._titles(), ["Milch", "Apfel", "Tiefkuehlpizza"])
        self.assertEqual(self.client.post(
            "/api/family/shopping-agent/einkauf/undo", headers=self.headers, json={},
        ).status_code, 400)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
