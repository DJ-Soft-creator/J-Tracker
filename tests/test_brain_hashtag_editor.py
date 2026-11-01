"""Brain: alle Hashtags eines Treffers bearbeiten, KI-Hashtags dauerhaft entfernen."""

from pathlib import Path

try:
    from .brain_api_case import FLASK_AVAILABLE, BrainApiCase
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import FLASK_AVAILABLE, BrainApiCase

if FLASK_AVAILABLE:
    import brain


ROOT_DIR = Path(__file__).resolve().parents[1]


class BrainHashtagEditorTests(BrainApiCase):
    def _tag_by_model(self, path, journal_date, anchor, tags):
        """Write ``tags`` into the footer as machine hashtags, like a tagging run."""
        import tagging

        tagging.propose_tags("user-a", tags)
        body, _ = tagging.strip_footer(path.read_text(encoding="utf-8"))
        blocks = [
            {**block, "raw_tags": sorted(set(block["raw_tags"]) | set(tags))}
            if block["anchor"] == anchor else block
            for block in tagging.journal_blocks(body, journal_date)
        ]
        path.write_text(
            body.rstrip() + "\n\n" + tagging.render_footer(
                blocks, [tagging.read_catalog("user-a")], {anchor: tags},
                "2026-08-03T09:00:00+00:00", [anchor],
            ),
            encoding="utf-8",
        )
        brain.rebuild_user_index("user-a")

    def _card(self, needle):
        results = self.client.get("/api/brain/search").get_json()["results"]
        return next(item for item in results if needle in item["snippet"])

    def _reject(self, card, tags):
        return self.client.post("/api/brain/ai-tags/reject", headers=self.headers, json={
            "doc_id": card["doc_id"], "fingerprint": card["fingerprint"], "tags": tags,
        })

    def test_a_removed_model_hashtag_leaves_the_card_and_is_remembered_as_rejected(self):
        import tagging

        self._tag_by_model(self.journal_path, "2026-08-03", "2026-08-03 11:00:00", ["kifalsch", "kirichtig"])
        card = self._card("Stored note")
        self.assertEqual(card["ai_tags"], ["kifalsch", "kirichtig"])

        response = self._reject(card, ["#KIfalsch"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["removed"], ["kifalsch"])
        _, footer = tagging.strip_footer(self.journal_path.read_text(encoding="utf-8"))
        self.assertNotIn("kifalsch", footer["blocks"]["2026-08-03 11:00:00"])
        self.assertEqual(footer["ai"], {"2026-08-03 11:00:00": ["kirichtig"]})
        self.assertEqual(footer["ai_rejected"], {"2026-08-03 11:00:00": ["kifalsch"]})
        self.assertEqual(footer["ai_seen"], ["2026-08-03 11:00:00"])
        updated = self._card("Stored note")
        self.assertEqual(updated["ai_tags"], ["kirichtig"])
        self.assertNotIn("kifalsch", updated["tags"])
        # Der Text selbst bleibt unberuehrt.
        body_before, _ = tagging.strip_footer(self.journal_path.read_text(encoding="utf-8"))
        self.assertIn("Stored note", body_before)

    def test_an_ordinary_save_keeps_the_rejection(self):
        import tagging

        self._tag_by_model(self.journal_path, "2026-08-03", "2026-08-03 11:00:00", ["kifalsch"])
        self._reject(self._card("Stored note"), ["kifalsch"])

        refreshed = tagging.refresh_journal_footer(
            "user-a", self.journal_path.read_text(encoding="utf-8"), "2026-08-03",
        )

        _, footer = tagging.strip_footer(refreshed)
        self.assertEqual(footer["ai_rejected"], {"2026-08-03 11:00:00": ["kifalsch"]})
        self.assertNotIn("2026-08-03 11:00:00", footer.get("ai", {}))

    def test_a_hashtag_from_the_text_is_not_removed(self):
        card = self._card("Visible journal text")
        self.assertIn("work", card["text_tags"])

        response = self._reject(card, ["work"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["removed"], [])
        self.assertIn("#work", self.journal_path.read_text(encoding="utf-8"))

    def test_a_whole_day_journal_takes_back_its_day_hashtag(self):
        import tagging

        path = self._write("user-a/2026/07/01/Journal_2026-07-01.md", "Ein Tag ganz ohne Uhrzeit\n")
        self._tag_by_model(path, "2026-07-01", "2026-07-01 00:00:00", ["kitag"])
        card = self._card("ganz ohne Uhrzeit")
        self.assertEqual(card["ai_tags"], ["kitag"])

        response = self._reject(card, ["kitag"])

        self.assertEqual(response.get_json()["removed"], ["kitag"])
        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["ai_rejected"], {"2026-07-01 00:00:00": ["kitag"]})

    def test_invalid_requests_are_refused(self):
        card = self._card("Stored note")
        self.assertEqual(self._reject(card, []).status_code, 400)
        self.assertEqual(self._reject(card, [3]).status_code, 400)
        note = self._card("A lasting note")
        self.assertEqual(self._reject(note, ["x"]).status_code, 404)
        gone = self._reject({**card, "fingerprint": "0" * 64}, ["x"])
        self.assertEqual(gone.status_code, 409)
        no_csrf = self.client.post("/api/brain/ai-tags/reject", json={
            "doc_id": card["doc_id"], "fingerprint": card["fingerprint"], "tags": ["x"],
        })
        self.assertEqual(no_csrf.status_code, 403)

    def test_both_shells_load_the_editor_and_cards_offer_it(self):
        cards = (ROOT_DIR / "app/static/brain-cards.js").read_text(encoding="utf-8")
        editor = (ROOT_DIR / "app/static/brain-tag-editor.js").read_text(encoding="utf-8")
        self.assertIn("Alle Hashtags bearbeiten", cards)
        self.assertIn("data-brain-card-tags", cards)
        self.assertIn("/api/brain/ai-tags/reject", editor)
        self.assertIn("/api/brain/metadata", editor)
        for template in ("desktop.html", "index.html"):
            text = (ROOT_DIR / "app/templates" / template).read_text(encoding="utf-8")
            self.assertIn("filename='brain-tag-editor.js'", text)
