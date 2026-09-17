"""Ausfuehrungsziele des AI-Worker-Bereichs.

Ort und Werkzeug gehoeren zusammen (E-35): ein Ziel bringt seine eigene
Modell-Liste mit, damit keine Kombination waehlbar ist, die es nicht gibt.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "workbench-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-workbench-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import agent_workbench
    import agent_projects


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class TargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.previous = main.DATA_DIR
        main.DATA_DIR = self.data
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        self.user = {"id": "user-1", "username": "admin", "admin": True}
        self.original_current_user = main._current_user
        self.original_store = agent_projects.load_store
        main._current_user = lambda: self.user
        agent_projects.load_store = lambda user_id: {"connections": []}

    def tearDown(self):
        main.DATA_DIR = self.previous
        main._current_user = self.original_current_user
        agent_projects.load_store = self.original_store
        self.temp.cleanup()

    def _host_targets(self, payload):
        (self.data / "host_targets.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def test_claude_target_offers_only_anthropic_models(self):
        self._host_targets({"tools": {"claude": {"available": True}}})
        targets = self.client.get("/api/worker/targets").get_json()["targets"]
        claude = next(entry for entry in targets if entry["tool"] == "claude")
        self.assertEqual(claude["models"],
                         [list(entry) for entry in agent_workbench.CLAUDE_MODELS])
        for model, label in claude["models"]:
            self.assertTrue(model.startswith("claude-"), model)
            self.assertTrue(label, model)
        # Die Reihenfolge ist die Empfehlung: das teuerste Modell steht nicht vorn.
        self.assertEqual(claude["models"][0][0], "claude-sonnet-5")
        # Claude Code kennt keinen Effort-Schalter.
        self.assertFalse(claude["supports_effort"])

    def test_pi_target_uses_the_models_reported_by_the_host(self):
        self._host_targets({"tools": {
            "claude": {"available": False},
            "pi": {"models": [{"id": "lmstudio/qwen3.6", "thinking": False},
                              {"id": "openai/gpt-5", "thinking": True}]},
        }})
        targets = self.client.get("/api/worker/targets").get_json()["targets"]
        self.assertEqual([entry["tool"] for entry in targets], ["pi"])
        self.assertEqual(targets[0]["models"], ["lmstudio/qwen3.6", "openai/gpt-5"])
        self.assertTrue(targets[0]["supports_effort"])

    def test_a_connection_becomes_a_target_with_its_own_runner(self):
        self._host_targets({"tools": {"claude": {"available": False}}})
        agent_projects.load_store = lambda user_id: {"connections": [
            {"id": "abc", "label": "LM Studio Mac", "runner": "opencode",
             "base_url": "http://192.168.1.50:1234"}]}
        targets = self.client.get("/api/worker/targets").get_json()["targets"]
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["tool"], "opencode")
        self.assertIn("LM Studio Mac", targets[0]["label"])
        # Ohne hinterlegtes Modell muss es eintragbar bleiben.
        self.assertTrue(targets[0]["free_model"])

    def test_targets_need_admin_rights(self):
        main._current_user = lambda: {"id": "u2", "username": "gast", "admin": False}
        self.assertEqual(self.client.get("/api/worker/targets").status_code, 403)

    def test_missing_host_report_does_not_break_the_list(self):
        targets = self.client.get("/api/worker/targets").get_json()["targets"]
        # Ohne Host-Meldung bleibt Claude als Ziel bestehen (es braucht keine).
        self.assertEqual([entry["tool"] for entry in targets], ["claude"])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.store = self.data / "user-1" / "tickets"
        self.store.mkdir(parents=True)
        self.previous = main.DATA_DIR
        main.DATA_DIR = self.data
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        self.user = {"id": "user-1", "username": "admin", "admin": True}
        self.original_current_user = main._current_user
        main._current_user = lambda: self.user
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.headers = {"X-CSRF-Token": "token"}
        self.session_id = "a" * 32
        self._write_session()

    def tearDown(self):
        main.DATA_DIR = self.previous
        main._current_user = self.original_current_user
        self.temp.cleanup()

    def _write_session(self, questions=None):
        (self.store / f"{self.session_id}.json").write_text(json.dumps({
            "schema": 1, "id": self.session_id, "ticket_id": "20260823-120000-aaaabbbb",
            "ticket_title": "Fehler im Heute-Tab", "status": "offen",
            "created_at": "2026-08-23T12:00:00+02:00", "runner_session": "sess-1",
            "questions": questions or [
                {"id": "q1", "type": "boolean", "question": "Auch mobil?", "options": [], "answer": None},
                {"id": "q2", "type": "choice", "question": "Variante?",
                 "options": ["A", "B"], "answer": None},
            ],
        }), encoding="utf-8")

    def _session(self):
        return json.loads((self.store / f"{self.session_id}.json").read_text(encoding="utf-8"))

    def test_open_questions_are_listed_with_a_waiting_count(self):
        payload = self.client.get("/api/worker/questions").get_json()
        self.assertEqual(payload["waiting"], 1)
        self.assertEqual(payload["items"][0]["open_count"], 2)
        self.assertEqual(payload["items"][0]["questions"][1]["options"], ["A", "B"])

    def test_answering_all_questions_marks_the_session_answered(self):
        first = self.client.post(f"/api/worker/questions/{self.session_id}/answer",
                                 json={"question_id": "q1", "answer": "ja"}, headers=self.headers)
        self.assertEqual(first.get_json()["remaining"], 1)
        # Solange eine Frage offen ist, bleibt die Sitzung offen.
        self.assertEqual(self._session()["status"], "offen")

        second = self.client.post(f"/api/worker/questions/{self.session_id}/answer",
                                  json={"question_id": "q2", "answer": "B"}, headers=self.headers)
        self.assertEqual(second.get_json()["remaining"], 0)
        self.assertEqual(self._session()["status"], "beantwortet")

    def test_an_invalid_choice_is_refused(self):
        response = self.client.post(f"/api/worker/questions/{self.session_id}/answer",
                                    json={"question_id": "q2", "answer": "C"}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_a_boolean_question_only_accepts_yes_or_no(self):
        response = self.client.post(f"/api/worker/questions/{self.session_id}/answer",
                                    json={"question_id": "q1", "answer": "vielleicht"},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_an_answered_session_is_not_changed_again(self):
        for question, answer in (("q1", "ja"), ("q2", "A")):
            self.client.post(f"/api/worker/questions/{self.session_id}/answer",
                             json={"question_id": question, "answer": answer}, headers=self.headers)
        response = self.client.post(f"/api/worker/questions/{self.session_id}/answer",
                                    json={"question_id": "q1", "answer": "nein"}, headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_answering_needs_csrf_and_admin(self):
        self.assertEqual(self.client.post(
            f"/api/worker/questions/{self.session_id}/answer",
            json={"question_id": "q1", "answer": "ja"}).status_code, 403)
        main._current_user = lambda: {"id": "u2", "username": "gast", "admin": False}
        self.assertEqual(self.client.get("/api/worker/questions").status_code, 403)

    def test_notifications_count_open_questions(self):
        payload = self.client.get("/api/worker/notifications").get_json()
        self.assertEqual(payload["questions"], 1)

        for question, answer in (("q1", "ja"), ("q2", "A")):
            self.client.post(f"/api/worker/questions/{self.session_id}/answer",
                             json={"question_id": question, "answer": answer},
                             headers=self.headers)
        self.assertEqual(
            self.client.get("/api/worker/notifications").get_json()["questions"], 0)

    def test_notifications_stay_silent_for_anonymous_callers(self):
        main._current_user = lambda: None
        payload = self.client.get("/api/worker/notifications").get_json()
        # Kein 401: die Linie darf im ausgeloggten Zustand einfach nichts melden.
        self.assertEqual(payload, {"questions": 0, "review": 0, "failed": 0})

    def test_a_foreign_session_identifier_is_refused(self):
        response = self.client.post("/api/worker/questions/..%2Fetc/answer",
                                    json={"question_id": "q1", "answer": "ja"},
                                    headers=self.headers)
        self.assertIn(response.status_code, (400, 404))


if __name__ == "__main__":
    unittest.main()
