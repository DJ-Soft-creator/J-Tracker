"""Sprache: Konfiguration, Anbieterwahl, ASR/TTS-Aufrufe und HTTP-Hülle."""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "voice-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-voice-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import voice
    import voice_routes


def _config():
    return {
        "templates": [{"id": "schnell"}],
        "ai_providers": [
            {"id": "lm", "label": "MacMini", "api_url": "http://192.168.1.50:8080/v1/chat/completions"},
            {"id": "cloud", "label": "Cloud", "api_url": "https://openrouter.ai/api/v1/chat/completions"},
        ],
    }


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class VoiceConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        self.path.write_text(json.dumps(_config()), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_when_section_is_missing(self):
        settings = voice.load_settings(self.path)
        self.assertEqual(settings["asr_model"], voice.DEFAULT_ASR_MODEL)
        self.assertEqual(settings["tts_model"], voice.DEFAULT_TTS_MODEL)
        self.assertEqual([p["id"] for p in settings["presets"]],
                         [p["id"] for p in voice.DEFAULT_PRESETS])

    def test_save_replaces_only_the_voice_key(self):
        settings = voice.save_settings(self.path, {
            "provider_id": "lm", "asr_model": "asr-a", "polish_model": "chat-x",
            "tts_model": "tts-t", "tts_voice": "alloy",
            "presets": [{"id": "kurz", "label": "Kurz", "prompt": "Straffe den Text."}],
        })
        self.assertEqual(settings["polish_model"], "chat-x")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["templates"], [{"id": "schnell"}])
        self.assertEqual(stored["ai_providers"][1]["id"], "cloud")
        self.assertEqual(stored["voice"]["presets"][0]["id"], "kurz")

    def test_a_preset_without_prompt_is_rejected(self):
        with self.assertRaises(voice.VoiceError):
            voice.save_settings(self.path, {
                "presets": [{"id": "leer", "label": "Leer", "prompt": "  "}],
            })

    def test_provider_base_url_strips_chat_suffix(self):
        self.assertEqual(
            voice.provider_base_url("http://192.168.1.50:8080/v1/chat/completions"),
            "http://192.168.1.50:8080/v1",
        )

    def test_only_local_providers_are_offered(self):
        providers = voice.local_providers(_config())
        self.assertEqual([p["id"] for p in providers], ["lm"])
        self.assertIsNone(voice.resolve_provider(_config(), "cloud"))
        self.assertEqual(voice.resolve_provider(_config(), "lm")["id"], "lm")


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class VoiceHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        self.path.write_text(json.dumps(_config()), encoding="utf-8")
        self.previous_path = main.CONFIG_PATH
        main.CONFIG_PATH = self.path
        main.app.config.update(TESTING=True)
        self.client = main.app.test_client()
        self.previous_user = main._current_user
        self.user = {"id": "user-1", "username": "david", "admin": True}
        main._current_user = lambda: self.user
        self.previous_csrf = main.csrf_protect
        main.csrf_protect = lambda f: (lambda *a, **k: None)

    def tearDown(self):
        main.CONFIG_PATH = self.previous_path
        main._current_user = self.previous_user
        main.csrf_protect = self.previous_csrf
        self.temp.cleanup()

    def test_settings_endpoint_requires_a_user(self):
        main._current_user = lambda: None
        self.assertEqual(self.client.get("/api/voice/settings").status_code, 401)

    def test_only_admins_may_write_settings(self):
        main._current_user = lambda: {**self.user, "admin": False}
        response = self.client.put("/api/voice/settings", json={"asr_model": "x"})
        self.assertEqual(response.status_code, 403)

    def test_saving_settings_keeps_the_default_presets(self):
        response = self.client.put("/api/voice/settings", json={"provider_id": "lm"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["settings"]["provider_id"], "lm")
        self.assertTrue(response.get_json()["settings"]["presets"])

    def test_transcribe_returns_the_text(self):
        with mock.patch.object(voice, "transcribe", return_value="Hallo Welt") as call:
            response = self.client.post(
                "/api/voice/transcribe",
                data={"model": "asr-a", "file": (io.BytesIO(b"RIFFdata"), "a.wav")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["text"], "Hallo Welt")
        self.assertEqual(call.call_args.args[2], "asr-a")

    def test_transcribe_sends_the_uploaded_wav_to_the_local_endpoint(self):
        """Echter Pfad bis zum HTTP-Aufruf: die Route reicht einen Temp-Dateinamen
        weiter, voice.transcribe muss ihn lesen koennen (frueher: 500)."""
        sent = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(request, timeout=None):
            sent["url"] = request.full_url
            sent["body"] = request.data
            return Response(json.dumps({"text": "Hallo MacMini"}).encode())

        opener = mock.MagicMock()
        opener.open.side_effect = fake_open
        with mock.patch.object(voice, "_opener", return_value=opener):
            response = self.client.post(
                "/api/voice/transcribe",
                data={"model": "asr-a", "file": (io.BytesIO(b"RIFFwavdata"), "a.wav")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["text"], "Hallo MacMini")
        self.assertTrue(sent["url"].endswith("/audio/transcriptions"))
        self.assertIn(b"RIFFwavdata", sent["body"])

    def test_transcribe_error_names_model_and_endpoint_answer(self):
        """Ein falsches Modell (etwa „voice-t“) muss in der Meldung erkennbar sein."""
        import urllib.error
        error = urllib.error.HTTPError(
            "http://mac/v1/audio/transcriptions", 400, "Bad Request", {},
            io.BytesIO(json.dumps({"error": {"message": "model voice-t is not an ASR model"}}).encode()),
        )
        opener = mock.MagicMock()
        opener.open.side_effect = error
        with mock.patch.object(voice, "_opener", return_value=opener):
            response = self.client.post(
                "/api/voice/transcribe",
                data={"model": "voice-t", "file": (io.BytesIO(b"RIFFwavdata"), "a.wav")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 502)
        message = response.get_json()["error"]
        self.assertIn("voice-t", message)
        self.assertIn("HTTP 400", message)
        self.assertIn("not an ASR model", message)

    def test_speak_returns_audio_bytes(self):
        with mock.patch.object(voice, "synthesize", return_value=(b"RIFF", "audio/wav")):
            response = self.client.post("/api/voice/speak", json={"text": "Hallo"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"RIFF")
        self.assertEqual(response.headers["Content-Type"], "audio/wav")

    def test_speak_uses_its_own_provider_from_the_settings(self):
        """Burger → Schreiben → Vorlesen waehlt Anbieter und Modell getrennt von der Aufnahme."""
        config = _config()
        config["ai_providers"].append(
            {"id": "tts", "label": "TTS-Box", "api_url": "http://192.168.1.60:9000/v1/chat/completions"})
        self.path.write_text(json.dumps(config), encoding="utf-8")
        response = self.client.put("/api/voice/settings", json={
            "provider_id": "lm", "tts_provider_id": "tts", "tts_model": "kokoro", "tts_voice": "af_sky",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["settings"]["tts_provider_id"], "tts")
        with mock.patch.object(voice, "synthesize", return_value=(b"RIFF", "audio/wav")) as call:
            response = self.client.post("/api/voice/speak", json={"text": "Hallo"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(call.call_args.args[1:], ("http://192.168.1.60:9000/v1", "kokoro", "af_sky"))

    def test_synthesize_requests_wav(self):
        """Ohne response_format lieferte tts-David auf dem MacMini leeres MP3."""
        sent = {}

        class Response(io.BytesIO):
            headers = {"Content-Type": "audio/wav"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(request, timeout=None):
            sent["body"] = json.loads(request.data)
            return Response(b"RIFFaudio")

        opener = mock.MagicMock()
        opener.open.side_effect = fake_open
        with mock.patch.object(voice, "_opener", return_value=opener):
            audio, content_type = voice.synthesize("Hallo", "http://mac/v1", "tts-David", "alloy")
        self.assertEqual(sent["body"]["response_format"], "wav")
        self.assertEqual((audio, content_type), (b"RIFFaudio", "audio/wav"))

    def test_csp_lets_the_player_play_blob_audio(self):
        """Der Vorlese-Player spielt eine blob:-URL; default-src 'self' allein blockte sie."""
        with mock.patch.object(voice, "synthesize", return_value=(b"RIFF", "audio/wav")):
            response = self.client.post("/api/voice/speak", json={"text": "Hallo"})
        self.assertIn("media-src 'self' blob:", response.headers["Content-Security-Policy"])

    def test_speak_falls_back_to_the_recording_provider(self):
        self.client.put("/api/voice/settings", json={"provider_id": "lm"})
        with mock.patch.object(voice, "synthesize", return_value=(b"RIFF", "audio/wav")) as call:
            self.client.post("/api/voice/speak", json={"text": "Hallo"})
        self.assertEqual(call.call_args.args[1], "http://192.168.1.50:8080/v1")

    def test_saving_recording_settings_keeps_the_tts_choice(self):
        self.client.put("/api/voice/settings", json={"tts_model": "kokoro", "tts_voice": "af_sky"})
        response = self.client.put("/api/voice/settings", json={"asr_model": "asr-b"})
        settings = response.get_json()["settings"]
        self.assertEqual((settings["tts_model"], settings["tts_voice"]), ("kokoro", "af_sky"))

    def test_polish_uses_the_selected_preset(self):
        def fake_call(provider, ai_function, text):
            self.assertTrue(ai_function["system_prompt"].strip())
            self.assertEqual(text, "roh")
            return "Gefeilt"
        with mock.patch.object(main, "_call_ai_api", side_effect=fake_call):
            response = self.client.post("/api/voice/polish", json={
                "text": "roh", "preset_id": "feinschliff",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["text"], "Gefeilt")

    def test_polish_caps_thinking_and_scales_the_token_limit(self):
        """Ein Denk-Modell verbrauchte bei langem Diktat alle 4000 Tokens fuers Nachdenken."""
        import voice_routes

        budgets = []

        def fake_call(provider, ai_function, text):
            budgets.append((ai_function["thinking_budget"], ai_function["max_tokens"]))
            return "Gefeilt"
        with mock.patch.object(main, "_call_ai_api", side_effect=fake_call):
            for text in ("roh", "x" * 20000):
                self.client.post("/api/voice/polish", json={"text": text, "preset_id": "feinschliff"})
        budget = voice_routes.VOICE_THINKING_BUDGET
        self.assertEqual(budgets[0], (budget, voice_routes.POLISH_MAX_TOKENS))
        self.assertEqual(budgets[1], (budget, budget + 10000))

    def test_polish_rejects_an_unknown_preset(self):
        response = self.client.post("/api/voice/polish", json={"text": "roh", "preset_id": "nope"})
        self.assertEqual(response.status_code, 404)

    def test_name_returns_title_and_safe_hashtags(self):
        """Workflow-/Ziel-Hashtags loesen Aktionen aus und duerfen nie im Titel landen."""
        import tagging

        catalogs = {
            False: {**tagging._catalog_default(), "canonical": ["urlaub", "familie"],
                    "aliases": {"ferien": "urlaub"}, "ai_workflows": {"agent": {}},
                    "blocked": ["egal"]},
            True: {**tagging._catalog_default(), "write_targets": {"einkauf": {"scope": "family"}}},
        }
        prompts = []

        def fake_call(provider, ai_function, text):
            prompts.append(ai_function["system_prompt"])
            return 'Klar: ```json\n{"title": "Urlaub an der Ostsee planen", "hashtags": ["#Ferien", "agent", "einkauf", "egal", "ostsee"]}\n```'
        with mock.patch.object(tagging, "read_catalog", side_effect=lambda user_id=None, family=False: catalogs[family]), \
             mock.patch.object(main, "_call_ai_api", side_effect=fake_call):
            response = self.client.post("/api/voice/name", json={"text": "Wir wollen an die Ostsee"})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {"title": "Urlaub an der Ostsee planen", "hashtags": ["urlaub", "ostsee"]})
        self.assertIn("familie", prompts[0])
        self.assertNotIn("agent", prompts[0])

    def test_name_uses_the_task_from_the_settings(self):
        """Die Aufgabe ist einstellbar, das JSON-Format haengt Journl immer an."""
        settings = self.client.put("/api/voice/settings", json={"name_prompt": "Nur Orte als Hashtags."}).get_json()["settings"]
        self.assertTrue(settings["name_enabled"])
        prompts = []

        def fake_call(provider, ai_function, text):
            prompts.append(ai_function)
            return '{"title": "Ostsee", "hashtags": ["ostsee"]}'
        with mock.patch.object(main, "_call_ai_api", side_effect=fake_call):
            response = self.client.post("/api/voice/name", json={"text": "Wir fahren an die Ostsee"})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn("Nur Orte als Hashtags.", prompts[0]["system_prompt"])
        self.assertIn('"hashtags"', prompts[0]["system_prompt"])
        # Denk-Modelle brauchen ~600 Tokens vor der Antwort; 200 lieferten nie Inhalt.
        self.assertGreaterEqual(prompts[0]["max_tokens"], 1000)

    def test_empty_naming_task_falls_back_to_the_default(self):
        settings = self.client.put("/api/voice/settings", json={"name_prompt": "  ", "name_enabled": False}).get_json()["settings"]
        self.assertEqual(settings["name_prompt"], voice.DEFAULT_NAME_TASK)
        self.assertFalse(settings["name_enabled"])

    def test_name_requires_text(self):
        self.assertEqual(self.client.post("/api/voice/name", json={"text": " "}).status_code, 400)


class VoiceNameParsingTests(unittest.TestCase):
    """Ohne Flask lauffaehig: das Parsen der Modellantwort."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "app"))
        import voice as voice_module
        self.voice = voice_module

    def test_plain_line_without_json_becomes_the_title(self):
        title, tags = self.voice.parse_name("Arzttermin verschoben #gesundheit", lambda tag: tag.lstrip("#").lower())
        self.assertEqual((title, tags), ("Arzttermin verschoben", ["gesundheit"]))

    def test_long_titles_are_cut_at_a_word_and_tags_capped(self):
        answer = json.dumps({"title": "Wort " * 30, "hashtags": ["a", "b", "c", "d"]})
        title, tags = self.voice.parse_name(answer, lambda tag: tag)
        self.assertLessEqual(len(title), 60)
        self.assertFalse(title.endswith(" "))
        self.assertEqual(tags, ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
