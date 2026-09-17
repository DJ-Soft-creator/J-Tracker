"""Attachments added while an existing Today entry or Brain note is edited.

The editor reuses a write session purely as a carrier for the uploads; the save
of the source file is what turns those uploads into permanent journal media.
"""

import base64
import io
import unittest
from datetime import datetime

try:
    from .brain_api_case import BrainApiCase
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PLACEHOLDER = "<!-- jt:media-placeholder -->"
ENTRY = "## Thema: Time:10:00:00\nVisible journal text\n  - [ ] Personal task #work"


class WriteSourceEditAttachmentTests(BrainApiCase):
    def _carrier_session(self):
        response = self.client.post(
            "/api/write-sessions", headers=self.headers,
            json={"content": "", "template_id": "schnell"},
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["id"]

    def _attach_document(self, session_id, name="Notizen.pdf", captured="2026-08-03T11:30:00+02:00"):
        response = self.client.post(
            f"/api/write-sessions/{session_id}/documents", headers=self.headers,
            data={"captured_at": captured, "file": (io.BytesIO(b"private document"), name, "application/pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["id"]

    def _attach_photo(self, session_id, captured="2026-08-03T11:31:00+02:00"):
        response = self.client.post(
            f"/api/write-sessions/{session_id}/images", headers=self.headers,
            data={"captured_at": captured, "file": (io.BytesIO(PNG), "foto.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["id"]

    def _edit_entry(self, new_content, session_id=None, old_content=ENTRY):
        return self.client.post("/api/dashboard/edit", headers=self.headers, json={
            "content": old_content, "new_content": new_content,
            "filename": "Journal_2026-08-03.md", "journal_date": "2026-08-03",
            "write_session_id": session_id,
        })

    def _document(self, doc_id):
        response = self.client.get(f"/api/brain/document?doc_id={doc_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_editing_a_day_keeps_the_machine_tags_of_its_quick_notes(self):
        import tagging

        path = self.data_dir / "user-a/2026/08/03/Journal_2026-08-03.md"
        path.write_text(
            path.read_text(encoding="utf-8").rstrip()
            + "\n\n___\n\n- 03.08. 09:15:00\nSchnell notiert\n~~03.08. 09:16:00~~\n\n___\n",
            encoding="utf-8",
        )
        stamped = tagging.refresh_journal_footer(
            "user-a", path.read_text(encoding="utf-8"), "2026-08-03", {
                "blocks": {"2026-08-03 09:15:00": ["maschine"]},
                "ai": {"2026-08-03 09:15:00": ["maschine"]},
                "ai_run": "2026-08-03T10:00:00+00:00",
            },
        )
        path.write_text(stamped, encoding="utf-8")

        response = self._edit_entry(f"{ENTRY}\nErgänzt")

        self.assertEqual(response.status_code, 200)
        _, footer = tagging.strip_footer(path.read_text(encoding="utf-8"))
        self.assertEqual(footer["ai"], {"2026-08-03 09:15:00": ["maschine"]})

    def test_today_entry_keeps_its_new_attachment_inside_the_edited_block(self):
        session_id = self._carrier_session()
        media_id = self._attach_document(session_id)

        response = self._edit_entry(f"{ENTRY}\nErgänzt\n\n{PLACEHOLDER}", session_id)

        self.assertEqual(response.status_code, 200)
        journal = self.journal_path.read_text(encoding="utf-8")
        self.assertNotIn(PLACEHOLDER, journal)
        self.assertIn("Ergänzt", journal)
        self.assertIn(f'"media_path":"2026/08/03/media/2026-08-03_11-30-00_{media_id}.pdf"', journal)
        self.assertIn("Dokument | Aufnahmezeit: 2026-08-03T11:30:00+02:00", journal)
        entry_body = journal.split("___")[1]
        self.assertIn("jt:media", entry_body)
        final = self.data_dir / "user-a" / "2026" / "08" / "03" / "media" / f"2026-08-03_11-30-00_{media_id}.pdf"
        self.assertEqual(final.read_bytes(), b"private document")
        self.assertFalse((self.data_dir / "user-a" / "write_sessions" / session_id / "media").exists())
        listed = self.client.get("/api/write-sessions").get_json()["sessions"]
        self.assertNotIn(session_id, [item["id"] for item in listed])

    def test_edit_without_attachments_never_leaves_a_placeholder_behind(self):
        response = self._edit_entry(f"{ENTRY}\nNur Text\n\n{PLACEHOLDER}")

        self.assertEqual(response.status_code, 200)
        journal = self.journal_path.read_text(encoding="utf-8")
        self.assertNotIn(PLACEHOLDER, journal)
        self.assertIn("Nur Text", journal)

    def test_entry_changed_in_the_background_returns_the_media_to_its_session(self):
        session_id = self._carrier_session()
        media_id = self._attach_document(session_id)
        original = (self.data_dir / "user-a" / "write_sessions" / session_id
                    / "media" / media_id / "original.pdf")

        response = self._edit_entry(f"Neu\n\n{PLACEHOLDER}", session_id, old_content="Fremder Eintrag")

        self.assertEqual(response.status_code, 409)
        self.assertTrue(original.is_file())
        self.assertFalse(any((self.data_dir / "user-a").glob("*/*/*/media/*.pdf")))
        self.assertNotIn(PLACEHOLDER, self.journal_path.read_text(encoding="utf-8"))

    def test_note_outside_the_journal_tree_stores_media_in_todays_day_folder(self):
        session_id = self._carrier_session()
        media_id = self._attach_photo(session_id)
        document = self._document("personal:notes/keep.md")

        response = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": "personal:notes/keep.md",
            "content": f"{document['content']}\n{PLACEHOLDER}",
            "content_hash": document["content_hash"],
            "write_session_id": session_id,
        })

        self.assertEqual(response.status_code, 200)
        note = (self.data_dir / "user-a" / "notes" / "keep.md").read_text(encoding="utf-8")
        today = datetime.now().astimezone().strftime("%Y/%m/%d")
        self.assertNotIn(PLACEHOLDER, note)
        self.assertIn(f'"media_path":"{today}/media/2026-08-03_11-31-00_{media_id}', note)
        self.assertFalse((self.data_dir / "user-a" / "notes" / ".journal-media").exists())
        self.assertTrue((self.data_dir / "user-a" / today / ".journal-media" / f"{media_id}.json").is_file())
        stored = next((self.data_dir / "user-a" / today / "media").glob(f"*_{media_id}.png"))
        self.assertEqual(stored.read_bytes(), PNG)

    def test_edited_note_reports_its_attachment_to_the_brain_view(self):
        session_id = self._carrier_session()
        self._attach_photo(session_id)
        document = self._document("personal:notes/keep.md")
        self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": "personal:notes/keep.md",
            "content": f"{document['content']}\n{PLACEHOLDER}",
            "content_hash": document["content_hash"],
            "write_session_id": session_id,
        })

        try:
            from .brain_api_case import brain
        except ImportError:
            from brain_api_case import brain
        path = self.data_dir / "user-a" / "notes" / "keep.md"
        built = brain._build_document("personal", self.data_dir / "user-a", path, "note")
        block = next(item for item in built["blocks"] if "jt:media" in item["text"])
        result = brain._serialise_result(built, block, "user-a")

        self.assertEqual([item["type"] for item in result["media"]], ["image"])

    def test_shared_family_source_rejects_private_attachments(self):
        session_id = self._carrier_session()
        self._attach_photo(session_id)
        document = self._document("family:projects/visible.md")

        response = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": "family:projects/visible.md",
            "content": f"{document['content']}\n{PLACEHOLDER}",
            "content_hash": document["content_hash"],
            "confirm_family_edit": True,
            "write_session_id": session_id,
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("privaten", response.get_json()["error"])
        self.assertNotIn(PLACEHOLDER, (self.data_dir / "family" / "projects" / "visible.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
