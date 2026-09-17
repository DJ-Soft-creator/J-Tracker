"""Sammelmappen: Themenmappen und die Sicht auf die Tagesablage."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "buckets-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-buckets-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import brain
    import buckets


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class BucketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.previous = main.DATA_DIR
        main.DATA_DIR = self.data
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        self.user = {"id": "user-1", "username": "david", "admin": True}
        self.original_current_user = main._current_user
        main._current_user = lambda: self.user
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.headers = {"X-CSRF-Token": "token"}

    def tearDown(self):
        main.DATA_DIR = self.previous
        main._current_user = self.original_current_user
        self.temp.cleanup()

    def _create(self, title="Ticket-Kette umbauen"):
        return self.client.post("/api/buckets", json={"title": title}, headers=self.headers)

    # ─── Themenmappen ───────────────────────────────────────────────
    def test_a_bucket_is_markdown_plus_sidecar(self):
        response = self._create()
        self.assertEqual(response.status_code, 201)
        slug = response.get_json()["slug"]
        self.assertEqual(slug, "ticket-kette-umbauen")

        directory = self.data / "user-1" / "mappen"
        self.assertTrue((directory / f"{slug}.md").is_file())
        item = json.loads((directory / f"{slug}.json").read_text(encoding="utf-8"))
        self.assertEqual(item["title"], "Ticket-Kette umbauen")
        self.assertEqual(item["items"], [])

    def test_umlauts_become_a_usable_slug(self):
        slug = self._create("Übergreifende Prüfung").get_json()["slug"]
        self.assertEqual(slug, "uebergreifende-pruefung")

    def test_a_duplicate_name_is_refused(self):
        self._create()
        self.assertEqual(self._create().status_code, 409)

    def test_a_name_without_usable_characters_is_refused(self):
        self.assertEqual(self._create("!!!").status_code, 400)

    def test_creating_needs_csrf(self):
        self.assertEqual(
            self.client.post("/api/buckets", json={"title": "Ohne Token"}).status_code, 403)

    # ─── Zuordnung ──────────────────────────────────────────────────
    def test_a_journal_entry_is_stored_as_a_reference(self):
        slug = self._create().get_json()["slug"]
        response = self.client.post(f"/api/buckets/{slug}/items",
                                    json={"date": "2026-08-22", "note": "Screenshot-Problem",
                                          "anchor": "block-3"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)

        payload = self.client.get(f"/api/buckets/{slug}").get_json()
        self.assertEqual(payload["items"], 1)
        entry = payload["entries"][0]
        self.assertEqual(entry["date"], "2026-08-22")
        self.assertEqual(entry["kind"], "journal")
        # Ein Verweis, keine Kopie des Inhalts.
        self.assertNotIn("content", entry)
        self.assertIn("2026-08-22", payload["text"])

    def test_the_same_entry_is_not_added_twice(self):
        slug = self._create().get_json()["slug"]
        for _ in range(2):
            self.client.post(f"/api/buckets/{slug}/items",
                             json={"date": "2026-08-22", "anchor": "block-3"},
                             headers=self.headers)
        self.assertEqual(self.client.get(f"/api/buckets/{slug}").get_json()["items"], 1)

    def test_an_invalid_date_is_refused(self):
        slug = self._create().get_json()["slug"]
        response = self.client.post(f"/api/buckets/{slug}/items",
                                    json={"date": "gestern"}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_a_foreign_slug_is_refused(self):
        response = self.client.get("/api/buckets/..%2F..%2Fetc")
        self.assertIn(response.status_code, (404, 400))

    # ─── Verweis aufloesen ──────────────────────────────────────────
    def _journal(self, day, entries):
        year, month, date = day.split("-")
        directory = self.data / "user-1" / year / month / date
        directory.mkdir(parents=True, exist_ok=True)
        # Layout wie im echten Journal: der Kopf steht fuer sich, jeder
        # Eintrag haengt hinter einem eigenen ``___``-Trenner.
        body = f"# Journal {day}\n\n" + "".join(f"___\n\n{entry}\n\n" for entry in entries)
        (directory / f"Journal_{day}.md").write_text(body, encoding="utf-8")

    def _reference(self, slug, day, anchor, note=""):
        return self.client.post(f"/api/buckets/{slug}/items",
                                json={"date": day, "anchor": anchor, "note": note},
                                headers=self.headers)

    def test_the_anchor_hash_matches_the_browser(self):
        # Werte aus simpleHash() in *-app-core.js — der Anker muss auf beiden
        # Seiten gleich gerechnet werden, sonst findet die Mappe nichts.
        self.assertEqual(buckets.anchor_hash("Erster Eintrag"), "62a69049")
        self.assertEqual(buckets.anchor_hash("Emoji \U0001F389 dabei"), "6a490d70")

    def test_a_reference_shows_the_journal_text(self):
        self._journal("2026-08-22", ["Erster Eintrag", "Zweiter Eintrag mit Text"])
        slug = self._create().get_json()["slug"]
        # Der Heute-Tab zeigt den neuesten Eintrag oben, also Index 0.
        self._reference(slug, "2026-08-22", f"{buckets.anchor_hash('Zweiter Eintrag mit Text')}-2026-08-22-0")
        entry = self.client.get(f"/api/buckets/{slug}").get_json()["entries"][0]
        self.assertTrue(entry["found"])
        self.assertEqual(entry["excerpt"], "Zweiter Eintrag mit Text")
        self.assertEqual(entry["file"], "Journal_2026-08-22.md")

    def test_a_reference_survives_a_later_entry(self):
        """Ein spaeter geschriebener Eintrag verschiebt den Index, nicht den Hash."""
        self._journal("2026-08-22", ["Erster Eintrag"])
        slug = self._create().get_json()["slug"]
        self._reference(slug, "2026-08-22", f"{buckets.anchor_hash('Erster Eintrag')}-2026-08-22-0")
        self._journal("2026-08-22", ["Erster Eintrag", "Danach geschrieben"])
        entry = self.client.get(f"/api/buckets/{slug}").get_json()["entries"][0]
        self.assertTrue(entry["found"])
        self.assertEqual(entry["excerpt"], "Erster Eintrag")

    def test_a_vanished_entry_stays_visible_with_its_note(self):
        self._journal("2026-08-22", ["Erster Eintrag"])
        slug = self._create().get_json()["slug"]
        self._reference(slug, "2026-08-22", "deadbeef-2026-08-22-0", note="Screenshot-Problem")
        entry = self.client.get(f"/api/buckets/{slug}").get_json()["entries"][0]
        self.assertFalse(entry["found"])
        self.assertEqual(entry["excerpt"], "")
        self.assertEqual(entry["note"], "Screenshot-Problem")

    # ─── Entfernen ──────────────────────────────────────────────────
    def test_a_reference_can_be_removed_without_touching_the_journal(self):
        self._journal("2026-08-22", ["Erster Eintrag"])
        slug = self._create().get_json()["slug"]
        anchor = f"{buckets.anchor_hash('Erster Eintrag')}-2026-08-22-0"
        entry_id = self._reference(slug, "2026-08-22", anchor, note="Notiz").get_json()["id"]
        response = self.client.delete(f"/api/buckets/{slug}/items/{entry_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f"/api/buckets/{slug}").get_json()["entries"], [])
        markdown = (self.data / "user-1" / "mappen" / f"{slug}.md").read_text(encoding="utf-8")
        self.assertNotIn("[[2026-08-22]]", markdown)
        journal = self.data / "user-1" / "2026" / "08" / "22" / "Journal_2026-08-22.md"
        self.assertIn("Erster Eintrag", journal.read_text(encoding="utf-8"))

    def test_removing_a_reference_needs_csrf_and_a_known_id(self):
        slug = self._create().get_json()["slug"]
        entry_id = self._reference(slug, "2026-08-22", "deadbeef-2026-08-22-0").get_json()["id"]
        self.assertEqual(self.client.delete(f"/api/buckets/{slug}/items/{entry_id}").status_code, 403)
        self.assertEqual(self.client.delete(
            f"/api/buckets/{slug}/items/ffffffffffff", headers=self.headers).status_code, 404)

    def test_an_old_reference_gets_an_id_when_it_is_read(self):
        """Verweise der ersten Fassung haben keine Id — ohne sie kein Loeschen."""
        slug = self._create().get_json()["slug"]
        sidecar = self.data / "user-1" / "mappen" / f"{slug}.json"
        item = json.loads(sidecar.read_text(encoding="utf-8"))
        item["items"] = [{"kind": "journal", "date": "2026-08-22", "anchor": "x", "note": "alt"}]
        sidecar.write_text(json.dumps(item), encoding="utf-8")
        entry = self.client.get(f"/api/buckets/{slug}").get_json()["entries"][0]
        self.assertRegex(entry["id"], r"^[0-9a-f]{12}$")
        self.assertEqual(self.client.delete(
            f"/api/buckets/{slug}/items/{entry['id']}", headers=self.headers).status_code, 200)

    def test_a_bucket_can_be_deleted(self):
        slug = self._create().get_json()["slug"]
        response = self.client.delete(f"/api/buckets/{slug}", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        directory = self.data / "user-1" / "mappen"
        self.assertFalse((directory / f"{slug}.json").exists())
        self.assertFalse((directory / f"{slug}.md").exists())
        self.assertEqual(self.client.get("/api/buckets").get_json()["items"], [])

    def test_deleting_a_bucket_needs_csrf_and_an_existing_mappe(self):
        slug = self._create().get_json()["slug"]
        self.assertEqual(self.client.delete(f"/api/buckets/{slug}").status_code, 403)
        self.assertEqual(self.client.delete("/api/buckets/gibtsnicht",
                                            headers=self.headers).status_code, 404)

    # ─── Brain-Verweise ─────────────────────────────────────────────
    def test_a_brain_block_is_stored_and_resolved(self):
        slug = self._create().get_json()["slug"]
        block = {"fingerprint": "abc123", "text": "Gedanke aus einer Notiz"}
        document = {"doc_id": "personal:notes/idee.md", "path": "notes/idee.md",
                    "blocks": [block]}
        original = buckets._brain_block
        buckets._brain_block = lambda user_id, doc_id, fingerprint: (
            {"content": block["text"], "file": document["path"], "media": 0}
            if doc_id == document["doc_id"] and fingerprint == block["fingerprint"] else None)
        try:
            response = self.client.post(
                f"/api/buckets/{slug}/items",
                json={"kind": "brain", "doc_id": document["doc_id"],
                      "fingerprint": block["fingerprint"], "path": document["path"],
                      "note": "Gedanke aus einer Notiz"},
                headers=self.headers)
            self.assertEqual(response.status_code, 200)
            entry = self.client.get(f"/api/buckets/{slug}").get_json()["entries"][0]
            self.assertEqual(entry["kind"], "brain")
            self.assertTrue(entry["found"])
            self.assertEqual(entry["excerpt"], "Gedanke aus einer Notiz")
            # Zweimal derselbe Block bleibt ein Verweis.
            again = self.client.post(
                f"/api/buckets/{slug}/items",
                json={"kind": "brain", "doc_id": document["doc_id"],
                      "fingerprint": block["fingerprint"]}, headers=self.headers)
            self.assertTrue(again.get_json()["duplicate"])
        finally:
            buckets._brain_block = original
        markdown = (self.data / "user-1" / "mappen" / f"{slug}.md").read_text(encoding="utf-8")
        self.assertIn("- notes/idee.md — Gedanke aus einer Notiz", markdown)

    def test_a_brain_block_resolves_through_the_real_brain_facade(self):
        """Der Verweis muss die echte Brain-Aufloesung treffen, nicht nur einen Stub."""
        previous = (brain.DATA_DIR, brain.FAMILY_DIR)
        brain.DATA_DIR = self.data
        brain.FAMILY_DIR = self.data / "family"
        try:
            note = self.data / "user-1" / "notes" / "idee.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("# Idee\n\nGedanke aus einer Notiz\n", encoding="utf-8")
            document, _, _, _ = brain._resolve_document("user-1", "personal:notes/idee.md")
            self.assertIsNotNone(document, "Brain findet die Notiz nicht — Fixture pruefen")
            fingerprint = document["blocks"][0]["fingerprint"]
            block = buckets._brain_block("user-1", "personal:notes/idee.md", fingerprint)
            self.assertIsNotNone(block)
            self.assertIn("Gedanke aus einer Notiz", block["content"])
            self.assertEqual(block["file"], "notes/idee.md")
            self.assertIsNone(buckets._brain_block("user-1", "personal:notes/idee.md", "nichts"))
        finally:
            brain.DATA_DIR, brain.FAMILY_DIR = previous

    def test_a_brain_reference_without_identifiers_is_refused(self):
        slug = self._create().get_json()["slug"]
        response = self.client.post(f"/api/buckets/{slug}/items",
                                    json={"kind": "brain", "doc_id": ""}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    # ─── Tagesablage ────────────────────────────────────────────────
    def _collected(self, day, identifier):
        year, month, date = day.split("-")
        directory = self.data / "user-1" / year / month / date / "media" / "Tagesablage"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{identifier}.json").write_text(json.dumps({
            "id": identifier, "type": "image", "status": "collected",
            "captured_at": f"{day}T10:00:00+02:00", "collected_at": f"{day}T23:59:00+02:00",
            "origin": "Screenshot-Inbox",
        }), encoding="utf-8")

    def test_day_buckets_are_listed_newest_first(self):
        self._collected("2026-08-20", "a")
        self._collected("2026-08-22", "b")
        days = self.client.get("/api/buckets/tagesablage").get_json()["days"]
        self.assertEqual([entry["date"] for entry in days], ["2026-08-22", "2026-08-20"])

    def test_a_single_day_can_be_read_back(self):
        self._collected("2026-08-22", "b")
        payload = self.client.get("/api/buckets/tagesablage/2026-08-22").get_json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["origin"], "Screenshot-Inbox")

    def _collected_image(self, day, identifier):
        """Ein eingesammeltes Medium samt Dateien, wie der Sammler es ablegt."""
        year, month, date = day.split("-")
        directory = self.data / "user-1" / year / month / date / "media" / "Tagesablage"
        directory.mkdir(parents=True, exist_ok=True)
        basename = f"{day}_10-00-00_{identifier}"
        (directory / f"{basename}.png").write_bytes(b"originalbytes")
        (directory / f"{basename}_preview.jpg").write_bytes(b"previewbytes")
        (directory / f"{basename}.json").write_text(json.dumps({
            "id": identifier, "type": "image", "status": "collected",
            "captured_at": f"{day}T10:00:00+02:00", "collected_at": f"{day}T23:59:00+02:00",
            "origin": "Screenshot-Inbox", "mime_type": "image/png",
            "original_filename": "Screenshot.png",
            # Der Sammler verschiebt die Dateien, laesst den Pfad im Sidecar
            # aber auf die Inbox zeigen — der Dateiname ist massgeblich.
            "source_path": f"{year}/{month}/{date}/media/Screenshot-Inbox/{basename}.png",
            "preview_path": f"{year}/{month}/{date}/media/Screenshot-Inbox/{basename}_preview.jpg",
        }), encoding="utf-8")
        return identifier

    def test_a_collected_medium_can_be_viewed(self):
        self._collected_image("2026-08-22", "a1b2c3d4-0000-4000-8000-000000000001")
        payload = self.client.get("/api/buckets/tagesablage/2026-08-22").get_json()
        item = payload["items"][0]
        self.assertTrue(item["has_preview"])
        self.assertEqual(item["original_filename"], "Screenshot.png")
        url = f"/api/buckets/tagesablage/2026-08-22/{item['id']}/preview"
        self.assertEqual(self.client.get(url).data, b"previewbytes")
        self.assertEqual(self.client.get(url + "?full=1").data, b"originalbytes")

    def test_an_unknown_medium_is_refused(self):
        self._collected_image("2026-08-22", "a1b2c3d4-0000-4000-8000-000000000001")
        self.assertEqual(self.client.get(
            "/api/buckets/tagesablage/2026-08-22/a1b2c3d4-0000-4000-8000-000000000009/preview"
        ).status_code, 404)
        self.assertEqual(self.client.get(
            "/api/buckets/tagesablage/2026-08-22/..%2F..%2Fetc/preview").status_code, 404)

    def test_buckets_require_a_session(self):
        main._current_user = lambda: None
        self.assertEqual(self.client.get("/api/buckets").status_code, 401)


if __name__ == "__main__":
    unittest.main()
