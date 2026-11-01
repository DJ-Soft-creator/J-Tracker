"""Der Whisper-Sidecar muss eine zweite Transkription ersetzen, nicht anhaengen.

Genau daran haengt „mit einem groesseren Modell noch einmal versuchen": ohne
Ersetzen stuenden zwei Transkript-Bloecke im selben Eintrag.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
_spec = importlib.util.spec_from_file_location(
    "whisper_scheduler", ROOT / "scripts" / "whisper-scheduler.py")
whisper_scheduler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(whisper_scheduler)

import transcript_blocks  # noqa: E402

MEDIA_ID = "e4f71c48-e5d3-4797-ab39-6fd64cef113e"
OTHER_ID = "11111111-2222-3333-4444-555555555555"
MARKER = '<!-- jt:media {"id":"%s","type":"audio"} -->' % MEDIA_ID
LABEL = "Sprachnachricht | Aufnahmezeit: 2026-08-24T08:34:36+00:00"


class ApplyToJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patch = mock.patch.object(whisper_scheduler, "DATA_DIR", self.data)
        self.patch.start()
        self.journal = self.data / "u1" / "2026" / "08" / "24" / "Journal_2026-08-24.md"
        self.journal.parent.mkdir(parents=True)
        self.item = {
            "id": MEDIA_ID, "user_id": "u1",
            "journal_path": "2026/08/24/Journal_2026-08-24.md",
        }

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def apply(self, text):
        whisper_scheduler._apply_to_journal("u1", self.item, {"text": text})
        return self.journal.read_text(encoding="utf-8")

    def test_star_microphone_recordings_are_transcribed_by_the_macmini(self):
        """Der regelmaessige Lauf nimmt sie mit - aber ueber den MacMini, nicht Whisper."""
        import json
        folder = self.data / "u1" / "2026" / "08" / "24" / "media" / "Sprachi"
        folder.mkdir(parents=True)
        for media_id, engine in ((MEDIA_ID, None), (OTHER_ID, "voice")):
            (folder / f"a_{media_id}.wav").write_bytes(b"RIFF")
            item = {"id": media_id, "user_id": "u1", "type": "audio", "status": "ready",
                    "transcription_status": "pending", "original_name": f"a_{media_id}.wav"}
            if engine:
                item["engine"] = engine
            (folder / f"a_{media_id}_metadata.json").write_text(json.dumps(item), encoding="utf-8")
        with mock.patch.object(whisper_scheduler, "_call_whisper", return_value={"text": "whisper"}) as whisper, \
             mock.patch.object(whisper_scheduler, "_call_voice", return_value={"text": "macmini"}) as voice:
            report = whisper_scheduler.run()
        self.assertEqual(report["completed"], 2)
        self.assertEqual(whisper.call_count, 1)
        self.assertEqual(voice.call_count, 1)
        stored = json.loads((folder / f"a_{OTHER_ID}_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["transcript_text"], "macmini")

    def test_the_macmini_call_resolves_the_local_provider_from_the_raw_config(self):
        import os
        import voice
        config = {"ai_providers": [{"id": "lm_mac", "api_url": "http://{locale_LLM_IP}:8080/v1/chat/completions"}],
                  "voice": {"provider_id": "lm_mac", "asr_model": "voice-t"}}
        with mock.patch.object(voice, "_raw_config", return_value=config), \
             mock.patch.dict(os.environ, {"locale_LLM_IP": "192.168.1.20"}), \
             mock.patch.object(voice, "transcribe", return_value="Hallo") as transcribe:
            result = whisper_scheduler._call_voice(Path("/tmp/a.wav"))
        self.assertEqual(result["text"], "Hallo")
        self.assertEqual(transcribe.call_args.args[1], "http://192.168.1.20:8080/v1")
        self.assertEqual(transcribe.call_args.args[2], "voice-t")

    def test_a_model_list_in_whisper_model_means_its_first_entry(self):
        """PROD 01.10.2026: WHISPER_MODEL=tiny,base,small,medium liess jeden Lauf scheitern."""
        import ast
        import os
        with mock.patch.dict(os.environ, {"WHISPER_MODEL": "tiny,base,small,medium"}):
            self.assertEqual(whisper_scheduler._default_model(), "tiny")
        with mock.patch.dict(os.environ, {"WHISPER_MODEL": " , small"}):
            self.assertEqual(whisper_scheduler._default_model(), "small")
        # Der Dienst selbst (eigenes Image, faster-whisper hier nicht installiert):
        source = (ROOT / "whisper_service" / "app.py").read_text(encoding="utf-8")
        node = next(item for item in ast.parse(source).body
                    if isinstance(item, ast.FunctionDef) and item.name == "_single_model")
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
        self.assertEqual(namespace["_single_model"]("tiny,base,small,medium"), "tiny")
        self.assertEqual(namespace["_single_model"]("medium"), "medium")
        self.assertEqual(namespace["_single_model"](""), "base")
        self.assertIn('MODEL_NAME = _single_model(os.environ.get("WHISPER_MODEL", "base"))', source)

    def test_an_unreachable_service_stops_the_run_without_marking_errors(self):
        import json
        folder = self.data / "u1" / "2026" / "08" / "24" / "media" / "Sprachi"
        folder.mkdir(parents=True)
        for media_id in (MEDIA_ID, OTHER_ID):
            (folder / f"a_{media_id}.webm").write_bytes(b"x")
            (folder / f"a_{media_id}_metadata.json").write_text(json.dumps({
                "id": media_id, "user_id": "u1", "type": "audio", "status": "ready",
                "transcription_status": "error", "original_name": f"a_{media_id}.webm"}), encoding="utf-8")
        with mock.patch.object(whisper_scheduler, "_call_whisper",
                               side_effect=whisper_scheduler.ServiceUnavailable("Connection refused")) as call:
            report = whisper_scheduler.run()
        self.assertEqual(report, {"completed": 0, "errors": 0, "unavailable": True})
        self.assertEqual(call.call_count, 1)  # nach dem ersten Versuch Schluss
        for media_id in (MEDIA_ID, OTHER_ID):
            stored = json.loads((folder / f"a_{media_id}_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["transcription_status"], "error")  # unveraendert

    def test_first_run_inserts_behind_the_media_label(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, "", "~~24.08. 10:35:02~~", ""]), encoding="utf-8")
        updated = self.apply("Erste Fassung.")
        lines = updated.split("\n")
        self.assertLess(lines.index(LABEL), lines.index(transcript_blocks.transcript_marker(MEDIA_ID)))
        self.assertIn("Erste Fassung.", updated)

    def test_second_run_replaces_instead_of_adding_a_block(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, "", "~~24.08. 10:35:02~~", ""]), encoding="utf-8")
        self.apply("Schwaches Modell.")
        updated = self.apply("Starkes Modell versteht mehr.")
        self.assertEqual(updated.count(transcript_blocks.transcript_marker(MEDIA_ID)), 1)
        self.assertEqual(updated.count("### Transkript"), 1)
        self.assertNotIn("Schwaches Modell.", updated)
        self.assertIn("Starkes Modell versteht mehr.", updated)

    def test_an_empty_first_result_can_be_filled_later(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, ""]), encoding="utf-8")
        self.apply("")
        updated = self.apply("Beim zweiten Versuch verstanden.")
        self.assertEqual(updated.count("### Transkript"), 1)
        self.assertIn("Beim zweiten Versuch verstanden.", updated)

    def test_every_rewrite_keeps_a_backup(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, ""]), encoding="utf-8")
        self.apply("Erste Fassung.")
        self.apply("Zweite Fassung.")
        self.assertEqual(len(list((self.journal.parent / "_Backup").glob("*.bak"))), 2)

    def test_an_unchanged_transcript_writes_no_backup(self):
        self.journal.write_text("\n".join(["- 10:34:36", "", MARKER, "", LABEL, ""]), encoding="utf-8")
        self.apply("Gleich.")
        self.apply("Gleich.")
        self.assertEqual(len(list((self.journal.parent / "_Backup").glob("*.bak"))), 1)

    def test_a_journal_without_the_marker_is_left_alone(self):
        self.journal.write_text("- 10:34:36\n\nNur Text.\n", encoding="utf-8")
        self.assertEqual(self.apply("Egal."), "- 10:34:36\n\nNur Text.\n")


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patch = mock.patch.object(whisper_scheduler, "DATA_DIR", self.data)
        self.patch.start()
        self.directory = self.data / "u1" / "2026" / "08" / "24" / "media" / "Sprachi"
        self.directory.mkdir(parents=True)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def sidecar(self, media_id, status):
        path = self.directory / f"2026-08-24_10-00-00_{media_id}_metadata.json"
        path.write_text(json.dumps({
            "id": media_id, "user_id": "u1", "type": "audio", "status": "ready",
            "transcription_status": status,
        }), encoding="utf-8")

    def ids(self, **kwargs):
        return sorted(item["id"] for _, item in whisper_scheduler.pending(**kwargs))

    def test_the_daily_run_skips_finished_recordings(self):
        self.sidecar(MEDIA_ID, "completed")
        self.sidecar(OTHER_ID, "error")
        self.assertEqual(self.ids(), [OTHER_ID])

    def test_a_requested_recording_is_redone_although_it_is_finished(self):
        self.sidecar(MEDIA_ID, "completed")
        self.sidecar(OTHER_ID, "error")
        self.assertEqual(self.ids(media_filter=MEDIA_ID), [MEDIA_ID])

    def test_a_foreign_user_is_never_included(self):
        self.sidecar(MEDIA_ID, "pending")
        self.assertEqual(self.ids(user_filter="u2"), [])


class ManualTriggerTests(unittest.TestCase):
    def test_a_valid_media_id_limits_the_run(self):
        self.assertEqual(whisper_scheduler._requested_media({"media_id": MEDIA_ID}), MEDIA_ID)

    def test_a_bogus_media_id_widens_to_the_normal_run(self):
        self.assertIsNone(whisper_scheduler._requested_media({"media_id": "../../etc/passwd"}))
        self.assertIsNone(whisper_scheduler._requested_media({}))


if __name__ == "__main__":
    unittest.main()
