"""„Chat with your Notes": Bereichsgrenze, Geheimnisse und Werkzeugschleife."""

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
    os.environ.setdefault("SECRET_KEY", "brain-chat-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-brain-chat-import")
    sys.path.insert(0, str(ROOT / "app"))
    # Ladefolge ist Teil der Architektur: brain_chat haengt (ueber mcp_server)
    # an der fertigen main-Fassade und darf nie vor ihr importiert werden.
    import main
    import brain
    import tagging
    import background_runs
    import brain_chat
    import brain_chat_sessions


def _completion(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _tool_call(name, arguments, call_id="call-1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class BrainChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            tagging.DATA_DIR, tagging.FAMILY_DIR, tagging.INDEX_PATH, tagging._snapshot,
            brain.DATA_DIR, brain.FAMILY_DIR,
        )
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
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
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf"
        self._write_journals()
        self.enqueue = mock.patch.object(brain, "enqueue_rebuild", return_value=True)
        self.enqueue.start()

    def tearDown(self):
        self.enqueue.stop()
        (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            tagging.DATA_DIR, tagging.FAMILY_DIR, tagging.INDEX_PATH, tagging._snapshot,
            brain.DATA_DIR, brain.FAMILY_DIR,
        ) = self.old
        self.temp.cleanup()

    def _write(self, relative, content):
        path = self.data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_journals(self):
        for owner in ("user-a", "user-b"):
            tagging.update_catalog(owner, False, "approve", "focus")
        self._write(
            "user-a/2026/08/03/Journal_2026-08-03.md",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-03 12:00:00\nEigener Eintrag #focus\n\n___\n",
        )
        self._write(
            "user-b/2026/08/03/Journal_2026-08-03.md",
            "___\n\n## Fremd | Datum & Uhrzeit: 2026-08-03 09:00:00\nGeheim #focus\n\n___\n",
        )
        self._write("family/Einkauf.md", "# Einkauf\n\nBrot besorgen #focus\n")
        tagging.rebuild_index(["user-a", "user-b"])

    def _headers(self):
        return {"X-CSRF-Token": "csrf"}

    def _configure(self, model="local-model", api_key=""):
        brain_chat.save_settings("user-a", {
            "base_url": "http://192.168.1.50:8080", "model": model, "api_key": api_key,
        })

    # ─── Einstellungen ───────────────────────────────────────────────

    def test_the_stored_api_key_never_reaches_the_browser(self):
        self._configure(api_key="secret-key")
        response = self.client.get("/api/brain/chat/settings")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertNotIn("api_key", payload)
        self.assertTrue(payload["has_api_key"])
        self.assertNotIn("secret-key", response.get_data(as_text=True))

    def test_saving_without_the_key_field_keeps_the_stored_secret(self):
        self._configure(api_key="secret-key")
        response = self.client.post(
            "/api/brain/chat/settings", headers=self._headers(),
            json={"base_url": "http://192.168.1.50:8080", "model": "other-model"},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        stored = brain_chat.load_settings("user-a")
        self.assertEqual(stored["model"], "other-model")
        self.assertEqual(stored["api_key"], "secret-key")

    def test_an_empty_key_field_clears_the_stored_secret(self):
        self._configure(api_key="secret-key")
        brain_chat.save_settings("user-a", {
            "base_url": "http://192.168.1.50:8080", "model": "local-model", "api_key": "",
        })
        self.assertEqual(brain_chat.load_settings("user-a")["api_key"], "")

    def test_a_public_endpoint_is_refused(self):
        with self.assertRaises(brain_chat.BrainChatError):
            brain_chat.save_settings("user-a", {"base_url": "https://api.openai.com", "model": "gpt"})

    def test_settings_require_a_session_and_a_csrf_token(self):
        anonymous = main.app.test_client()
        self.assertEqual(anonymous.get("/api/brain/chat/settings").status_code, 401)
        self.assertEqual(anonymous.post("/api/brain/chat/ask", json={"messages": []}).status_code, 401)
        response = self.client.post(
            "/api/brain/chat/settings",
            json={"base_url": "http://192.168.1.50:8080", "model": "local-model"},
        )
        self.assertEqual(response.status_code, 403)

    # ─── Werkzeuge ───────────────────────────────────────────────────

    def test_the_chat_offers_exactly_the_private_mcp_tools(self):
        names = {tool["function"]["name"] for tool in brain_chat.tool_specs()}
        self.assertTrue({"list_tags", "list_days", "get_blocks", "get_day"}.issubset(names))
        self.assertNotIn("sources", json.dumps(brain_chat.tool_specs()))

    def test_a_tool_never_reaches_another_users_journal(self):
        payload = brain_chat.run_tool("user-a", "get_blocks", {"tags": ["focus"]})
        texts = " ".join(block["text"] for block in payload["blocks"])
        self.assertIn("Eigener Eintrag", texts)
        self.assertNotIn("Geheim", texts)

    def test_the_model_cannot_widen_the_area_through_arguments(self):
        # Family documents carry the same hashtag; only the endpoint sets the area.
        payload = brain_chat.run_tool("user-a", "get_blocks", {"tags": ["focus"]})
        self.assertNotIn("Brot besorgen", " ".join(b["text"] for b in payload["blocks"]))
        with self.assertRaises(brain_chat.mcp_tools.ToolInputError):
            brain_chat.run_tool("user-a", "get_blocks", {"tags": ["focus"], "sources": ["family"]})

    def test_an_unknown_tool_is_reported_as_a_correctable_input_error(self):
        with self.assertRaises(brain_chat.mcp_tools.ToolInputError):
            brain_chat.run_tool("user-a", "delete_everything", {})

    # ─── Schleife ────────────────────────────────────────────────────

    def test_a_tool_call_round_is_executed_and_traced(self):
        self._configure()
        responses = [
            _completion(tool_calls=[_tool_call("get_blocks", {"tags": ["focus"]})]),
            _completion(content="Am 2026-08-03 ging es um #focus."),
        ]
        with mock.patch.object(brain_chat, "_post", side_effect=responses) as post:
            result = brain_chat.answer(
                "user-a", [{"role": "user", "content": "Worum ging es?"}],
                brain_chat.load_settings("user-a"),
            )
        self.assertEqual(result["answer"], "Am 2026-08-03 ging es um #focus.")
        self.assertEqual([step["tool"] for step in result["steps"]], ["get_blocks"])
        self.assertFalse(result["truncated"])
        # Die zweite Runde muss das Werkzeugergebnis wirklich mitschicken.
        second = post.call_args_list[1].args[1]["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertIn("Eigener Eintrag", second[-1]["content"])

    def test_a_failing_tool_is_handed_back_to_the_model_instead_of_aborting(self):
        self._configure()
        responses = [
            _completion(tool_calls=[_tool_call("get_day", {"start_date": "keindatum"})]),
            _completion(content="Das Datum war unbrauchbar."),
        ]
        with mock.patch.object(brain_chat, "_post", side_effect=responses) as post:
            result = brain_chat.answer(
                "user-a", [{"role": "user", "content": "Was war da?"}],
                brain_chat.load_settings("user-a"),
            )
        self.assertIn("error", result["steps"][0])
        self.assertEqual(post.call_args_list[1].args[1]["messages"][-1]["role"], "tool")

    def test_the_loop_is_capped_and_forces_a_final_answer_without_tools(self):
        self._configure()
        responses = [
            _completion(tool_calls=[_tool_call("list_tags", {})])
            for _ in range(brain_chat.MAX_ROUNDS)
        ] + [_completion(content="Soweit ich sehen konnte: nichts Neues.")]
        with mock.patch.object(brain_chat, "_post", side_effect=responses) as post:
            result = brain_chat.answer(
                "user-a", [{"role": "user", "content": "Und?"}],
                brain_chat.load_settings("user-a"),
            )
        self.assertTrue(result["truncated"])
        self.assertEqual(post.call_count, brain_chat.MAX_ROUNDS + 1)
        self.assertNotIn("tools", post.call_args_list[-1].args[1])

    def test_an_unconfigured_connection_fails_with_a_readable_hint(self):
        with self.assertRaises(brain_chat.BrainChatError) as caught:
            brain_chat.answer("user-a", [{"role": "user", "content": "Hallo"}],
                              brain_chat.load_settings("user-a"))
        self.assertIn("Verbindung", str(caught.exception))

    def test_the_history_must_end_with_a_user_message(self):
        with self.assertRaises(brain_chat.BrainChatError):
            brain_chat.normalise_history([{"role": "assistant", "content": "hm"}])
        with self.assertRaises(brain_chat.BrainChatError):
            brain_chat.normalise_history([])

    def _await_run(self, run_id, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = background_runs.read("user-a", run_id)
            if record and record["status"] in background_runs.FINAL_STATUSES:
                return record
            time.sleep(0.02)
        self.fail("Die Frage wurde nicht innerhalb von {}s beantwortet".format(timeout))

    def _ask(self, question, session_path="", responses=None):
        with mock.patch.object(brain_chat, "_post", side_effect=responses):
            response = self.client.post(
                "/api/brain/chat/ask", headers=self._headers(),
                json={"messages": [{"role": "user", "content": question}],
                      "session_path": session_path},
            )
            self.assertEqual(response.status_code, 202, response.get_json())
            run_id = response.get_json()["run"]["id"]
            return self._await_run(run_id)

    def test_the_answer_arrives_even_when_the_browser_is_gone(self):
        """Der Kern des Umbaus: die Antwort haengt nicht mehr am offenen Request."""
        self._configure()
        record = self._ask("Wann?", responses=[
            _completion(tool_calls=[_tool_call("list_days", {})]),
            _completion(content="Am 2026-08-03."),
        ])

        self.assertEqual(record["status"], "done")
        self.assertEqual(record["result"]["answer"], "Am 2026-08-03.")
        self.assertEqual(record["result"]["steps"][0]["tool"], "list_days")
        # Die Werkzeugschritte stehen schon waehrend des Laufs im Protokoll.
        self.assertEqual(record["log"][0]["tool"], "list_days")

    def test_every_turn_lands_in_the_chat_file_of_the_day(self):
        self._configure()
        record = self._ask("Wann?", responses=[_completion(content="Am 2026-08-03.")])

        relative = record["result"]["session"]["path"]
        self.assertTrue(brain_chat_sessions.is_session_path(relative), relative)
        stored = (self.data / "user-a" / relative).read_text(encoding="utf-8")
        self.assertIn("type: brain-chat-session", stored)
        self.assertIn("Wann?", stored)
        self.assertIn("Am 2026-08-03.", stored)

    def test_a_second_question_appends_to_the_same_file(self):
        self._configure()
        first = self._ask("Wann?", responses=[_completion(content="Am 2026-08-03.")])
        relative = first["result"]["session"]["path"]
        second = self._ask("Und davor?", session_path=relative,
                           responses=[_completion(content="Am 2026-08-01.")])

        self.assertEqual(second["result"]["session"]["path"], relative)
        stored = (self.data / "user-a" / relative).read_text(encoding="utf-8")
        self.assertIn("Und davor?", stored)
        self.assertIn("Am 2026-08-01.", stored)
        self.assertEqual(stored.count("type\": \"question"), 2)

    def test_the_stored_session_can_be_read_back_for_the_browser(self):
        """Nach einer Handysperre soll der Verlauf wieder im Chat stehen."""
        self._configure()
        record = self._ask("Wann?", responses=[
            _completion(tool_calls=[_tool_call("list_days", {})]),
            _completion(content="Am 2026-08-03."),
        ])
        relative = record["result"]["session"]["path"]

        response = self.client.get("/api/brain/chat/session?path={}".format(relative))
        self.assertEqual(response.status_code, 200)
        turns = response.get_json()["turns"]
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant"])
        self.assertEqual(turns[0]["content"], "Wann?")
        self.assertEqual(turns[1]["content"], "Am 2026-08-03.")
        self.assertEqual(turns[1]["steps"][0]["tool"], "list_days")

    def test_a_session_is_only_deleted_on_request(self):
        self._configure()
        record = self._ask("Wann?", responses=[_completion(content="Am 2026-08-03.")])
        relative = record["result"]["session"]["path"]

        deleted = self.client.delete("/api/brain/chat/session", headers=self._headers(),
                                     json={"path": relative})
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse((self.data / "user-a" / relative).exists())
        self.assertEqual(
            self.client.get("/api/brain/chat/session?path={}".format(relative)).status_code, 404,
        )

    def test_a_foreign_path_is_refused_instead_of_read_or_deleted(self):
        """Der Pfad kommt aus dem Browser und wird deshalb streng geprueft."""
        self._configure()
        self.assertEqual(
            self.client.get("/api/brain/chat/session?path=../users.json").status_code, 404,
        )
        for path in ("../users.json", "2026/08/31/Journal_2026-08-31.md", "notes/keep.md"):
            refused = self.client.post(
                "/api/brain/chat/ask", headers=self._headers(),
                json={"messages": [{"role": "user", "content": "Hallo"}], "session_path": path},
            )
            self.assertEqual(refused.status_code, 400, path)

    def test_a_failing_model_ends_the_run_with_a_readable_reason(self):
        self._configure()
        # Der Modellaufruf passiert im Lauf, nicht im Request: die Attrappe muss
        # deshalb bis zum Ende des Laufs stehen bleiben.
        with mock.patch.object(brain_chat, "_post",
                               side_effect=brain_chat.BrainChatError("Der Endpunkt ist nicht erreichbar: x")):
            response = self.client.post(
                "/api/brain/chat/ask", headers=self._headers(),
                json={"messages": [{"role": "user", "content": "Wann?"}]},
            )
            record = self._await_run(response.get_json()["run"]["id"])
        self.assertEqual(record["status"], "failed")
        self.assertIn("nicht erreichbar", record["error"])

    def test_an_empty_question_is_refused_before_a_run_starts(self):
        self._configure()
        response = self.client.post(
            "/api/brain/chat/ask", headers=self._headers(), json={"messages": []},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(background_runs.active("user-a", brain_chat.RUN_KIND))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
