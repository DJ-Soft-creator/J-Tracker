"""Bildschirmschoner-Presets: speichern, laden, abweichen, mitziehen."""

import importlib.util
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
    import screensaver_presets


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class ScreensaverPresetTests(unittest.TestCase):
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

    def _settings(self, **values):
        return self.client.post("/api/screensaver/settings", headers=self.headers, json=values).get_json()["settings"]

    def _flow(self, **values):
        return self.client.put("/api/screensaver/questions", headers=self.headers, json={"flow": values}).get_json()

    def _create(self, label):
        return self.client.post("/api/screensaver/presets", headers=self.headers, json={"label": label})

    def _apply(self, preset_id):
        return self.client.post(f"/api/screensaver/presets/{preset_id}/apply", headers=self.headers, json={})

    def _folder(self, name):
        directory = self.data / "user-1" / "screensaver" / "ordner" / name
        directory.mkdir(parents=True)
        (directory / "bild.png").write_bytes(b"\x89PNG")
        return directory

    def test_a_preset_freezes_settings_and_question_flow_under_a_stable_id(self):
        self._settings(interval=60, dim=40, mode="focus", sources=["journal"])
        self._flow(flow="quiz", seconds=30)
        response = self._create("  Grübeln   am Meer ")
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["preset"], "gruebeln-am-meer")
        self.assertEqual(payload["presets"], [{"id": "gruebeln-am-meer", "label": "Grübeln am Meer"}])
        self.assertEqual(payload["active"], "gruebeln-am-meer")
        self.assertFalse(payload["modified"])
        stored = self.user["screensaver_presets"][0]
        self.assertEqual(stored["screensaver"]["interval"], 60)
        self.assertEqual(stored["questions"]["flow"], "quiz")

    def test_names_are_unique_and_the_list_is_capped(self):
        self.assertEqual(self._create("Fokus").status_code, 201)
        self.assertEqual(self._create("fokus").status_code, 409)
        self.assertEqual(self._create("   ").status_code, 400)
        self.assertEqual(self._create("Fokus!").get_json()["preset"], "fokus-2")
        for index in range(screensaver_presets.MAX_PRESETS - 2):
            self._create(f"Nummer {index}")
        self.assertEqual(self._create("Zu viel").status_code, 400)

    def test_loading_restores_the_setup_and_later_changes_leave_the_preset_clean(self):
        self._settings(interval=60, sources=["journal"])
        self._flow(flow="quiz")
        self._create("Abend")
        self._settings(interval=10, sources=["theme:caves"])
        self._flow(flow="manual")
        self.assertTrue(self.client.get("/api/screensaver/presets").get_json()["modified"])

        applied = self._apply("abend").get_json()
        self.assertEqual(applied["settings"]["interval"], 60)
        self.assertEqual(applied["settings"]["sources"], ["journal"])
        self.assertEqual(applied["flow"]["flow"], "quiz")
        self.assertEqual(applied["missing"], [])
        self.assertFalse(applied["modified"])

        self._settings(interval=120, sources=["journal"])
        listing = self.client.get("/api/screensaver/presets").get_json()
        self.assertEqual(listing["active"], "abend")
        self.assertTrue(listing["modified"])
        self.assertEqual(self.user["screensaver_presets"][0]["screensaver"]["interval"], 60)

    def test_overwrite_and_rename_are_explicit_and_keep_the_id(self):
        self._settings(interval=60)
        self._create("Morgen")
        self._settings(interval=300)
        renamed = self.client.patch("/api/screensaver/presets/morgen", headers=self.headers, json={"label": "Früh"})
        self.assertEqual(renamed.get_json()["presets"], [{"id": "morgen", "label": "Früh"}])
        self.assertEqual(self.user["screensaver_presets"][0]["screensaver"]["interval"], 60)
        captured = self.client.patch("/api/screensaver/presets/morgen", headers=self.headers, json={"capture": True})
        self.assertFalse(captured.get_json()["modified"])
        self.assertEqual(self.user["screensaver_presets"][0]["screensaver"]["interval"], 300)

    def test_order_and_delete(self):
        for label in ("Eins", "Zwei", "Drei"):
            self._create(label)
        self.assertEqual(self.client.post("/api/screensaver/presets/order", headers=self.headers,
                                          json={"ids": ["drei", "eins"]}).status_code, 400)
        ordered = self.client.post("/api/screensaver/presets/order", headers=self.headers,
                                   json={"ids": ["drei", "eins", "zwei"]}).get_json()
        self.assertEqual([entry["id"] for entry in ordered["presets"]], ["drei", "eins", "zwei"])
        deleted = self.client.delete("/api/screensaver/presets/drei", headers=self.headers).get_json()
        self.assertEqual(deleted["active"], "")
        self.assertEqual([entry["id"] for entry in deleted["presets"]], ["eins", "zwei"])
        self.assertEqual(self._apply("drei").status_code, 404)

    def test_a_vanished_folder_is_skipped_and_reported_but_kept_in_the_preset(self):
        self._folder("Urlaub")
        self._settings(sources=["user:Urlaub", "journal"])
        self._create("Reise")
        (self.data / "user-1" / "screensaver" / "ordner" / "Urlaub" / "bild.png").unlink()
        (self.data / "user-1" / "screensaver" / "ordner" / "Urlaub").rmdir()
        applied = self._apply("reise").get_json()
        self.assertEqual(applied["missing"], ["user:Urlaub"])
        self.assertEqual(applied["settings"]["sources"], ["journal"])
        self.assertEqual(self.user["screensaver_presets"][0]["screensaver"]["sources"], ["user:Urlaub", "journal"])

    def test_renaming_or_deleting_a_folder_follows_into_every_preset(self):
        self._folder("Alt")
        self._settings(sources=["user:Alt"])
        self._create("Mit Ordner")
        self.client.patch("/api/screensaver/folders/Alt", headers=self.headers, json={"name": "Neu"})
        self.assertEqual(self.user["screensaver_presets"][0]["screensaver"]["sources"], ["user:Neu"])
        self.client.delete("/api/screensaver/folders/Neu", headers=self.headers)
        self.assertEqual(self.user["screensaver_presets"][0]["screensaver"]["sources"], [])
        self.assertEqual(self._apply("mit-ordner").get_json()["settings"]["sources"], ["theme:landscapes"])

    def test_fields_an_older_preset_does_not_know_keep_the_current_value(self):
        self._settings(dim=55, interval=60)
        self.user["screensaver_presets"] = [{"id": "alt", "label": "Alt", "screensaver": {"interval": 20}}]
        applied = self._apply("alt").get_json()
        self.assertEqual(applied["settings"]["interval"], 20)
        self.assertEqual(applied["settings"]["dim"], 55)
        self.assertFalse(applied["modified"])

    def test_changes_need_a_session_and_the_csrf_token(self):
        self._create("Schutz")
        self.assertEqual(self.client.post("/api/screensaver/presets", json={"label": "x"}).status_code, 403)
        self.assertEqual(self.client.post("/api/screensaver/presets/schutz/apply", json={}).status_code, 403)
        self.assertEqual(self.client.delete("/api/screensaver/presets/schutz").status_code, 403)
        main._current_user = lambda: None
        self.assertEqual(self.client.get("/api/screensaver/presets").status_code, 401)


class ScreensaverPresetFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app" / "static" / "screensaver-presets.js").read_text(encoding="utf-8")
        cls.screensaver = (ROOT / "app" / "static" / "screensaver.js").read_text(encoding="utf-8")
        cls.macro = (ROOT / "app" / "templates" / "_screensaver.html").read_text(encoding="utf-8")

    def test_presets_load_after_the_screensaver_and_fill_menu_and_settings(self):
        self.assertLess(self.macro.index("filename='screensaver.js'"), self.macro.index("filename='screensaver-presets.js'"))
        self.assertIn("data-screensaver-presets-menu", self.macro)
        self.assertIn('id="settings-screensaver-presets"', self.macro)

    def test_the_running_screensaver_asks_the_presets_before_waking_up(self):
        self.assertIn("window.journlScreensaverPresets?.handleKey(event)", self.screensaver)
        self.assertIn("journl:screensaver-opened", self.screensaver)
        self.assertIn("[data-screensaver-keep]", self.screensaver)

    def test_altgr_never_counts_as_a_preset_shortcut(self):
        # Die Tasten sind umbelegbar und laufen deshalb ueber `combo` des
        # Players, das AltGr ausschliesst.
        player = (ROOT / "app" / "static" / "screensaver-player.js").read_text(encoding="utf-8")
        self.assertIn("journlScreensaverPlayer?.combo?.(event)", self.source)
        self.assertIn("getModifierState?.('AltGraph')", player)

    def test_a_deep_link_is_consumed_once(self):
        self.assertIn("get('screensaver')", self.source)
        self.assertIn("url.searchParams.delete('screensaver')", self.source)


if __name__ == "__main__":
    unittest.main()
