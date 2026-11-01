"""Bildschirmschoner-Modus „Fragen“: Sets, Ablauf, Generieren."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "screensaver-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-screensaver-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import screensaver_questions as questions


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class ScreensaverQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.previous = main.DATA_DIR
        main.DATA_DIR = self.data
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        self.user = {"id": "user-1", "username": "david"}
        self.original_current_user = main._current_user
        self.original_load_config = main.load_config
        self.original_update_user = main.update_user
        main.update_user = lambda user: self.user.update(user)
        main._current_user = lambda: self.user
        main.load_config = lambda: {"ai_providers": []}
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.headers = {"X-CSRF-Token": "token"}

    def tearDown(self):
        main.DATA_DIR = self.previous
        main._current_user = self.original_current_user
        main.load_config = self.original_load_config
        main.update_user = self.original_update_user
        self.temp.cleanup()

    def test_a_new_account_starts_with_the_starter_set(self):
        payload = self.client.get("/api/screensaver/questions").get_json()
        self.assertEqual(payload["sets"][0]["id"], "tagesreflexion")
        self.assertEqual(payload["flow"]["set_id"], "tagesreflexion")
        self.assertEqual(payload["flow"]["answer"], "impulse")
        energy = payload["sets"][0]["questions"][0]
        self.assertEqual(energy["choices"], ["Hoch", "Mittel", "Niedrig"])
        self.assertTrue(energy["required"])

    def test_sets_are_cleaned_and_saved_per_user(self):
        response = self.client.put("/api/screensaver/questions", headers=self.headers, json={
            "sets": [{"label": "Morgen", "questions": [
                {"text": "  Worauf freust du dich?  ", "seconds": 99999},
                {"text": "", "seconds": 10},
                {"text": "Stimmung?", "choices": ["gut", "gut", "", "mies"] + [str(i) for i in range(20)],
                 "seconds": 1, "required": 1},
            ]}],
            "flow": {"flow": "sequence", "answer": "session", "on_timeout": "wait", "seconds": 3, "set_id": "morgen"},
        })
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()
        first, second = saved["sets"][0]["questions"]
        self.assertEqual(first["text"], "Worauf freust du dich?")
        self.assertEqual(first["seconds"], questions.SECONDS_MAX)
        self.assertEqual(second["seconds"], questions.SECONDS_MIN)
        self.assertEqual(second["choices"][:2], ["gut", "mies"])
        self.assertEqual(len(second["choices"]), questions.MAX_CHOICES)
        self.assertTrue(second["required"])
        self.assertEqual(saved["flow"]["flow"], "sequence")
        self.assertEqual(saved["flow"]["on_timeout"], "wait")
        self.assertEqual(saved["flow"]["seconds"], questions.SECONDS_MIN)
        self.assertEqual(saved["flow"]["set_id"], "morgen")
        stored = json.loads((self.data / "user-1" / "screensaver" / "question-sets.json").read_text())
        self.assertEqual(stored["sets"][0]["label"], "Morgen")

    def test_saving_only_the_flow_keeps_the_sets(self):
        self.client.put("/api/screensaver/questions", headers=self.headers, json={"flow": {"seconds": 45}})
        payload = self.client.get("/api/screensaver/questions").get_json()
        self.assertEqual(payload["flow"]["seconds"], 45)
        self.assertEqual(payload["sets"][0]["id"], "tagesreflexion")

    def test_zero_seconds_is_the_manual_step(self):
        saved = self.client.put("/api/screensaver/questions", headers=self.headers,
                                json={"flow": {"seconds": 0}}).get_json()
        self.assertEqual(saved["flow"]["seconds"], 0)
        saved = self.client.put("/api/screensaver/questions", headers=self.headers,
                                json={"flow": {"seconds": False}}).get_json()
        self.assertEqual(saved["flow"]["seconds"], questions.DEFAULT_FLOW["seconds"])

    def test_loop_is_an_optional_flow_switch(self):
        payload = self.client.get("/api/screensaver/questions").get_json()
        self.assertFalse(payload["flow"]["loop"])
        saved = self.client.put("/api/screensaver/questions", headers=self.headers,
                                json={"flow": {"loop": True}}).get_json()
        self.assertTrue(saved["flow"]["loop"])
        saved = self.client.put("/api/screensaver/questions", headers=self.headers,
                                json={"flow": {"loop": "ja"}}).get_json()
        self.assertFalse(saved["flow"]["loop"], "Kein Bool - zurueck auf den Standard")

    def test_an_unknown_set_falls_back_to_the_first(self):
        saved = self.client.put("/api/screensaver/questions", headers=self.headers,
                                json={"flow": {"set_id": "gibt-es-nicht", "flow": "chaos"}}).get_json()
        self.assertEqual(saved["flow"]["set_id"], "tagesreflexion")
        self.assertEqual(saved["flow"]["flow"], "auto")

    def test_writes_need_the_csrf_token(self):
        self.assertEqual(self.client.put("/api/screensaver/questions", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/screensaver/questions/generate", json={}).status_code, 403)

    def test_generated_text_is_parsed_from_json_or_lines(self):
        parsed = questions.parse_generated('```json\n{"questions": [{"text": "Was war schön?", '
                                           '"choices": ["a", "b", "c", "d", "e", "f"]}]}\n```')
        self.assertEqual(parsed[0]["text"], "Was war schön?")
        self.assertEqual(len(parsed[0]["choices"]), 5)
        lines = questions.parse_generated("1. Erste Frage?\n- Zweite Frage?\n\n")
        self.assertEqual([item["text"] for item in lines], ["Erste Frage?", "Zweite Frage?"])

    def test_journal_context_reads_the_last_days_only(self):
        for day, text in (("2026-09-29", "alt"), ("2026-09-30", "gestern"), ("2026-10-01", "heute")):
            folder = self.data / "user-1" / day[:4] / day[5:7] / day[8:]
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"Journal_{day}.md").write_text(f"# {text}\n", encoding="utf-8")
        context = questions.journal_context("user-1", 2, self.data, today=date(2026, 10, 1))
        self.assertIn("gestern", context)
        self.assertIn("heute", context)
        self.assertNotIn("alt", context)
        self.assertEqual(questions.journal_context("user-1", 0, self.data), "")

    def test_generating_uses_only_a_local_provider(self):
        handle = mock.MagicMock(user_id="user-1")
        with mock.patch.object(questions, "local_providers", return_value=[]):
            with self.assertRaisesRegex(Exception, "lokaler KI-Anbieter"):
                questions.generate_handler(handle, {"count": 3})
        local = {"id": "lm_mac", "model": "m", "api_url": "http://10.0.0.2:8080/v1/chat/completions"}
        with mock.patch.object(questions, "local_providers", return_value=[local]), \
             mock.patch.object(main, "_call_ai_api", return_value='{"questions": [{"text": "Neu?"}]}') as call:
            result = questions.generate_handler(handle, {"count": 3, "instruction": "Woche"})
        self.assertEqual(result["questions"][0]["text"], "Neu?")
        self.assertEqual(call.call_args.args[0]["id"], "lm_mac")
        self.assertTrue(call.call_args.args[1]["stream"])
        self.assertIn("Woche", call.call_args.args[2])

    def test_prompts_are_saved_cleaned_and_kept_beside_the_sets(self):
        saved = self.client.put("/api/screensaver/questions", headers=self.headers, json={"prompts": [
            {"id": "wochenrueckblick", "label": "  Wochen  rückblick ", "instruction": "Arbeit und Familie",
             "count": 99, "days": 7, "provider_id": "lm_mac", "model": "qwen3-14b"},
            {"instruction": "Nur ein Wunsch ohne Namen"},
            {"label": "", "instruction": ""},
        ]}).get_json()
        self.assertEqual([item["label"] for item in saved["prompts"]],
                         ["Wochen rückblick", "Nur ein Wunsch ohne Namen"])
        first = saved["prompts"][0]
        self.assertEqual((first["count"], first["days"], first["model"]), (questions.GENERATE_MAX, 7, "qwen3-14b"))
        # Ein reines Set-Update laesst die Prompts stehen - und umgekehrt.
        self.client.put("/api/screensaver/questions", headers=self.headers, json={"flow": {"seconds": 45}})
        payload = self.client.get("/api/screensaver/questions").get_json()
        self.assertEqual(len(payload["prompts"]), 2)
        self.assertEqual(payload["sets"][0]["id"], "tagesreflexion")

    def test_the_chosen_model_overrides_the_configured_one(self):
        handle = mock.MagicMock(user_id="user-1")
        local = {"id": "lm_mac", "model": "", "api_url": "http://10.0.0.2:8080/v1/chat/completions"}
        with mock.patch.object(questions, "local_providers", return_value=[local]), \
             mock.patch.object(main, "_call_ai_api", return_value='{"questions": [{"text": "Neu?"}]}') as call:
            result = questions.generate_handler(handle, {"count": 3, "model": "gemma-3-12b"})
        self.assertEqual(call.call_args.args[0]["model"], "gemma-3-12b")
        self.assertEqual(result["model"], "gemma-3-12b")

    def test_models_are_listed_only_for_local_providers(self):
        import journal_analysis_providers as providers

        local = {"id": "lm_mac", "model": "", "api_url": "http://10.0.0.2:8080/v1/chat/completions"}
        with mock.patch.object(questions, "local_providers", return_value=[local]), \
             mock.patch.object(providers, "discover_models", return_value=["a", "b"]):
            ok = self.client.get("/api/screensaver/questions/providers/lm_mac/models")
            unknown = self.client.get("/api/screensaver/questions/providers/openai/models")
        self.assertEqual(ok.get_json()["models"], ["a", "b"])
        self.assertEqual(unknown.status_code, 404)

    def test_the_mode_is_a_valid_screensaver_setting(self):
        self.client.post("/api/screensaver/settings", headers=self.headers, json={"mode": "questions"})
        settings = self.client.get("/api/screensaver/settings").get_json()["settings"]
        self.assertEqual(settings["mode"], "questions")



class ScreensaverQuestionFrontendTests(unittest.TestCase):
    def test_both_shells_load_runtime_and_settings_after_the_screensaver(self):
        for name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertIn("{{ screensaver_scripts() }}", template, name)
        macro = (ROOT / "app" / "templates" / "_screensaver.html").read_text(encoding="utf-8")
        order = [macro.index(f"filename='{asset}'") for asset in (
            "screensaver.js", "screensaver-settings.js", "screensaver-folders.js", "screensaver-questions.js",
            "screensaver-question-sets.js")]
        self.assertEqual(order, sorted(order))

    def test_the_screensaver_hands_the_mode_to_the_question_module(self):
        source = (ROOT / "app" / "static" / "screensaver.js").read_text(encoding="utf-8")
        self.assertIn("window.journlScreensaverQuestions?.start(", source)
        self.assertIn("window.journlScreensaverQuestions?.handleKey(event)", source)
        self.assertIn("questions: 'Fragen'", source)

    def test_sessions_write_answers_into_the_field_and_honour_the_timeout_rule(self):
        source = (ROOT / "app" / "static" / "screensaver-questions.js").read_text(encoding="utf-8")
        for snippet in ("flow.on_timeout === 'skip'", "flow.on_timeout === 'wait'", "question.required",
                        "appendBlock(`**${current.text}**", "/^[1-9]$/", "data-q-slider"):
            self.assertIn(snippet, source)

    def test_the_slider_ends_with_a_manual_step_without_countdown(self):
        for name in ("screensaver-questions.js", "screensaver-question-sets.js"):
            source = (ROOT / "app" / "static" / name).read_text(encoding="utf-8")
            self.assertIn("600, 900, 1800, 0];", source, name)
            self.assertIn("'Manuell'", source, name)
        runtime = (ROOT / "app" / "static" / "screensaver-questions.js").read_text(encoding="utf-8")
        self.assertIn("if (flow.flow === 'manual' || !flow.seconds) return 0;", runtime)
        self.assertIn("STEPS[Number(el.slider.value)] ?? flow.seconds", runtime)

    def test_back_and_loop_only_in_free_and_automatic(self):
        source = (ROOT / "app" / "static" / "screensaver-questions.js").read_text(encoding="utf-8")
        for snippet in ("function canGoBack() { return flow.flow === 'manual' || flow.flow === 'auto'; }",
                        "function loops() { return Boolean(flow.loop) && canGoBack(); }",
                        "if (!ctx || !flow || !canGoBack() || !queue.length) return;",
                        "if (!loops()) { finish(); return; }",
                        "event.altKey && event.key === 'ArrowLeft'",
                        "if (value.endsWith(previous.text)) setFieldValue("):
            self.assertIn(snippet, source)
        settings = (ROOT / "app" / "static" / "screensaver-question-sets.js").read_text(encoding="utf-8")
        self.assertIn('data-flow-field="loop"', settings)

    def test_every_flow_ends_after_the_last_question(self):
        """Frueher begann alles ausser der Sequenz von vorn; jetzt nur noch mit Loop."""
        source = (ROOT / "app" / "static" / "screensaver-questions.js").read_text(encoding="utf-8")
        self.assertIn("if (queue.length && position >= queue.length) {\n      if (!loops()) { finish(); return; }", source)

    def test_settings_generate_through_a_background_run(self):
        source = (ROOT / "app" / "static" / "screensaver-question-sets.js").read_text(encoding="utf-8")
        self.assertIn("${ENDPOINT}/generate", source)
        self.assertIn("/api/runs/", source)
        self.assertIn("settings-screensaver-questions", source)

    def test_the_generator_offers_a_model_and_saved_prompts(self):
        source = (ROOT / "app" / "static" / "screensaver-question-sets.js").read_text(encoding="utf-8")
        for snippet in ("${ENDPOINT}/providers/${encodeURIComponent(provider)}/models",
                        "model: body.querySelector('[data-gen=\"model\"]').value",
                        "JSON.stringify({ prompts })", "data-gen-prompt-save", "data-gen-prompt-delete"):
            self.assertIn(snippet, source)


if __name__ == "__main__":
    unittest.main()
