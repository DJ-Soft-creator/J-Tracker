"""Safety and selection behavior for Brain journal hashtag replacement."""

import hashlib
import unittest
from pathlib import Path

try:
    from .brain_api_case import BrainApiCase
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase


class BrainHashtagReplacementTests(BrainApiCase):
    def _replacement_journal(self, date, body):
        return self._write(
            f"user-a/{date[:4]}/{date[5:7]}/{date[8:]}/Journal_{date}.md",
            f"# Journal {date}\n\n___\n\n## Thema: Time:10:00:00\n{body}\n\n___\n",
        )

    def test_preview_lists_exact_personal_journal_matches_with_hashes(self):
        first = self._replacement_journal("2026-08-04", "One #Legacy and #legacy. Not #legacy-more.")
        self._replacement_journal("2026-08-05", "Only #legacy-more here.")
        self._write("user-a/notes/legacy.md", "A note with #legacy stays outside the workflow.\n")

        response = self.client.get("/api/brain/hashtag-replacement?old_tag=%23legacy")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["journal_count"], 1)
        self.assertEqual(payload["occurrence_count"], 2)
        self.assertEqual(payload["journals"][0]["path"], "2026/08/04/Journal_2026-08-04.md")
        self.assertEqual(
            payload["journals"][0]["content_hash"],
            hashlib.sha256(first.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
        )
        self.assertIn("#Legacy", payload["journals"][0]["previews"][0])

    def test_apply_changes_only_selected_journals_after_confirmation(self):
        first = self._replacement_journal("2026-08-04", "First #legacy item.")
        second = self._replacement_journal("2026-08-05", "Second #legacy item.")
        preview = self.client.get("/api/brain/hashtag-replacement?old_tag=legacy").get_json()
        selected = next(item for item in preview["journals"] if "2026-08-04" in item["path"])

        unconfirmed = self.client.post(
            "/api/brain/hashtag-replacement", headers=self.headers,
            json={"old_tag": "legacy", "new_tag": "cluster", "documents": [selected]},
        )
        applied = self.client.post(
            "/api/brain/hashtag-replacement", headers=self.headers,
            json={
                "old_tag": "legacy", "new_tag": "cluster", "confirmed": True,
                "documents": [{"doc_id": selected["doc_id"], "content_hash": selected["content_hash"]}],
            },
        )

        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(applied.status_code, 200, applied.get_json())
        self.assertEqual(applied.get_json()["journal_count"], 1)
        self.assertIn("#cluster", first.read_text(encoding="utf-8"))
        self.assertNotIn("#legacy", first.read_text(encoding="utf-8").casefold())
        self.assertIn("#legacy", second.read_text(encoding="utf-8"))
        self.assertIn("cluster", self.client.get("/api/brain/tag-catalog").get_json()["catalog"]["personal"]["canonical"])

    def test_apply_preflight_rejects_stale_selection_before_any_write(self):
        first = self._replacement_journal("2026-08-04", "First #legacy item.")
        second = self._replacement_journal("2026-08-05", "Second #legacy item.")
        preview = self.client.get("/api/brain/hashtag-replacement?old_tag=legacy").get_json()
        second.write_text(second.read_text(encoding="utf-8") + "External change.\n", encoding="utf-8")

        response = self.client.post(
            "/api/brain/hashtag-replacement", headers=self.headers,
            json={
                "old_tag": "legacy", "new_tag": "cluster", "confirmed": True,
                "documents": [
                    {"doc_id": item["doc_id"], "content_hash": item["content_hash"]}
                    for item in preview["journals"]
                ],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["changed"], [])
        self.assertIn("#legacy", first.read_text(encoding="utf-8"))

    def test_apply_requires_csrf_and_rejects_non_journal_documents(self):
        note = self.data_dir / "user-a/notes/keep.md"
        document = {
            "doc_id": "personal:notes/keep.md",
            "content_hash": hashlib.sha256(note.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
        }
        payload = {
            "old_tag": "legacy", "new_tag": "cluster", "confirmed": True,
            "documents": [document],
        }

        self.assertEqual(self.client.post("/api/brain/hashtag-replacement", json=payload).status_code, 403)
        self.assertEqual(
            self.client.post("/api/brain/hashtag-replacement", headers=self.headers, json=payload).status_code,
            400,
        )


class BrainHashtagReplacementFrontendTests(unittest.TestCase):
    def test_both_clients_load_the_selection_and_confirmation_workflow(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "app/static/brain-replace.js").read_text(encoding="utf-8")
        for template_name in ("index.html", "desktop.html"):
            template = (root / "app/templates" / template_name).read_text(encoding="utf-8")
            self.assertIn("data-brain-hashtag-replace", template)
            self.assertIn("filename='brain-replace.js'", template)
        self.assertIn("data-brain-replace-doc", script)
        self.assertIn("confirmed: true", script)
        self.assertIn("Änderung wirklich durchführen?", script)
