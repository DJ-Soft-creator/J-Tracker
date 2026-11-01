"""Player des Bildschirmschoners: Quellen, Auslieferung, Stand, Presets, Uploads."""

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "screensaver-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-screensaver-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import screensaver
    import screensaver_player

# Kein echtes MP3 noetig: Auslieferung und Range pruefen nur Bytes und Endung.
AUDIO = bytes(range(256)) * 8
# Fuer die Tags schon: zehn leere MPEG-1-Layer-3-Frames (128 kbit/s, 44,1 kHz).
MP3 = (b"\xff\xfb\x90\x64" + bytes(413)) * 10
PNG = b"\x89PNG\r\n\x1a\n" + bytes(32)


def screensaver_tags_available():
    return FLASK_AVAILABLE and importlib.util.find_spec("mutagen") is not None


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class ScreensaverPlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.previous = main.DATA_DIR
        main.DATA_DIR = self.data
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        self.user = {"id": "user-1", "username": "david"}
        self.original_current_user = main._current_user
        self.original_update_user = main.update_user
        main._current_user = lambda: self.user
        main.update_user = lambda user: self.user.update(user)
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.headers = {"X-CSRF-Token": "token"}

    def tearDown(self):
        main.DATA_DIR = self.previous
        main._current_user = self.original_current_user
        main.update_user = self.original_update_user
        self.temp.cleanup()

    def _private(self, name, *files):
        directory = self.data / "user-1" / "screensaver" / "ordner" / name
        directory.mkdir(parents=True, exist_ok=True)
        for file in files:
            (directory / file).parent.mkdir(parents=True, exist_ok=True)
            (directory / file).write_bytes(AUDIO)
        return directory

    def _shared(self, name, *files):
        directory = self.data / "screensaver" / name
        directory.mkdir(parents=True, exist_ok=True)
        for file in files:
            (directory / file).write_bytes(AUDIO)
        return directory

    def _settings(self, **values):
        response = self.client.post("/api/screensaver/player/settings", headers=self.headers, json=values)
        self.assertEqual(response.status_code, 200)
        return response.get_json()["settings"]

    def _tracks(self):
        return self.client.get("/api/screensaver/player/tracks").get_json()

    # ─── Einstellungen ──────────────────────────────────────────────
    def test_settings_default_to_a_silent_minimal_player(self):
        settings = self.client.get("/api/screensaver/player/settings").get_json()["settings"]
        self.assertEqual(settings["sources"], [])
        self.assertEqual(settings["visibility"], "minimal")
        self.assertEqual(settings["display"], "everywhere")
        self.assertEqual(settings["shortcuts"]["play_pause"], "Ctrl+Alt+Space")

    def test_settings_are_clamped_to_the_whitelist(self):
        settings = self._settings(
            sources=["user:Musik", "theme:landscapes", "journal", "user:../x", "user:Musik"],
            repeat="forever", visibility="hidden", crossfade=99, volume=-5, skip_back=7, skip_forward=45,
            duck_level=250, display="write", shuffle="yes", unknown=1,
        )
        self.assertEqual(settings["sources"], ["user:Musik"])
        self.assertEqual(settings["repeat"], "folder")
        self.assertEqual(settings["visibility"], "hidden")
        self.assertEqual(settings["crossfade"], screensaver_player.CROSSFADE_MAX)
        self.assertEqual(settings["volume"], 0)
        self.assertEqual(settings["skip_back"], 10)
        self.assertEqual(settings["skip_forward"], 45)
        self.assertEqual(settings["duck_level"], 100)
        self.assertEqual(settings["display"], "write")
        self.assertFalse(settings["shuffle"])
        self.assertNotIn("unknown", settings)

    def test_shortcuts_need_a_modifier_stay_unique_and_avoid_reserved_keys(self):
        settings = self._settings(shortcuts={
            "play_pause": "Shift+Alt+KeyP",       # Reihenfolge wird vereinheitlicht
            "next": "KeyN",                       # ohne Strg/Alt: Tipp-Taste
            "previous": "Ctrl+Alt+KeyB",          # startet schon den Schoner
            "back": "Ctrl+Alt+KeyK",              # Schreibmodus
            "forward": "Alt+Shift+KeyP",          # doppelt
            "volume_up": "Ctrl+Alt+Meta+ArrowUp",  # Meta kennt der Browser als Strg
        })["shortcuts"]
        self.assertEqual(settings["play_pause"], "Alt+Shift+KeyP")
        self.assertEqual(settings["next"], screensaver_player.DEFAULT_SHORTCUTS["next"])
        self.assertEqual(settings["previous"], screensaver_player.DEFAULT_SHORTCUTS["previous"])
        self.assertEqual(settings["back"], screensaver_player.DEFAULT_SHORTCUTS["back"])
        self.assertEqual(settings["forward"], screensaver_player.DEFAULT_SHORTCUTS["forward"])
        self.assertEqual(settings["volume_up"], screensaver_player.DEFAULT_SHORTCUTS["volume_up"])
        self.assertEqual(settings["preset_1"], "Ctrl+Alt+Digit1")

    def test_light_effects_default_to_color_and_glass_and_are_switched_one_by_one(self):
        settings = self.client.get("/api/screensaver/player/settings").get_json()["settings"]
        self.assertEqual(settings["effects"], {"color": True, "reactive": False, "glass": True, "breathe": False})
        effects = self._settings(effects={"reactive": True, "glass": "ja", "funkeln": True})["effects"]
        self.assertEqual(effects, {"color": True, "reactive": True, "glass": True, "breathe": False})

    def test_preset_keys_are_shortcuts_like_any_other(self):
        settings = self._settings(shortcuts={
            "next": "Ctrl+Alt+Digit3",     # nimmt Preset 3 die Voreinstellung weg
            "preset_1": "Ctrl+Alt+Shift+Digit1",
            "preset_9": "",                # ausdruecklich unbelegt
        })["shortcuts"]
        self.assertEqual(settings["next"], "Ctrl+Alt+Digit3")
        self.assertEqual(settings["preset_3"], "")
        self.assertEqual(settings["preset_1"], "Ctrl+Alt+Shift+Digit1")
        self.assertEqual(settings["preset_2"], "Ctrl+Alt+Digit2")
        self.assertEqual(settings["preset_9"], "")
        # Ohne Angabe gilt die Voreinstellung, nicht „unbelegt“.
        self.assertEqual(self._settings(volume=40)["shortcuts"]["preset_9"], "Ctrl+Alt+Digit9")

    def test_writing_needs_csrf(self):
        response = self.client.post("/api/screensaver/player/settings", json={"volume": 10})
        self.assertEqual(response.status_code, 403)
        response = self.client.put("/api/screensaver/player/state", json={"track": "0" * 40})
        self.assertEqual(response.status_code, 403)

    # ─── Quellen und Titel ──────────────────────────────────────────
    def test_sources_list_only_folders_with_music(self):
        self._private("Musik", "a.mp3", "b.flac")
        self._private("Nur Bilder")
        (self.data / "user-1" / "screensaver" / "ordner" / "Nur Bilder" / "x.png").write_bytes(b"png")
        self._shared("Radio-Mitschnitte", "c.ogg")
        sources = self.client.get("/api/screensaver/player/settings?sources=1").get_json()["sources"]
        self.assertEqual([(source["id"], source["count"]) for source in sources],
                         [("user:Musik", 2), ("local:Radio-Mitschnitte", 1)])

    def test_tracks_follow_the_chosen_sources_in_path_order(self):
        directory = self._private("Musik", "02 - Zwei.mp3", "01 - Eins.mp3", "Album/03_Drei.m4a", ".versteckt.mp3")
        (directory / "cover.jpg").write_bytes(b"jpg")
        self._shared("Gemeinsam", "z.wav")
        self._settings(sources=["local:Gemeinsam", "user:Musik"])
        payload = self._tracks()
        self.assertEqual([track["title"] for track in payload["tracks"]], ["z", "Eins", "Zwei", "Drei"])
        self.assertEqual(payload["tracks"][3]["album"], "Album")
        self.assertTrue(payload["tracks"][1]["cover"])
        self.assertEqual(payload["tracks"][0]["cover"], "")
        self.assertEqual(payload["missing"], [])
        for track in payload["tracks"]:
            self.assertNotIn("Musik/", track["src"])

    def test_audio_is_served_with_range_support_and_the_cover_alongside(self):
        directory = self._private("Musik", "song.mp3")
        (directory / "folder.png").write_bytes(b"png")
        self._settings(sources=["user:Musik"])
        track = self._tracks()["tracks"][0]
        full = self.client.get(track["src"])
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.mimetype, "audio/mpeg")
        self.assertEqual(full.data, AUDIO)
        partial = self.client.get(track["src"], headers={"Range": "bytes=100-199"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.data, AUDIO[100:200])
        self.assertEqual(self.client.get(track["cover"]).data, b"png")

    @unittest.skipUnless(screensaver_tags_available(), "mutagen is not installed")
    def test_id3_tags_name_the_track_and_bring_their_own_cover(self):
        from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
        directory = self._private("Musik")
        path = directory / "01 - datei.mp3"
        path.write_bytes(MP3)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Echter Titel"))
        tags.add(TPE1(encoding=3, text="Interpretin"))
        tags.add(TALB(encoding=3, text="Das Album"))
        tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=PNG))
        tags.save(path)
        (directory / "cover.jpg").write_bytes(b"\xff\xd8\xff-ordner")
        self._settings(sources=["user:Musik"])
        track = self._tracks()["tracks"][0]
        self.assertEqual((track["title"], track["artist"], track["album"]), ("Echter Titel", "Interpretin", "Das Album"))
        cover = self.client.get(track["cover"])
        self.assertEqual(cover.status_code, 200)
        self.assertEqual(cover.mimetype, "image/png")
        self.assertEqual(cover.data, PNG)
        # Der Tag-Cache haelt, solange die Datei gleich bleibt.
        cache = list((self.data / "cache" / "screensaver" / "audio-tags").glob("*.json"))
        self.assertEqual(len(cache), 1)

    def test_track_ids_survive_a_deleted_cache_and_new_files_appear(self):
        self._private("Musik", "a.mp3")
        self._settings(sources=["user:Musik"])
        first = self._tracks()["tracks"][0]
        screensaver_player._index_path("user-1").unlink()
        self.assertEqual(self.client.get(first["src"]).status_code, 200)
        self._private("Musik", "b.mp3")
        self.assertEqual(self.client.get("/api/screensaver/player/audio/" + "0" * 40).status_code, 404)
        self.assertEqual(len(self._tracks()["tracks"]), 2)

    def test_private_music_stays_with_its_own_account(self):
        self._private("Musik", "geheim.mp3")
        self._settings(sources=["user:Musik"])
        track = self._tracks()["tracks"][0]
        self.user = {"id": "user-2", "username": "gast"}
        self.assertEqual(self.client.get(track["src"]).status_code, 404)
        self.client.post("/api/screensaver/player/settings", headers=self.headers, json={"sources": ["user:Musik"]})
        self.assertEqual(self._tracks()["tracks"], [])

    def test_unknown_and_malformed_ids_are_rejected(self):
        for track_id in ("../../etc/passwd", "abc", "g" * 40):
            self.assertEqual(self.client.get(f"/api/screensaver/player/audio/{track_id}").status_code, 404)

    def test_symlinked_files_are_not_tracks(self):
        directory = self._private("Musik", "echt.mp3")
        outside = self.data / "draussen.mp3"
        outside.write_bytes(AUDIO)
        (directory / "link.mp3").symlink_to(outside)
        self._settings(sources=["user:Musik"])
        self.assertEqual([track["title"] for track in self._tracks()["tracks"]], ["echt"])

    # ─── Wiedergabestand ────────────────────────────────────────────
    def test_playback_position_is_stored_per_account_for_resume(self):
        self._private("Musik", "a.mp3")
        self._settings(sources=["user:Musik"])
        track = self._tracks()["tracks"][0]
        response = self.client.put("/api/screensaver/player/state", headers=self.headers,
                                   json={"track": track["id"], "position": 83.456})
        self.assertEqual(response.status_code, 200)
        state = self._tracks()["state"]
        self.assertEqual((state["track"], state["position"]), (track["id"], 83.5))
        self.assertTrue((self.data / "user-1" / "screensaver" / "player.json").is_file())
        bad = self.client.put("/api/screensaver/player/state", headers=self.headers, json={"track": "x"})
        self.assertEqual(bad.status_code, 400)

    # ─── Presets ────────────────────────────────────────────────────
    def test_presets_carry_music_but_not_shortcuts_or_display(self):
        self._private("Ruhig", "a.mp3")
        self._private("Wach", "b.mp3")
        self._settings(sources=["user:Ruhig"], shuffle=True, crossfade=4, volume=30, display="write")
        self.client.post("/api/screensaver/presets", headers=self.headers, json={"label": "Abend"})
        stored = self.user["screensaver_presets"][0]["player"]
        self.assertEqual(stored["sources"], ["user:Ruhig"])
        self.assertNotIn("shortcuts", stored)
        self.assertNotIn("display", stored)

        self._settings(sources=["user:Wach"], volume=80, display="screensaver")
        self.assertTrue(self.client.get("/api/screensaver/presets").get_json()["modified"])
        applied = self.client.post("/api/screensaver/presets/abend/apply", headers=self.headers, json={}).get_json()
        self.assertTrue(applied["player_from_preset"])
        self.assertEqual(applied["player"]["sources"], ["user:Ruhig"])
        self.assertEqual(applied["player"]["volume"], 30)
        self.assertEqual(applied["player"]["display"], "screensaver")
        self.assertFalse(applied["modified"])

    def test_presets_from_before_the_player_leave_the_music_alone(self):
        self._private("Musik", "a.mp3")
        self._settings(sources=["user:Musik"], volume=55)
        self.user["screensaver_presets"] = [{"id": "alt", "label": "Alt", "screensaver": {"interval": 60}}]
        applied = self.client.post("/api/screensaver/presets/alt/apply", headers=self.headers, json={}).get_json()
        self.assertFalse(applied["player_from_preset"])
        self.assertEqual(applied["player"]["sources"], ["user:Musik"])
        self.assertEqual(applied["player"]["volume"], 55)

    def test_a_missing_music_folder_is_skipped_and_reported(self):
        self._private("Musik", "a.mp3")
        self.user["screensaver_presets"] = [{"id": "p", "label": "P",
                                             "player": {"sources": ["user:Musik", "user:Weg"]}}]
        applied = self.client.post("/api/screensaver/presets/p/apply", headers=self.headers, json={}).get_json()
        self.assertEqual(applied["player"]["sources"], ["user:Musik"])
        self.assertIn("user:Weg", applied["missing"])
        self.assertEqual(self.user["screensaver_presets"][0]["player"]["sources"], ["user:Musik", "user:Weg"])

    def test_renaming_and_deleting_a_folder_follows_into_player_and_presets(self):
        self._private("Musik", "a.mp3")
        self._settings(sources=["user:Musik"])
        self.client.post("/api/screensaver/presets", headers=self.headers, json={"label": "Mit Musik"})
        self.client.patch("/api/screensaver/folders/Musik", headers=self.headers, json={"name": "Lieder"})
        self.assertEqual(self.user["screensaver_player"]["sources"], ["user:Lieder"])
        self.assertEqual(self.user["screensaver_presets"][0]["player"]["sources"], ["user:Lieder"])
        self.client.delete("/api/screensaver/folders/Lieder", headers=self.headers)
        self.assertEqual(self.user["screensaver_player"]["sources"], [])
        self.assertEqual(self.user["screensaver_presets"][0]["player"]["sources"], [])

    # ─── Uploads in „Meine Ordner“ ──────────────────────────────────
    def _upload(self, folder, name, data):
        return self.client.post(f"/api/screensaver/folders/{folder}/files", headers=self.headers,
                                data={"files": (io.BytesIO(data), name)}, content_type="multipart/form-data")

    def test_music_uploads_land_next_to_the_images(self):
        self._private("Gemischt")
        response = self._upload("Gemischt", "Mein Lied.FLAC", AUDIO)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["saved"], ["Mein Lied.flac"])
        folder = response.get_json()["folder"]
        self.assertEqual((folder["count"], folder["audio"]), (0, 1))
        files = self.client.get("/api/screensaver/folders/Gemischt").get_json()["files"]
        self.assertEqual(files, [{"name": "Mein Lied.flac", "bytes": len(AUDIO), "kind": "audio"}])
        self.assertEqual(self.client.delete("/api/screensaver/folders/Gemischt/files/Mein%20Lied.flac",
                                            headers=self.headers).status_code, 200)

    def test_audio_has_its_own_larger_limit(self):
        self._private("Gemischt")
        original = (screensaver.MAX_UPLOAD_BYTES, screensaver.MAX_AUDIO_UPLOAD_BYTES)
        screensaver.MAX_UPLOAD_BYTES, screensaver.MAX_AUDIO_UPLOAD_BYTES = 100, 4096
        try:
            self.assertEqual(self._upload("Gemischt", "lang.mp3", AUDIO).status_code, 200)
            self.assertEqual(self._upload("Gemischt", "gross.png", AUDIO).status_code, 400)
            too_big = self._upload("Gemischt", "zu-lang.mp3", AUDIO * 3)
            self.assertEqual(too_big.status_code, 400)
            self.assertIn("Größer als", too_big.get_json()["rejected"][0]["error"])
        finally:
            screensaver.MAX_UPLOAD_BYTES, screensaver.MAX_AUDIO_UPLOAD_BYTES = original


class ScreensaverPlayerFrontendTests(unittest.TestCase):
    """Ladefolge und Vertraege zwischen den Player-Skripten."""

    @classmethod
    def setUpClass(cls):
        static = ROOT / "app" / "static"
        cls.macro = (ROOT / "app" / "templates" / "_screensaver.html").read_text(encoding="utf-8")
        cls.player = (static / "screensaver-player.js").read_text(encoding="utf-8")
        cls.effects = (static / "screensaver-player-effects.js").read_text(encoding="utf-8")
        cls.screensaver = (static / "screensaver.js").read_text(encoding="utf-8")
        cls.view = (static / "screensaver-player-view.js").read_text(encoding="utf-8")
        cls.sources = (static / "screensaver-player-sources.js").read_text(encoding="utf-8")
        cls.visual = (static / "screensaver-visual.js").read_text(encoding="utf-8")

    def test_view_effects_and_sources_load_around_the_player(self):
        order = ["screensaver-player-view.js", "screensaver-player-effects.js", "screensaver-player.js",
                 "screensaver-player-sources.js", "screensaver-player-settings.js"]
        positions = [self.macro.index(f"filename='{name}'") for name in order]
        self.assertEqual(positions, sorted(positions))

    def test_the_screensaver_reports_its_image_for_the_color_effect(self):
        self.assertIn("journl:screensaver-image", self.screensaver)
        self.assertIn("journl:screensaver-image", self.effects)

    def test_ios_without_settable_volume_gets_no_crossfade_or_ducking(self):
        # Sonst liefen beim Crossfade beide Titel voll uebereinander.
        self.assertIn("probe.volume = 0.5;", self.player)
        self.assertIn("const fade = volumeLocked ? 0 : (options.fade || 0);", self.player)
        self.assertIn("dictation === 'duck' && !volumeLocked", self.player)
        self.assertIn("reactive: Boolean(wanted.reactive) && !volumeLocked", self.player)

    def test_the_analyser_only_wires_decks_into_a_running_context(self):
        # Ein haengender AudioContext wuerde den Ton stumm schalten.
        self.assertIn("context.state !== 'running') return;", self.effects)
        self.assertIn("effects?.wake()", self.player)

    def test_mobile_player_steps_aside_while_the_keyboard_is_open(self):
        # Fixiert am unteren Rand laege er auf iOS hinter der Tastatur; nach
        # dem Schliessen wird neu gemessen, wenn die App-Hoehe nachgezogen ist.
        self.assertIn("classList.contains('keyboard-open')", self.view)
        self.assertIn("(state.typing || keyboardOpen())", self.view)
        self.assertIn("new MutationObserver(settle)", self.view)

    def test_the_menu_brings_a_hidden_player_back(self):
        self.assertIn("data-player-toggle", self.macro)
        self.assertIn("closest('[data-player-toggle]')", self.player)
        self.assertIn("persist({ visibility: show ? 'minimal' : 'hidden' })", self.player)

    def test_station_form_is_read_before_busy_redraws_it(self):
        # busy() zeichnet die Maske neu; las addStation danach, kam die URL leer an.
        self.assertLess(self.sources.index("const station = { label: value('label')"),
                        self.sources.index("await busy('Stream wird geprüft…'"))
        self.assertIn("body: JSON.stringify(station)", self.sources)
        self.assertIn("data.draft.station?.url", self.sources)

    def test_the_visual_loads_after_the_player_and_shares_its_analyser(self):
        self.assertLess(self.macro.index("filename='screensaver-player-settings.js'"),
                        self.macro.index("filename='screensaver-visual.js'"))
        self.assertIn("return { mount, configure, setCover, setPlaying, wake, demand, levels, palette };", self.effects)
        # Auf iOS fordert das Klangbild keinen echten Ton an - es simuliert.
        self.assertIn("demand?.('visual', on && !simulated() && !reaction().free)", self.visual)
        self.assertIn("raw = simulate(now);", self.visual)

    def test_the_visual_sits_above_the_dim_only_in_the_slideshow(self):
        self.assertIn("if (mode === 'screensaver') dim.after(canvas);", self.visual)
        self.assertIn("journl:screensaver-mode", self.screensaver)
        self.assertIn("window.journlScreensaverVisual?.handleKey(event)", self.screensaver)

    def test_visual_forms_match_the_server_and_beats_come_from_an_unsmoothed_analyser(self):
        import screensaver_visual
        forms = "const FORMS = [" + ", ".join(f"'{form}'" for form in screensaver_visual.FORMS) + "];"
        self.assertIn(forms, self.visual)
        for reaction in screensaver_visual.REACTIONS:
            self.assertIn(f"    {reaction}: {{ quick:", self.visual)
        # Der geglaettete Analyser verwischt Kicks; Schlaege kommen aus dem zweiten.
        self.assertIn("sharp.smoothingTimeConstant = 0.3;", self.effects)
        self.assertIn("source.connect(sharp);", self.effects)
        # Mindestabstand haelt das Aufhellen unter ~3,5 Blitzen pro Sekunde.
        self.assertIn("const REFRACTORY = 0.28;", self.visual)
        # Die gewaehlte Reaktion gilt auch bei „Bewegung reduzieren“ - sonst kommt keine Musik an.
        self.assertIn("return REACTIONS[settings.reaction] || REACTIONS.beat;", self.visual)
        self.assertIn("react.flash * (reduced?.matches ? 0.5 : 1)", self.visual)
        # WebKit liefert bei Live-Streams nur Nullen: dann simulierter Takt.
        self.assertIn("if (deaf > 1.5) heard = null;", self.visual)
        # Safari/WebKit: Decks im Audio-Graphen springen an den Titelanfang.
        self.assertIn("if (webkit || !needed() || !root) return;", self.effects)


if __name__ == "__main__":
    unittest.main()
