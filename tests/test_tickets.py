import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

CONFIG_BASE = {"endpoint": "openrouter", "tool": "opencode", "model": "m"}


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "tickets-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-tickets-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import tickets


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class TicketApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.temp.name) / "inbox"
        self.inbox.mkdir()
        self.previous = os.environ.get("TICKETS_DIR")
        os.environ["TICKETS_DIR"] = str(self.inbox)
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        self.user = {"id": "user-1", "username": "admin", "admin": True}
        self.original_current_user = main._current_user

    def tearDown(self):
        main._current_user = self.original_current_user
        if self.previous is None:
            os.environ.pop("TICKETS_DIR", None)
        else:
            os.environ["TICKETS_DIR"] = self.previous
        self.temp.cleanup()

    def _login(self, user=None):
        active = self.user if user is None else user
        main._current_user = lambda: active
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        return {"X-CSRF-Token": "token"}

    def _submit(self, content="Erste Zeile\n\nMehr Text", headers=None):
        return self.client.post(
            "/api/tickets",
            json={"content": content},
            headers=headers if headers is not None else self._login(),
        )

    # ─── Zugriff ────────────────────────────────────────────────────
    def test_anonymous_is_unauthorized(self):
        main._current_user = lambda: None
        self.assertEqual(self.client.get("/api/tickets").status_code, 401)

    def test_non_admin_is_forbidden(self):
        headers = self._login({"id": "user-2", "username": "gast", "admin": False})
        self.assertEqual(self.client.get("/api/tickets").status_code, 403)
        self.assertEqual(self._submit(headers=headers).status_code, 403)

    def test_endpoints_are_absent_without_configured_directory(self):
        self._login()
        os.environ.pop("TICKETS_DIR", None)
        self.assertEqual(self.client.get("/api/tickets").status_code, 404)
        self.assertEqual(self._submit(headers=self._login()).status_code, 404)

    def test_config_reports_availability(self):
        self._login()
        self.assertTrue(self.client.get("/api/tickets/config").get_json()["available"])
        self._login({"id": "user-2", "username": "gast", "admin": False})
        self.assertFalse(self.client.get("/api/tickets/config").get_json()["available"])

    def test_submission_requires_csrf_token(self):
        main._current_user = lambda: self.user
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.assertEqual(self._submit(headers={}).status_code, 403)

    # ─── Einreichen ─────────────────────────────────────────────────
    def test_submission_writes_markdown_and_sidecar(self):
        response = self._submit()
        self.assertEqual(response.status_code, 201)
        ticket_id = response.get_json()["id"]

        markdown = self.inbox / f"{ticket_id}.md"
        sidecar = self.inbox / f"{ticket_id}.json"
        self.assertTrue(markdown.is_file())
        self.assertTrue(sidecar.is_file())
        self.assertIn("Mehr Text", markdown.read_text(encoding="utf-8"))

        item = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(item["state"], "neu")
        self.assertEqual(item["schema"], tickets.SCHEMA_VERSION)
        self.assertEqual(item["title"], "Erste Zeile")
        self.assertEqual(item["created_by"], "user-1")
        self.assertEqual(item["processing"], [])

    def test_submission_rejects_empty_and_oversized_content(self):
        headers = self._login()
        self.assertEqual(self._submit(content="   ", headers=headers).status_code, 400)
        too_large = "x" * (tickets.MAX_TICKET_BYTES + 1)
        self.assertEqual(self._submit(content=too_large, headers=headers).status_code, 413)

    def test_no_temporary_files_remain(self):
        self._submit()
        self.assertEqual([path.name for path in self.inbox.glob(".*")], [])

    # ─── Auflistung ─────────────────────────────────────────────────
    def test_listing_skips_broken_sidecar_without_failing(self):
        self._submit()
        (self.inbox / "20260823-120000-abcdef01.json").write_text("{kaputt", encoding="utf-8")
        payload = self.client.get("/api/tickets").get_json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["broken"], 1)

    def test_listing_ignores_foreign_file_names(self):
        self._submit()
        (self.inbox / "notizen.json").write_text('{"state": "neu"}', encoding="utf-8")
        self.assertEqual(len(self.client.get("/api/tickets").get_json()["items"]), 1)

    def test_listing_reports_missing_markdown(self):
        ticket_id = self._submit().get_json()["id"]
        (self.inbox / f"{ticket_id}.md").unlink()
        item = self.client.get("/api/tickets").get_json()["items"][0]
        self.assertTrue(item["content_missing"])

    # ─── Zustandswechsel ────────────────────────────────────────────
    CONFIG = {"endpoint": "openrouter", "tool": "opencode",
              "model": "deepseek/deepseek-v4-flash", "effort": "medium", "mode": "read"}

    def test_release_changes_state_and_keeps_markdown_untouched(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        markdown = self.inbox / f"{ticket_id}.md"
        before = markdown.read_text(encoding="utf-8")

        response = self.client.post(
            f"/api/tickets/{ticket_id}/state",
            json={"state": "freigegeben", "config": self.CONFIG}, headers=headers)
        self.assertEqual(response.status_code, 200)

        item = json.loads((self.inbox / f"{ticket_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(item["state"], "freigegeben")
        self.assertEqual(item["config"], self.CONFIG)
        self.assertTrue(item["released_at"])
        # Regel 2 des Verarbeitungsvertrags: der Inhalt bleibt unangetastet.
        self.assertEqual(markdown.read_text(encoding="utf-8"), before)

    def test_release_without_configuration_is_refused(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        for config in (None, {}, {"endpoint": "openrouter"},
                       {**CONFIG_BASE, "mode": "execute"}):
            response = self.client.post(
                f"/api/tickets/{ticket_id}/state",
                json={"state": "freigegeben", "config": config}, headers=headers)
            self.assertEqual(response.status_code, 400, config)
        item = json.loads((self.inbox / f"{ticket_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(item["state"], "neu")

    def test_other_states_need_no_configuration(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        response = self.client.post(
            f"/api/tickets/{ticket_id}/state", json={"state": "verworfen"}, headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_failed_run_is_not_reset_by_the_browser_silently(self):
        """`fehlgeschlagen` bleibt liegen, ist aber bewusst neu freigebbar."""
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        sidecar = self.inbox / f"{ticket_id}.json"
        item = json.loads(sidecar.read_text(encoding="utf-8"))
        item["state"] = "fehlgeschlagen"
        sidecar.write_text(json.dumps(item), encoding="utf-8")

        response = self.client.post(
            f"/api/tickets/{ticket_id}/state",
            json={"state": "freigegeben", "config": self.CONFIG}, headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_unknown_state_is_rejected(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        response = self.client.post(
            f"/api/tickets/{ticket_id}/state", json={"state": "in_arbeit"}, headers=headers)
        self.assertEqual(response.status_code, 400)

    def test_ticket_in_progress_is_not_changed_by_the_browser(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        sidecar = self.inbox / f"{ticket_id}.json"
        item = json.loads(sidecar.read_text(encoding="utf-8"))
        item["state"] = "in_arbeit"
        sidecar.write_text(json.dumps(item), encoding="utf-8")

        response = self.client.post(
            f"/api/tickets/{ticket_id}/state", json={"state": "erledigt"}, headers=headers)
        self.assertEqual(response.status_code, 409)

    # ─── Anhaenge ───────────────────────────────────────────────────
    def _session_with_media(self, statuses=("ready",)):
        """Legt eine aktive Schreibsession mit Medien auf der Platte an."""
        import write_sessions

        session_id = "11111111-2222-3333-4444-555555555555"
        session_dir = Path(self.temp.name) / "sessions" / session_id
        (session_dir / "media").mkdir(parents=True)
        session = {
            "id": session_id, "user_id": self.user["id"], "status": "active",
            "content": "Text", "expires_at": None,
        }
        (session_dir / "session.json").write_text(json.dumps(session), encoding="utf-8")
        for index, status in enumerate(statuses, start=1):
            media_id = f"media-{index}"
            media_dir = session_dir / "media" / media_id
            media_dir.mkdir()
            (media_dir / "original.png").write_bytes(b"bild")
            (media_dir / "metadata.json").write_text(json.dumps({
                "id": media_id, "session_id": session_id, "user_id": self.user["id"],
                "type": "image", "status": status, "original_name": "original.png",
                "captured_at": f"2026-08-23T10:0{index}:00+02:00",
            }), encoding="utf-8")

        self._patched = [
            (write_sessions, "_load_session", write_sessions._load_session),
            (write_sessions, "_consume_inbox_attachment", write_sessions._consume_inbox_attachment),
        ]
        write_sessions._load_session = lambda uid, sid: (
            (json.loads((session_dir / "session.json").read_text(encoding="utf-8")), session_dir)
            if sid == session_id and uid == self.user["id"] else (None, None))
        write_sessions._consume_inbox_attachment = lambda uid, item: None
        self.addCleanup(lambda: [setattr(module, name, value) for module, name, value in self._patched])
        return session_id, session_dir

    def test_attachments_are_copied_and_linked(self):
        headers = self._login()
        session_id, session_dir = self._session_with_media(("ready", "ready"))
        response = self.client.post(
            "/api/tickets", json={"content": "Bug mit Screenshot", "session_id": session_id}, headers=headers)
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["attachments"], 2)

        ticket_id = payload["id"]
        attachment_dir = self.inbox / ticket_id
        self.assertEqual(sorted(path.name for path in attachment_dir.iterdir()),
                         ["01-image.png", "02-image.png"])

        markdown = (self.inbox / f"{ticket_id}.md").read_text(encoding="utf-8")
        self.assertIn("## Anhänge", markdown)
        self.assertIn(f"![01-image.png]({ticket_id}/01-image.png)", markdown)

        item = json.loads((self.inbox / f"{ticket_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(item["attachments"], ["01-image.png", "02-image.png"])

    def test_session_is_closed_and_media_removed_after_transfer(self):
        headers = self._login()
        session_id, session_dir = self._session_with_media()
        self.client.post(
            "/api/tickets", json={"content": "Mit Anhang", "session_id": session_id}, headers=headers)

        session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(session["status"], "submitted")
        self.assertTrue(session["submission_target"].startswith("ticket:"))
        # Die Medien duerfen nicht doppelt liegen bleiben.
        self.assertFalse((session_dir / "media").exists())

    def test_unfinished_attachment_blocks_the_ticket(self):
        headers = self._login()
        session_id, _ = self._session_with_media(("uploading",))
        response = self.client.post(
            "/api/tickets", json={"content": "Noch am Hochladen", "session_id": session_id}, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_unknown_session_does_not_create_a_ticket(self):
        headers = self._login()
        response = self.client.post(
            "/api/tickets", json={"content": "Text", "session_id": "fremd"}, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_ticket_without_session_stays_text_only(self):
        ticket_id = self._submit().get_json()["id"]
        self.assertNotIn("## Anhänge", (self.inbox / f"{ticket_id}.md").read_text(encoding="utf-8"))
        self.assertFalse((self.inbox / ticket_id).exists())

    # ─── Protokoll und Bericht ──────────────────────────────────────
    def test_log_and_report_are_readable_in_the_browser(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        (self.inbox / f"{ticket_id}.protokoll.md").write_text(
            "## Lauf 2026-08-23T14:00:00+02:00 — erfolgreich\n", encoding="utf-8")
        (self.inbox / f"{ticket_id}.bericht.md").write_text("Alles geprüft.\n", encoding="utf-8")

        log = self.client.get(f"/api/tickets/{ticket_id}/protokoll").get_json()
        self.assertIn("erfolgreich", log["text"])
        self.assertFalse(log["truncated"])
        report = self.client.get(f"/api/tickets/{ticket_id}/bericht").get_json()
        self.assertIn("Alles geprüft", report["text"])

    def test_a_long_log_is_truncated_from_the_front(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        limit = tickets.SIDE_FILES["protokoll"][1]
        (self.inbox / f"{ticket_id}.protokoll.md").write_text(
            "A" * limit + "ENDE", encoding="utf-8")
        payload = self.client.get(f"/api/tickets/{ticket_id}/protokoll").get_json()
        self.assertTrue(payload["truncated"])
        # Das Ende ist bei einem Protokoll die interessante Seite.
        self.assertTrue(payload["text"].endswith("ENDE"))

    def test_missing_log_reports_not_found(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        self.assertEqual(
            self.client.get(f"/api/tickets/{ticket_id}/protokoll").status_code, 404)

    def test_unknown_side_file_is_refused(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        (self.inbox / "passwd").write_text("geheim", encoding="utf-8")
        self.assertEqual(
            self.client.get(f"/api/tickets/{ticket_id}/passwd").status_code, 404)

    def test_side_files_need_admin_rights(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        (self.inbox / f"{ticket_id}.bericht.md").write_text("intern", encoding="utf-8")
        self._login({"id": "user-2", "username": "gast", "admin": False})
        self.assertEqual(
            self.client.get(f"/api/tickets/{ticket_id}/bericht").status_code, 403)

    # ─── Review-Entscheidung ────────────────────────────────────────
    def _in_review(self, headers):
        ticket_id = self._submit(headers=headers).get_json()["id"]
        sidecar = self.inbox / f"{ticket_id}.json"
        item = json.loads(sidecar.read_text(encoding="utf-8"))
        item["state"] = "pruefung"
        item["processing"] = [{"branch": f"ticket/{ticket_id}", "files": ["app/main.py"],
                               "commit": "abc123", "changed": True}]
        sidecar.write_text(json.dumps(item), encoding="utf-8")
        return ticket_id

    def test_review_request_is_queued_for_the_host_worker(self):
        headers = self._login()
        ticket_id = self._in_review(headers)
        response = self.client.post(
            f"/api/tickets/{ticket_id}/review", json={"action": "merge"}, headers=headers)
        self.assertEqual(response.status_code, 200)

        item = json.loads((self.inbox / f"{ticket_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(item["requested_action"], "merge")
        # Der Zustand wechselt erst, wenn der Worker die git-Arbeit erledigt hat.
        self.assertEqual(item["state"], "pruefung")

    def test_review_only_applies_to_tickets_awaiting_a_decision(self):
        headers = self._login()
        ticket_id = self._submit(headers=headers).get_json()["id"]
        response = self.client.post(
            f"/api/tickets/{ticket_id}/review", json={"action": "merge"}, headers=headers)
        self.assertEqual(response.status_code, 409)

    def test_unknown_review_action_is_refused(self):
        headers = self._login()
        ticket_id = self._in_review(headers)
        response = self.client.post(
            f"/api/tickets/{ticket_id}/review", json={"action": "push"}, headers=headers)
        self.assertEqual(response.status_code, 400)

    def test_review_needs_admin_and_csrf(self):
        headers = self._login()
        ticket_id = self._in_review(headers)
        self.assertEqual(self.client.post(
            f"/api/tickets/{ticket_id}/review", json={"action": "merge"}).status_code, 403)
        self._login({"id": "user-2", "username": "gast", "admin": False})
        self.assertEqual(self.client.post(
            f"/api/tickets/{ticket_id}/review", json={"action": "merge"},
            headers={"X-CSRF-Token": "token"}).status_code, 403)

    def test_listing_exposes_changed_files_and_pending_decision(self):
        headers = self._login()
        ticket_id = self._in_review(headers)
        self.client.post(f"/api/tickets/{ticket_id}/review",
                         json={"action": "discard"}, headers=headers)
        item = next(entry for entry in self.client.get("/api/tickets").get_json()["items"]
                    if entry["id"] == ticket_id)
        self.assertEqual(item["files"], ["app/main.py"])
        self.assertEqual(item["requested_action"], "discard")

    def test_path_traversal_in_identifier_is_refused(self):
        headers = self._login()
        response = self.client.post(
            "/api/tickets/..%2F..%2Fetc%2Fpasswd/state", json={"state": "freigegeben"}, headers=headers)
        self.assertIn(response.status_code, (400, 404))


if __name__ == "__main__":
    unittest.main()
