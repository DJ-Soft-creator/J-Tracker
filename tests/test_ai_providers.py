"""KI-Anbieter und Standard-Agent: Persistenz, Geheimnisse und die Admin-API."""

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
    os.environ.setdefault("SECRET_KEY", "ai-providers-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-ai-providers")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import ai_providers
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class AiProviderApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH)
        main.DATA_DIR = self.data_dir
        main.USERS_PATH = self.data_dir / "users.json"
        main.CONFIG_PATH = self.data_dir / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "admin-a", "username": "Alex", "password": "test", "admin": True},
            {"id": "user-b", "username": "Kim", "password": "test"},
        ]}), encoding="utf-8")
        self.write_config({
            "app_version": "4.7.14",
            "templates": [{"id": "schnell"}],
            "ai_providers": [
                {"id": "lm", "label": "LM Studio", "model": "qwen",
                 "api_url": "http://{locale_LLM_IP}:1234/v1/chat/completions",
                 "api_key": "geheim-lokal", "max_tokens": 10000},
                {"id": "cloud", "label": "Cloud", "model": "deepseek",
                 "api_url": "https://openrouter.ai/api/v1/chat/completions",
                 "api_key": "geheim-cloud"},
            ],
        })
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        self.login("admin-a")

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

    def put(self, providers, default_id=""):
        return self.client.put("/api/worker/providers", headers=self.headers,
                               json={"providers": providers, "default_id": default_id})

    def fetched(self):
        response = self.client.get("/api/worker/providers")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    # ─── Lesen ────────────────────────────────────────────────────────
    def test_a_key_never_reaches_the_browser(self):
        payload = self.fetched()
        self.assertNotIn("geheim-lokal", json.dumps(payload))
        self.assertNotIn("geheim-cloud", json.dumps(payload))
        self.assertEqual([item["has_key"] for item in payload["shared"]], [True, True])

    def test_the_raw_placeholder_survives_and_the_resolved_url_comes_along(self):
        """Die Verwaltung schreibt Rohwerte zurueck, zeigt aber, worauf sie zeigen."""
        os.environ["LOCALE_LLM_IP"] = "192.168.1.5"
        old = main.LOCALE_LLM_IP
        main.LOCALE_LLM_IP = "192.168.1.5"
        try:
            payload = self.fetched()
        finally:
            main.LOCALE_LLM_IP = old
            os.environ.pop("LOCALE_LLM_IP", None)
        self.assertIn("{locale_LLM_IP}", payload["shared"][0]["api_url"])
        self.assertTrue(payload["shared"][0]["uses_placeholder"])
        self.assertEqual(payload["resolved"]["lm"],
                         "http://192.168.1.5:1234/v1/chat/completions")

    def test_only_admins_may_read_or_write(self):
        self.login("user-b")
        self.assertEqual(self.client.get("/api/worker/providers").status_code, 403)
        self.assertEqual(self.put([]).status_code, 403)

    def test_write_requires_a_csrf_token(self):
        response = self.client.put("/api/worker/providers", json={"providers": []})
        self.assertEqual(response.status_code, 403)

    # ─── Schreiben ────────────────────────────────────────────────────
    def test_saving_without_a_key_field_keeps_the_stored_key(self):
        """Der Browser kennt den Schluessel nicht und darf ihn nicht verlieren."""
        response = self.put([
            {"id": "lm", "label": "LM Studio neu", "model": "qwen",
             "api_url": "http://{locale_LLM_IP}:1234/v1/chat/completions"},
        ])
        self.assertEqual(response.status_code, 200)
        stored = self.read_config()["ai_providers"]
        self.assertEqual(stored[0]["api_key"], "geheim-lokal")
        self.assertEqual(stored[0]["label"], "LM Studio neu")

    def test_journal_analysis_approval_is_explicit_and_sticky(self):
        """Ein Cloud-Anbieter bekommt Journalinhalte nur nach ausdruecklichem Haken."""
        cloud = {"id": "cloud", "label": "Opus", "model": "anthropic/claude-opus",
                 "api_url": "https://openrouter.ai/api/v1/chat/completions"}
        self.put([cloud])
        self.assertFalse(self.read_config()["ai_providers"][0]["journal_analysis"])
        self.put([{**cloud, "journal_analysis": True}])
        self.assertTrue(self.read_config()["ai_providers"][0]["journal_analysis"])
        self.put([cloud])  # Feld fehlt -> bleibt
        self.assertTrue(self.read_config()["ai_providers"][0]["journal_analysis"])
        self.put([{**cloud, "journal_analysis": "ja"}])  # nur echtes true zaehlt
        self.assertFalse(self.read_config()["ai_providers"][0]["journal_analysis"])
        self.assertIn("journal_analysis", self.fetched()["shared"][0])

    def test_an_empty_key_field_clears_the_key(self):
        self.put([
            {"id": "lm", "label": "LM Studio", "model": "qwen", "api_key": "",
             "api_url": "http://{locale_LLM_IP}:1234/v1/chat/completions"},
        ])
        self.assertEqual(self.read_config()["ai_providers"][0]["api_key"], "")

    def test_saving_touches_only_the_provider_keys(self):
        self.put([
            {"id": "lm", "label": "LM Studio", "model": "qwen",
             "api_url": "http://{locale_LLM_IP}:1234/v1/chat/completions"},
        ], default_id="lm")
        config = self.read_config()
        self.assertEqual(config["templates"], [{"id": "schnell"}])
        self.assertEqual(config["app_version"], "4.7.14")
        self.assertEqual(config["ai_default_provider"], "lm")

    def test_the_default_must_exist(self):
        response = self.put([
            {"id": "lm", "label": "LM Studio", "model": "qwen",
             "api_url": "http://host:1234/v1/chat/completions"},
        ], default_id="weg")
        self.assertEqual(response.status_code, 400)
        self.assertIn("weg", response.get_json()["error"])

    def test_the_notes_chat_is_a_valid_default_although_it_is_not_in_the_list(self):
        response = self.put([], default_id=ai_providers.CHAT_PROVIDER_ID)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.read_config()["ai_default_provider"],
                         ai_providers.CHAT_PROVIDER_ID)

    def test_an_empty_default_removes_the_key_again(self):
        self.put([], default_id=ai_providers.CHAT_PROVIDER_ID)
        self.put([], default_id="")
        self.assertNotIn("ai_default_provider", self.read_config())

    def test_a_deleted_provider_disappears_from_the_file(self):
        self.put([
            {"id": "cloud", "label": "Cloud", "model": "deepseek",
             "api_url": "https://openrouter.ai/api/v1/chat/completions"},
        ])
        self.assertEqual([item["id"] for item in self.read_config()["ai_providers"]], ["cloud"])

    def test_unknown_fields_of_a_hand_written_entry_survive(self):
        """Die config.json wird auch von Hand gepflegt."""
        self.write_config({"ai_providers": [
            {"id": "lm", "label": "LM", "model": "qwen", "api_url": "http://host:1/v1",
             "eigenes_feld": "bitte behalten"},
        ]})
        self.put([{"id": "lm", "label": "LM", "model": "qwen", "api_url": "http://host:1/v1"}])
        self.assertEqual(self.read_config()["ai_providers"][0]["eigenes_feld"], "bitte behalten")

    # ─── Validierung ──────────────────────────────────────────────────
    def test_duplicate_ids_are_rejected(self):
        response = self.put([
            {"id": "lm", "label": "A", "model": "m", "api_url": "http://host:1/v1"},
            {"id": "lm", "label": "B", "model": "m", "api_url": "http://host:1/v1"},
        ])
        self.assertEqual(response.status_code, 400)

    def test_an_address_without_a_scheme_is_rejected(self):
        response = self.put([
            {"id": "lm", "label": "A", "model": "m", "api_url": "host:1234/v1"},
        ])
        self.assertEqual(response.status_code, 400)

    def test_the_placeholder_counts_as_a_valid_address(self):
        response = self.put([
            {"id": "lm", "label": "A", "model": "m",
             "api_url": "http://{locale_LLM_IP}:1234/v1/chat/completions"},
        ])
        self.assertEqual(response.status_code, 200)

    def test_a_missing_model_is_rejected(self):
        response = self.put([{"id": "lm", "label": "A", "model": "", "api_url": "http://host:1/v1"}])
        self.assertEqual(response.status_code, 400)

    def test_an_id_with_capitals_is_rejected(self):
        response = self.put([{"id": "LM", "label": "A", "model": "m", "api_url": "http://host:1/v1"}])
        self.assertEqual(response.status_code, 400)

    def test_an_absurd_temperature_is_rejected(self):
        response = self.put([
            {"id": "lm", "label": "A", "model": "m", "api_url": "http://host:1/v1",
             "temperature": 9},
        ])
        self.assertEqual(response.status_code, 400)

    def test_thinking_budget_is_stored_and_validated(self):
        self.put([
            {"id": "lm", "label": "A", "model": "m", "api_url": "http://host:1/v1",
             "thinking_budget": "4000"},
        ])
        self.assertEqual(self.read_config()["ai_providers"][0]["thinking_budget"], 4000)
        response = self.put([
            {"id": "lm", "label": "A", "model": "m", "api_url": "http://host:1/v1",
             "thinking_budget": -1},
        ])
        self.assertEqual(response.status_code, 400)
        self.put([
            {"id": "lm", "label": "A", "model": "m", "api_url": "http://host:1/v1",
             "thinking_budget": None},
        ])
        self.assertNotIn("thinking_budget", self.read_config()["ai_providers"][0])

    def test_optional_numbers_may_be_cleared(self):
        self.put([
            {"id": "lm", "label": "A", "model": "m", "api_url": "http://host:1/v1",
             "max_tokens": None, "temperature": None},
        ])
        stored = self.read_config()["ai_providers"][0]
        self.assertNotIn("max_tokens", stored)
        self.assertNotIn("temperature", stored)

    # ─── Env-Hoheit ───────────────────────────────────────────────────
    def test_fields_overridden_by_the_environment_are_flagged(self):
        """Ein still ueberschriebenes Feld waere die schlechtere Ueberraschung."""
        os.environ["AI_PROVIDER_1_MODEL"] = "von-aussen"
        os.environ["AI_KEY_LM"] = "von-aussen"
        try:
            payload = self.fetched()
        finally:
            os.environ.pop("AI_PROVIDER_1_MODEL", None)
            os.environ.pop("AI_KEY_LM", None)
        by_id = {item["id"]: item for item in payload["shared"]}
        self.assertIn("model", by_id["cloud"]["env_managed"])
        self.assertIn("api_key", by_id["lm"]["env_managed"])
        self.assertEqual(by_id["lm"]["env_managed"], ["api_key"])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class DefaultAgentTests(unittest.TestCase):
    """Der Standard-Agent wirkt dort, wo ein Agentenfenster aufgeht."""

    def setUp(self):
        import shopping_agent
        self.shopping_agent = shopping_agent
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.CONFIG_PATH)
        main.DATA_DIR = self.data_dir
        main.CONFIG_PATH = self.data_dir / "config.json"

    def tearDown(self):
        main.DATA_DIR, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    def _config(self, **extra):
        main.CONFIG_PATH.write_text(json.dumps({
            "ai_providers": [
                {"id": "lm", "label": "LM Studio", "model": "qwen",
                 "api_url": "http://192.168.1.5:1234/v1/chat/completions"},
                {"id": "cloud", "label": "Cloud", "model": "deepseek",
                 "api_url": "https://openrouter.ai/api/v1/chat/completions"},
            ],
            **extra,
        }), encoding="utf-8")

    def test_without_a_setting_the_first_option_wins(self):
        self._config()
        options = self.shopping_agent.provider_options("user-a")
        self.assertEqual(self.shopping_agent.default_provider_id(options), options[0]["id"])

    def test_the_configured_default_wins(self):
        self._config(ai_default_provider="cloud")
        options = self.shopping_agent.provider_options("user-a")
        self.assertEqual(self.shopping_agent.default_provider_id(options), "cloud")

    def test_a_stale_default_falls_back_instead_of_emptying_the_choice(self):
        self._config(ai_default_provider="abgeschaltet")
        options = self.shopping_agent.provider_options("user-a")
        self.assertEqual(self.shopping_agent.default_provider_id(options), options[0]["id"])

    def test_the_label_comes_from_the_config_field_that_actually_exists(self):
        """`label` steht in der config.json, `name` gab es dort nie."""
        self._config()
        labels = [item["label"] for item in self.shopping_agent.provider_options("user-a")]
        self.assertIn("LM Studio", labels)
        self.assertIn("Cloud", labels)

    def test_an_empty_wish_resolves_to_the_configured_default(self):
        self._config(ai_default_provider="cloud")
        settings = self.shopping_agent.resolve_settings("user-a")
        self.assertEqual(settings["model"], "deepseek")


if __name__ == "__main__":
    unittest.main()
