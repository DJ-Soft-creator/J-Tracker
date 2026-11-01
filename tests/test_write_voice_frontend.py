"""Verdrahtung der erweiterten Aufnahme und des wiederverwendbaren Vorlesens."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WriteVoiceFrontendTests(unittest.TestCase):
    def test_both_shells_load_the_module_after_write_session(self):
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn("filename='write-voice.js'", template, template_name)
            self.assertLess(
                template.index("filename='write-session.js'"),
                template.index("filename='write-voice.js'"),
                template_name,
            )
            # Vorlesen haengt sich an journlVoice an; „Probe hoeren“ braucht journlSpeak.
            self.assertLess(template.index("filename='write-voice.js'"),
                            template.index("filename='write-speech.js'"), template_name)
            self.assertLess(template.index("filename='write-speech.js'"),
                            template.index("filename='tts-settings.js'"), template_name)

    def test_module_uses_the_voice_endpoints(self):
        source = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        for endpoint in ("/api/voice/settings", "/api/voice/transcribe", "/api/voice/polish",
                         "/api/voice/name"):
            self.assertIn(endpoint, source)
        speech = (ROOT / "app" / "static" / "write-speech.js").read_text(encoding="utf-8")
        self.assertIn("/api/voice/speak", speech)

    def test_module_exposes_reusable_speech(self):
        source = (ROOT / "app" / "static" / "write-speech.js").read_text(encoding="utf-8")
        self.assertIn("window.journlSpeak", source)
        self.assertIn("window.journlVoice", source)

    def test_recording_produces_wav_for_the_asr(self):
        """Der MacMini-ASR akzeptiert kein webm/m4a; die Aufnahme kodiert WAV."""
        source = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        self.assertIn("encodeWav", source)
        self.assertIn("createScriptProcessor", source)
        self.assertNotIn("MediaRecorder", source)

    def test_write_session_exposes_the_helpers_used_here(self):
        source = (ROOT / "app" / "static" / "write-session.js").read_text(encoding="utf-8")
        for helper in ("attachAudioBlob", "insertText", "replaceText", "mediaUpdated", "media:"):
            self.assertIn(helper, source)


    def test_panel_text_can_be_dismissed_and_shows_the_original(self):
        """Nach der Nachbearbeitung bleibt der Rohtext lesbar; ins Feld kommt nur der verbesserte."""
        source = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        self.assertIn("data-voice-preview-dismiss", source)
        self.assertIn("data-voice-preview-original", source)
        self.assertIn("setResult(text, raw)", source)
        self.assertIn("writeSession.insertText(text)", source)
        self.assertNotIn("writeSession.insertText(raw)", source)

    def test_naming_task_is_configurable_in_the_panel(self):
        source = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        self.assertIn("data-voice-naming-prompt", source)
        self.assertIn("name_prompt", source)
        self.assertIn("namingOn()", source)

    def test_reading_aloud_is_its_own_menu_entry(self):
        source = (ROOT / "app" / "static" / "write-speech.js").read_text(encoding="utf-8")
        self.assertIn("readEntry.setAttribute('data-voice-read', '')", source)
        self.assertIn("insertAdjacentElement('afterend', readEntry)", source)
        self.assertNotIn('<button type="button" data-voice-read', source)

    def test_tts_is_configured_in_the_write_settings(self):
        """Anbieter/Modell/Stimme fuers Vorlesen stehen unter Burger → Schreiben."""
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('<div id="settings-tts"></div>', template, template_name)
            self.assertIn("filename='tts-settings.js'", template, template_name)
        settings = (ROOT / "app" / "static" / "tts-settings.js").read_text(encoding="utf-8")
        self.assertIn("event.detail?.group !== 'write'", settings)
        for key in ("tts_provider_id", "tts_model", "tts_voice", "/api/voice/models"):
            self.assertIn(key, settings)
        voice = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        # Das Aufnahme-Panel schickt die Vorlese-Felder nicht mehr mit.
        self.assertNotIn("tts_model", voice)
        self.assertNotIn("data-voice-tts", voice)

    def test_speech_plays_in_a_visible_player(self):
        """Safari gibt Audio nach dem TTS-Abruf nicht mehr automatisch frei;
        der Player bleibt stehen, damit ein Tipp auf Play immer reicht."""
        voice = (ROOT / "app" / "static" / "write-speech.js").read_text(encoding="utf-8")
        speak = voice[voice.index("async function speak(text, options)"):voice.index("// Vorlesen ist ein eigener Eintrag")]
        self.assertIn("<audio data-player-audio controls", voice)
        self.assertIn("playerAudio.src = playerUrl", speak)
        self.assertIn("Zum Abspielen auf Play tippen", speak)
        self.assertNotIn("AudioContext", speak)

    def test_submit_waits_for_the_extended_recording(self):
        source = (ROOT / "app" / "static" / "write-session.js").read_text(encoding="utf-8")
        self.assertIn("window.journlVoice?.busyReason?.()", source)

    def test_a_stored_recording_can_be_sent_to_the_macmini(self):
        strip = (ROOT / "app" / "static" / "write-media-strip.js").read_text(encoding="utf-8")
        voice = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        self.assertIn("data-transcribe-voice", strip)
        # Nur Aufnahmen des Sternchen-Mikrofons gehen an den MacMini.
        self.assertIn("item.engine === 'voice'", strip)
        self.assertIn("'audio/wav', new Date(job.startedAt).toISOString(), 'voice')", voice)
        self.assertIn("chip(RECORD_ONLY, 'Nur Aufnahme')", voice)
        self.assertIn("form.append('engine', 'voice')", voice)
        self.assertIn("OfflineAudioContext", voice)

    def test_preview_stays_in_the_panel_until_the_final_text(self):
        voice = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        tick = voice[voice.index("async function previewTick()"):voice.index("async function startRecording()")]
        self.assertNotIn("insertText", tick)
        self.assertNotIn("replaceText", tick)

    def test_preset_text_reaches_the_field_only_after_polishing(self):
        voice = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        job = voice[voice.index("async function runJob(job, blob)"):voice.index("recordButton.addEventListener('click'")]
        self.assertNotIn("replaceText", job)
        self.assertEqual(job.count("writeSession.insertText("), 1)
        self.assertLess(job.index("/api/voice/polish"), job.index("writeSession.insertText("))

    def test_next_recording_can_start_while_a_job_is_processing(self):
        voice = (ROOT / "app" / "static" / "write-voice.js").read_text(encoding="utf-8")
        # Stoppen wartet nicht auf die Verarbeitung; Jobs fuegen in Reihenfolge ein.
        self.assertIn("if (recording) { stopRecording(); return; }", voice)
        self.assertIn("await job.turn;", voice)
        self.assertIn("data-voice-busy", voice)
        self.assertIn("data-voice-jobs", voice)


if __name__ == "__main__":
    unittest.main()
