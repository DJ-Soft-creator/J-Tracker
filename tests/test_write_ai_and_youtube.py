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
    os.environ.setdefault("SECRET_KEY", "write-ai-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-write-ai-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import main
    import tagging


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class WriteAiAndYoutubeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR)
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.data_dir, self.data_dir / "users.json", self.data_dir / "config.json"
        tagging.DATA_DIR, tagging.FAMILY_DIR = self.data_dir, self.data_dir / "family"
        self.data_dir.mkdir(exist_ok=True)
        main.USERS_PATH.write_text(json.dumps({"users": [{"id": "user-a", "username": "TestuserA", "password": "test"}]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"ai_providers": [{"id": "provider-a", "label": "Provider A", "model": "model-a", "api_url": "http://example.invalid"}], "templates": []}), encoding="utf-8")
        tagging.update_ai_workflow("user-a", "save", "ai-template", {"agent": "custom", "model": "model-a", "prompt": "Rewrite clearly", "context": "block", "target": "write_tab", "provider_id": "provider-a"})
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf-test"

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH, tagging.DATA_DIR, tagging.FAMILY_DIR = self.old
        self.temp.cleanup()

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def test_write_ai_uses_private_draft_and_never_writes_a_journal(self):
        with mock.patch.object(main, "_call_ai_api", return_value="AI result") as call:
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": "My private draft",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "AI result")
        self.assertEqual((self.data_dir / "user-a/temp_Eingabe.md").read_text(encoding="utf-8"), "AI result")
        self.assertFalse(list((self.data_dir / "user-a").rglob("Journal_*.md")))
        self.assertIn("My private draft", call.call_args.args[2])

    def test_second_device_draft_is_preserved_under_ai_result(self):
        draft = self.client.post("/api/write-ai/draft", headers=self.headers, json={"content": "base"}).get_json()
        def ai_call(*_):
            main.write_text_file(self.data_dir / "user-a/temp_Eingabe.md", "typed on another device")
            return "AI result"
        with mock.patch.object(main, "_call_ai_api", side_effect=ai_call):
            response = self.client.post("/api/write-ai/submit", headers=self.headers, json={
                "workflow_tag": "ai-template", "provider_id": "provider-a", "model": "model-a",
                "context_type": "draft", "text": "base", "revision": draft["revision"],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "AI result\n\ntyped on another device")

    def test_youtube_mode_is_dev_only_presentation_state(self):
        with mock.patch.object(main, "IS_DEV", True):
            response = self.client.post("/api/settings/youtube-mode", headers=self.headers, json={"enabled": True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["enabled"])
            self.assertEqual(response.get_json()["environment"], "dev")
        with mock.patch.object(main, "IS_DEV", False):
            self.assertEqual(self.client.get("/api/settings/youtube-mode").status_code, 404)


if __name__ == "__main__":
    unittest.main()
