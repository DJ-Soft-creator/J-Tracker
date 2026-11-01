"""Bildschirmschoner: Quellen, Playlist-Manifeste und Bildauslieferung."""

import importlib.util
import io
import json
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


# Ein winziges, gueltiges PNG - genug fuer send_file und die Suffixpruefung.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class ScreensaverTests(unittest.TestCase):
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

    def _collection(self, folder, count=3):
        directory = self.data / "screensaver" / folder
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (directory / f"bild-{index}.png").write_bytes(PNG)
        return directory

    def _journal_image(self, name="2026-09-14_10-00-00.png"):
        directory = self.data / "user-1" / "2026" / "09" / "14" / "media"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(PNG)
        return directory / name

    # ─── Einstellungen ──────────────────────────────────────────────
    def test_settings_start_at_the_documented_default(self):
        payload = self.client.get("/api/screensaver/settings").get_json()
        self.assertEqual(payload["settings"]["interval"], 30)
        self.assertEqual(payload["settings"]["sources"], ["theme:landscapes"])
        self.assertEqual(payload["settings"]["mode"], "screensaver")

    def test_settings_are_clamped_and_unknown_sources_dropped(self):
        response = self.client.post("/api/screensaver/settings", headers=self.headers, json={
            "interval": 99999,
            "sources": ["theme:landscapes", "theme:does-not-exist", "local:../etc", "journal"],
            "info": "nonsense",
            "dim": 500,
        })
        settings = response.get_json()["settings"]
        self.assertEqual(settings["interval"], screensaver.INTERVAL_MAX)
        self.assertEqual(settings["sources"], ["theme:landscapes", "journal"])
        self.assertEqual(settings["info"], "full")  # unbekannter Wert faellt auf die Vorgabe zurueck
        self.assertEqual(settings["dim"], 80)

    def test_the_fade_duration_is_clamped_and_snapped_to_tenths(self):
        for sent, expected in ((0, 0.0), (0.04, 0.0), (0.17, 0.2), (2.5, 2.5), (99, 10.0), (-3, 0.0)):
            response = self.client.post("/api/screensaver/settings", headers=self.headers,
                                        json={"fade_seconds": sent})
            self.assertEqual(response.get_json()["settings"]["fade_seconds"], expected, sent)

    def test_a_missing_or_wrong_fade_duration_keeps_the_default(self):
        for sent in ({}, {"fade_seconds": "zwei"}, {"fade_seconds": True}):
            response = self.client.post("/api/screensaver/settings", headers=self.headers, json=sent)
            self.assertEqual(response.get_json()["settings"]["fade_seconds"], 1.0, sent)

    def test_a_source_list_without_any_valid_entry_keeps_the_default(self):
        response = self.client.post("/api/screensaver/settings", headers=self.headers, json={"sources": ["nope"]})
        self.assertEqual(response.get_json()["settings"]["sources"], ["theme:landscapes"])

    def test_visual_settings_default_off_and_are_clamped(self):
        payload = self.client.get("/api/screensaver/settings").get_json()
        self.assertEqual(payload["settings"]["visual"], screensaver.screensaver_visual.DEFAULT_VISUAL)
        response = self.client.post("/api/screensaver/settings", headers=self.headers, json={
            "visual": {"enabled": True, "form": "ink", "blend": "laser", "strength": 900, "quality": "full",
                       "reaction": "brutal"},
        })
        visual = response.get_json()["settings"]["visual"]
        self.assertEqual(visual, {"enabled": True, "form": "ink", "blend": "glow", "strength": 100, "quality": "full",
                                  "reaction": "beat"})

    def test_visual_accepts_the_psychedelic_forms_and_the_wild_reaction(self):
        for form in ("kaleido", "mandala", "tunnel", "fractal", "plasma"):
            response = self.client.post("/api/screensaver/settings", headers=self.headers,
                                        json={"visual": {"form": form, "reaction": "wild"}})
            visual = response.get_json()["settings"]["visual"]
            self.assertEqual((visual["form"], visual["reaction"]), (form, "wild"))

    def test_no_images_stands_alone_and_yields_an_empty_playlist(self):
        self._collection("Berge", count=2)
        mixed = self.client.post("/api/screensaver/settings", headers=self.headers,
                                 json={"sources": ["none", "local:Berge"]}).get_json()["settings"]
        self.assertEqual(mixed["sources"], ["local:Berge"])
        alone = self.client.post("/api/screensaver/settings", headers=self.headers,
                                 json={"sources": ["none"]}).get_json()["settings"]
        self.assertEqual(alone["sources"], ["none"])
        playlist = self.client.get("/api/screensaver/playlist").get_json()
        self.assertEqual(playlist, {"items": [], "none": True})
        sources = self.client.get("/api/screensaver/settings").get_json()["sources"]
        self.assertEqual(sources[0], {"id": "none", "label": "Keine Bilder", "kind": "none"})

    def test_settings_need_the_csrf_token(self):
        self.assertEqual(self.client.post("/api/screensaver/settings", json={"interval": 10}).status_code, 403)

    # ─── Quellen ────────────────────────────────────────────────────
    def test_every_subfolder_is_its_own_selectable_collection(self):
        self._collection("Berge", count=2)
        self._collection("Nordsee", count=4)
        sources = {item["id"]: item for item in self.client.get("/api/screensaver/settings").get_json()["sources"]}
        self.assertEqual(sources["local:Berge"]["count"], 2)
        self.assertEqual(sources["local:Nordsee"]["count"], 4)
        self.assertEqual(sources["local:Nordsee"]["label"], "Nordsee")
        self.assertIn("theme:landscapes", sources)
        self.assertIn("journal", sources)

    def test_journal_images_are_found_below_the_dated_media_folders(self):
        self._journal_image()
        sources = {item["id"]: item for item in self.client.get("/api/screensaver/settings").get_json()["sources"]}
        self.assertEqual(sources["journal"]["count"], 1)

    # ─── Playlist ───────────────────────────────────────────────────
    def test_playlist_mixes_the_selected_local_sources(self):
        self._collection("Berge", count=5)
        self._collection("Nordsee", count=5)
        self.client.post("/api/screensaver/settings", headers=self.headers,
                         json={"sources": ["local:Berge", "local:Nordsee"]})
        items = self.client.get("/api/screensaver/playlist?count=8").get_json()["items"]
        self.assertEqual(len(items), 8)
        self.assertEqual({item["source"] for item in items}, {"Berge", "Nordsee"})
        self.assertEqual(len({item["id"] for item in items}), 8)  # keine Wiederholung in einer Runde

    def test_playlist_reports_an_empty_source_instead_of_failing(self):
        self.client.post("/api/screensaver/settings", headers=self.headers, json={"sources": ["journal"]})
        payload = self.client.get("/api/screensaver/playlist").get_json()
        self.assertEqual(payload["items"], [])
        self.assertIn("Keine Bilder", payload["error"])

    def test_deselecting_a_folder_removes_it_from_the_next_playlist(self):
        self._collection("Berge", count=4)
        self._collection("Nordsee", count=4)
        self.client.post("/api/screensaver/settings", headers=self.headers,
                         json={"sources": ["local:Berge", "local:Nordsee"]})
        self.client.post("/api/screensaver/settings", headers=self.headers, json={"sources": ["local:Nordsee"]})
        items = self.client.get("/api/screensaver/playlist?count=8").get_json()["items"]
        self.assertEqual({item["source"] for item in items}, {"Nordsee"})

    def test_spread_order_alternates_between_the_sources(self):
        self._collection("Berge", count=6)
        self._collection("Nordsee", count=6)
        self.client.post("/api/screensaver/settings", headers=self.headers,
                         json={"sources": ["local:Berge", "local:Nordsee"], "order": "spread"})
        items = self.client.get("/api/screensaver/playlist?count=6").get_json()["items"]
        self.assertEqual([item["source"] for item in items], ["Berge", "Nordsee"] * 3)

    def test_sequential_order_pages_through_journal_images_by_date(self):
        for day in ("12", "03", "20"):
            directory = self.data / "user-1" / "2026" / "09" / day / "media"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "foto.png").write_bytes(PNG)
        self.client.post("/api/screensaver/settings", headers=self.headers,
                         json={"sources": ["journal"], "order": "sequential", "details": True})
        first = self.client.get("/api/screensaver/playlist?count=2&offset=0").get_json()
        self.assertEqual([item["date"] for item in first["items"]], ["03.09.2026", "12.09.2026"])
        self.assertEqual(first["next_offset"], 2)
        second = self.client.get("/api/screensaver/playlist?count=2&offset=2").get_json()
        self.assertEqual([item["date"] for item in second["items"]], ["20.09.2026"])
        self.assertEqual(second["next_offset"], 0)  # am Ende wieder von vorn
        self.assertTrue(self.client.get("/api/screensaver/settings").get_json()["settings"]["details"])

    def test_unknown_order_falls_back_to_shuffle(self):
        response = self.client.post("/api/screensaver/settings", headers=self.headers, json={"order": "chaos"})
        self.assertEqual(response.get_json()["settings"]["order"], "shuffle")

    # ─── Auslieferung ───────────────────────────────────────────────
    def test_an_image_is_served_through_its_manifest(self):
        self._collection("Berge", count=1)
        self.client.post("/api/screensaver/settings", headers=self.headers, json={"sources": ["local:Berge"]})
        item = self.client.get("/api/screensaver/playlist?count=1").get_json()["items"][0]
        response = self.client.get(item["src"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, PNG)

    def test_an_unknown_id_is_not_served(self):
        self.assertEqual(self.client.get("/api/screensaver/image/" + "a" * 40).status_code, 404)
        self.assertEqual(self.client.get("/api/screensaver/image/..%2F..%2Fetc%2Fpasswd").status_code, 404)

    def test_a_manifest_can_never_point_outside_its_own_root(self):
        """Ein manipuliertes Manifest darf keine fremde Datei ausliefern."""
        secret = self.data / "users.json"
        secret.write_text("{}", encoding="utf-8")
        image_id = "b" * 40
        manifest = self.data / "cache" / "screensaver" / f"{image_id}.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"kind": "local", "path": "../users.json"}), encoding="utf-8")
        self.assertEqual(self.client.get(f"/api/screensaver/image/{image_id}").status_code, 404)

    def test_journal_images_stay_with_their_own_account(self):
        self._journal_image()
        self.client.post("/api/screensaver/settings", headers=self.headers, json={"sources": ["journal"]})
        item = self.client.get("/api/screensaver/playlist?count=1").get_json()["items"][0]
        self.assertEqual(self.client.get(item["src"]).status_code, 200)
        self.user["id"] = "user-2"
        self.assertEqual(self.client.get(item["src"]).status_code, 404)

    # ─── Private Ordner ─────────────────────────────────────────────
    def _private(self, folder, count=2, user="user-1"):
        directory = self.data / user / "screensaver" / "ordner" / folder
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (directory / f"privat-{index}.png").write_bytes(PNG)
        return directory

    def _upload(self, folder, name="foto.png", data=PNG):
        return self.client.post(f"/api/screensaver/folders/{folder}/files", headers=self.headers,
                                data={"files": (io.BytesIO(data), name)}, content_type="multipart/form-data")

    def test_private_folders_are_their_own_sources_next_to_the_shared_ones(self):
        self._collection("Berge", count=1)
        self._private("Berge", count=3)
        sources = {item["id"]: item for item in self.client.get("/api/screensaver/settings").get_json()["sources"]}
        self.assertEqual(sources["user:Berge"]["count"], 3)
        self.assertEqual(sources["user:Berge"]["kind"], "user")
        self.assertEqual(sources["local:Berge"]["count"], 1)

    def test_files_dropped_in_by_sync_show_up_without_the_app(self):
        directory = self._private("Sync", count=1)
        (directory / "unten" ).mkdir()
        (directory / "unten" / "tief.png").write_bytes(PNG)
        folders = self.client.get("/api/screensaver/folders").get_json()["folders"]
        self.assertEqual(folders, [{"name": "Sync", "count": 2, "audio": 0, "bytes": 2 * len(PNG)}])

    def test_private_images_stay_with_their_own_account(self):
        self._private("Urlaub", count=1)
        self.client.post("/api/screensaver/settings", headers=self.headers, json={"sources": ["user:Urlaub"]})
        item = self.client.get("/api/screensaver/playlist?count=1").get_json()["items"][0]
        self.assertEqual(self.client.get(item["src"]).status_code, 200)
        self.user["id"] = "user-2"
        self.assertEqual(self.client.get(item["src"]).status_code, 404)
        self.assertNotIn("user:Urlaub", [s["id"] for s in self.client.get("/api/screensaver/settings").get_json()["sources"]])
        self.assertEqual(self.client.get("/api/screensaver/folders/Urlaub").status_code, 404)

    def test_a_folder_is_created_renamed_and_deleted_with_the_selection_following(self):
        created = self.client.post("/api/screensaver/folders", headers=self.headers, json={"name": "  Ur/laub: 2026  "})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["folder"]["name"], "Urlaub 2026")
        self.assertEqual(self.client.post("/api/screensaver/folders", headers=self.headers,
                                          json={"name": "Urlaub 2026"}).status_code, 409)
        self.assertEqual(self._upload("Urlaub 2026").status_code, 200)
        self.client.post("/api/screensaver/settings", headers=self.headers,
                         json={"sources": ["user:Urlaub 2026", "journal"]})

        renamed = self.client.patch("/api/screensaver/folders/Urlaub 2026", headers=self.headers, json={"name": "Sommer"})
        self.assertEqual(renamed.status_code, 200)
        self.assertTrue((self.data / "user-1" / "screensaver" / "ordner" / "Sommer" / "foto.png").is_file())
        self.assertEqual(self.user["screensaver"]["sources"], ["user:Sommer", "journal"])

        deleted = self.client.delete("/api/screensaver/folders/Sommer", headers=self.headers)
        self.assertEqual(deleted.get_json()["removed"], 1)
        self.assertFalse((self.data / "user-1" / "screensaver" / "ordner" / "Sommer").exists())
        self.assertEqual(self.user["screensaver"]["sources"], ["journal"])

    def test_folder_names_can_never_leave_the_own_root(self):
        for name in ("..", "", "/", " . "):
            response = self.client.post("/api/screensaver/folders", headers=self.headers, json={"name": name})
            self.assertEqual(response.status_code, 400, name)
        hidden = self.client.post("/api/screensaver/folders", headers=self.headers, json={"name": ".versteckt"})
        self.assertEqual(hidden.get_json()["folder"]["name"], "versteckt")
        self._private("Echt", count=1)
        for path in ("/api/screensaver/folders/..", "/api/screensaver/folders/Echt/files/../../../../users.json",
                     "/api/screensaver/folders/Echt/files/%2E%2E/%2E%2E/question-sets.json"):
            self.assertIn(self.client.get(path).status_code, (404, 308), path)
        self.assertEqual(self.client.patch("/api/screensaver/folders/Echt", headers=self.headers,
                                           json={"name": "../draussen"}).status_code, 200)
        self.assertTrue((self.data / "user-1" / "screensaver" / "ordner" / "draussen").is_dir())

    def test_uploads_keep_the_original_and_get_a_free_name(self):
        self.client.post("/api/screensaver/folders", headers=self.headers, json={"name": "Neu"})
        self.assertEqual(self._upload("Neu", name="../bild.PNG").get_json()["saved"], ["bild.png"])
        self.assertEqual(self._upload("Neu", name="bild.png").get_json()["saved"], ["bild (2).png"])
        stored = self.data / "user-1" / "screensaver" / "ordner" / "Neu" / "bild.png"
        self.assertEqual(stored.read_bytes(), PNG)
        served = self.client.get("/api/screensaver/folders/Neu/files/bild.png")
        self.assertEqual(served.data, PNG)
        served.close()

    def test_uploads_reject_foreign_formats_and_oversized_files(self):
        self.client.post("/api/screensaver/folders", headers=self.headers, json={"name": "Neu"})
        rejected = self._upload("Neu", name="skript.svg", data=b"<svg/>")
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.get_json()["rejected"][0]["name"], "skript.svg")
        previous = screensaver.MAX_UPLOAD_BYTES
        screensaver.MAX_UPLOAD_BYTES = 10
        try:
            self.assertEqual(self._upload("Neu").status_code, 400)
        finally:
            screensaver.MAX_UPLOAD_BYTES = previous
        self.assertEqual(list((self.data / "user-1" / "screensaver" / "ordner" / "Neu").iterdir()), [])

    def test_a_single_file_is_deleted(self):
        self._private("Weg", count=2)
        response = self.client.delete("/api/screensaver/folders/Weg/files/privat-0.png", headers=self.headers)
        self.assertEqual(response.get_json()["folder"]["count"], 1)
        self.assertEqual(self.client.delete("/api/screensaver/folders/Weg/files/privat-0.png",
                                            headers=self.headers).status_code, 404)

    def test_folder_changes_need_the_csrf_token(self):
        self._private("Schutz", count=1)
        self.assertEqual(self.client.post("/api/screensaver/folders", json={"name": "x"}).status_code, 403)
        self.assertEqual(self.client.delete("/api/screensaver/folders/Schutz").status_code, 403)
        self.assertEqual(self.client.patch("/api/screensaver/folders/Schutz", json={"name": "y"}).status_code, 403)
        self.assertEqual(self.client.delete("/api/screensaver/folders/Schutz/files/privat-0.png").status_code, 403)

    def test_everything_requires_a_session(self):
        main._current_user = lambda: None
        for path in ("/api/screensaver/settings", "/api/screensaver/playlist", "/api/screensaver/image/" + "c" * 40):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    # ─── Cache ──────────────────────────────────────────────────────
    def test_the_cache_is_pruned_back_to_its_limit(self):
        directory = self.data / "cache" / "screensaver"
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(screensaver.CACHE_LIMIT + 20):
            (directory / f"{index:040x}.json").write_text("{}", encoding="utf-8")
        screensaver._prune_cache()
        self.assertEqual(len(list(directory.glob("*.json"))), screensaver.CACHE_LIMIT)


class ScreensaverOverlayTests(unittest.TestCase):
    """Das Overlay lebt nur im Browser - hier steht, was seine Quelle halten muss."""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "app" / "static" / "screensaver.js").read_text(encoding="utf-8")
        cls.markup = (ROOT / "app" / "templates" / "_screensaver.html").read_text(encoding="utf-8")

    def test_closing_on_purpose_leaves_no_way_back(self):
        """Kreuz und Esc beenden ganz; nur Aufwecken zeigt den Rueckkehr-Knopf."""
        self.assertIn("[data-close]').addEventListener('click', () => close({ resume: false }))", self.js)
        self.assertIn("close({ resume: false });", self.js.split("if (event.key === 'Escape')")[1][:120])
        self.assertIn("resumeButton()?.classList.toggle('hidden', !resume)", self.js)

    def test_both_arrow_keys_page_through_the_playlist(self):
        self.assertIn("event.key === 'ArrowLeft'", self.js)
        self.assertIn("function rewind()", self.js)

    def test_the_backdrop_mode_pages_with_buttons(self):
        """In der Kulisse gehoeren die Pfeiltasten dem Schreibfeld - Knoepfe blaettern."""
        self.assertIn("[data-prev]').addEventListener('click', () => step(-1))", self.js)
        self.assertIn("[data-next]').addEventListener('click', () => step(1))", self.js)
        self.assertIn("!isTyping(event.target)", self.js)

    def test_the_gear_keeps_the_slideshow_running_behind_the_settings(self):
        """Das Zahnrad beendet den Schoner nicht mehr, Esc schliesst dann nur das Modal."""
        self.assertIn("[data-settings]').addEventListener('click', () => openSettingsAbove())", self.js)
        self.assertIn("modal.style.zIndex = '130'", self.js)
        settings_branch = self.js.split("if (settingsOpen()) {")[1][:160]
        self.assertIn("window.closeSettings?.()", settings_branch)
        self.assertNotIn("close(", settings_branch.replace("closeSettings", ""))

    def test_a_shortcut_starts_the_slideshow_and_the_desktop_menu_names_it(self):
        self.assertIn("event.code !== 'KeyB'", self.js)
        self.assertIn("Strg+Alt+B", (ROOT / "app" / "templates" / "desktop.html").read_text(encoding="utf-8"))

    def test_the_caption_stays_readable_over_bright_images(self):
        """Der Nachweis war bei /45 und /20 auf hellen Bildern nicht mehr lesbar."""
        self.assertIn('data-info class="text-sm text-white/85', self.js)
        self.assertIn('data-credit class="text-xs text-white/60', self.js)


if __name__ == "__main__":
    unittest.main()
