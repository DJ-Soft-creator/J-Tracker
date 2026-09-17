"""Textbausteine ("Abkürzungen") für das Schreiben-Tab: Persistenz und API."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "text-snippets-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-text-snippets")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import main
    import text_snippets


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class TextSnippetApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH)
        main.DATA_DIR = self.data_dir
        main.USERS_PATH = self.data_dir / "users.json"
        main.CONFIG_PATH = self.data_dir / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "user-a", "username": "Alex", "password": "test"},
            {"id": "user-b", "username": "Kim", "password": "test"},
        ]}), encoding="utf-8")
        self.write_config({"app_version": "2.2.5", "templates": []})
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        self.login("user-a")

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf-test"

    def write_config(self, config):
        main.CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_config(self):
        return json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def put(self, snippets):
        return self.client.put("/api/settings/snippets", headers=self.headers, json={"snippets": snippets})

    def test_get_falls_back_to_the_built_in_defaults(self):
        response = self.client.get("/api/settings/snippets")
        self.assertEqual(response.status_code, 200)
        triggers = {item["trigger"] for item in response.get_json()["snippets"]}
        self.assertIn("-", triggers)

    def test_config_endpoint_carries_snippets_for_the_write_tab(self):
        cfg = self.client.get("/api/config").get_json()
        self.assertIn("-", {item["trigger"] for item in cfg["text_snippets"]})

    def test_any_signed_in_user_may_save_not_just_admins(self):
        self.login("user-b")
        response = self.put([{"trigger": "termin", "expansion": "Termin: "}])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.read_config()["text_snippets"][0]["trigger"], "termin")

    def test_write_requires_a_csrf_token(self):
        response = self.client.put("/api/settings/snippets", json={"snippets": []})
        self.assertEqual(response.status_code, 403)

    def test_saving_touches_only_the_snippets_key(self):
        self.write_config({"app_version": "2.2.5", "templates": [{"id": "schnell"}]})
        response = self.put([{"trigger": "-", "expansion": "- [ ] "}])
        self.assertEqual(response.status_code, 200)
        config = self.read_config()
        self.assertEqual(config["templates"], [{"id": "schnell"}])
        self.assertEqual(config["app_version"], "2.2.5")

    def test_duplicate_triggers_are_rejected(self):
        response = self.put([
            {"trigger": "aufgabe", "expansion": "a"},
            {"trigger": "Aufgabe", "expansion": "b"},
        ])
        self.assertEqual(response.status_code, 400)

    def test_a_trigger_with_whitespace_is_rejected(self):
        response = self.put([{"trigger": "neue aufgabe", "expansion": "- [ ] "}])
        self.assertEqual(response.status_code, 400)

    def test_an_empty_trigger_is_rejected(self):
        response = self.put([{"trigger": "  ", "expansion": "x"}])
        self.assertEqual(response.status_code, 400)

    def test_ids_are_stable_across_a_round_trip(self):
        first = self.put([{"trigger": "termin", "expansion": "Termin: "}]).get_json()["snippets"]
        second = self.put(first).get_json()["snippets"]
        self.assertEqual(first[0]["id"], second[0]["id"])


if __name__ == "__main__":
    unittest.main()
