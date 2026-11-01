"""„Chat with your Notes": Projekte und Notes als zuschaltbare Quellen."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("PIL") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "brain-chat-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-brain-chat-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import brain
    import tagging
    import brain_chat
    import brain_chat_documents
    from mcp_tools import ToolInputError


GARDEN_ID = "11111111-1111-4111-8111-111111111111"
HIDDEN_ID = "22222222-2222-4222-8222-222222222222"

GARDEN = """---
id: {id}
title: Garten
type: projekt
template_id:
target_file:
assigned_users: []
created_at: 2026-10-01T10:00:00
created_by: user-a
exclude_from_personal: false
updated_at: 2026-10-02T10:00:00
---

Beet umgraben im Herbst.

## Aufgaben
- [ ] id: t1 | title: Erde kaufen | user: user-a | target-date: 2026-10-20
- [x] id: t2 | title: Samen bestellen | user: user-b | target-date:

## Kommentare
- id: c1 | user: user-b | text: Bitte Bio-Erde | created_at: 2026-10-03T09:00:00
""".format(id=GARDEN_ID)

HIDDEN = """---
id: {id}
title: Ueberraschungsparty
type: projekt
assigned_users: [user-b]
---

Geheimplan fuer Alex.

## Aufgaben
- [ ] id: t9 | title: Kuchen | user: user-b | target-date:
""".format(id=HIDDEN_ID)


def _completion(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _tool_call(name, arguments, call_id="call-1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class BrainChatDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.old = (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            tagging.DATA_DIR, tagging.FAMILY_DIR, tagging.INDEX_PATH, tagging._snapshot,
            brain.DATA_DIR, brain.FAMILY_DIR,
        )
        main.DATA_DIR = self.data
        main.USERS_PATH = self.data / "users.json"
        main.CONFIG_PATH = self.data / "config.json"
        tagging.DATA_DIR = self.data
        tagging.FAMILY_DIR = self.data / "family"
        tagging.INDEX_PATH = self.data / "indexes/hashtag_index.json"
        tagging._snapshot = None
        brain.DATA_DIR = self.data
        brain.FAMILY_DIR = self.data / "family"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "user-a", "username": "Alex", "password": "x"},
            {"id": "user-b", "username": "Sam", "password": "x"},
        ]}), encoding="utf-8")
        main.CONFIG_PATH.write_text(json.dumps({"templates": []}), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf"
        self._write_files()

    def tearDown(self):
        (
            main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH,
            tagging.DATA_DIR, tagging.FAMILY_DIR, tagging.INDEX_PATH, tagging._snapshot,
            brain.DATA_DIR, brain.FAMILY_DIR,
        ) = self.old
        self.temp.cleanup()

    def _write(self, relative, content):
        path = self.data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_files(self):
        self._write("user-a/projects/umzug.md", "# Umzug\n\nKartons packen.\n\n- [ ] Kisten besorgen\n- [x] Termin\n")
        self._write("user-a/notes/rezepte.md", "# Rezepte\n\nLinsensuppe mit Ingwer.\n")
        self._write("user-b/projects/fremd.md", "# Fremdes Projekt\n\nNur fuer Sam.\n")
        self._write("user-b/notes/fremd.md", "# Fremde Notiz\n\nNur fuer Sam.\n")
        self._write(f"family/projects/{GARDEN_ID}.md", GARDEN)
        self._write(f"family/projects/{GARDEN_ID}.md.lock", "")
        self._write(f"family/projects/{HIDDEN_ID}.md", HIDDEN)
        self._write(
            f"family/projects/{GARDEN_ID}.sync-conflict-20261010-101010-ABCDEFG.md",
            GARDEN.replace("Garten", "Konfliktgarten"),
        )
        self._write("family/notes/familie-essen.md", "# Familienessen\n\nSonntag bei Oma.\n")
        self._write("family/notes/familie-essen.md.lock", "")
        self._write("family/notes/familie-essen.sync-conflict-20261010-101010-ABCDEFG.md",
                    "# Konfliktessen\n\nAlte Fassung.\n")
        self._write("family/notes/kaputt.md", "---\nkaputt\n---\n# Kaputt\n\nUngueltiges Frontmatter.\n")
        self._write("family/notes/nur-sam.md", "---\nassigned_users: [user-b]\n---\n# Nur Sam\n\nPrivat.\n")

    def _headers(self):
        return {"X-CSRF-Token": "csrf"}

    def _configure(self, **flags):
        brain_chat.save_settings("user-a", {
            "base_url": "http://192.168.1.50:8080", "model": "local-model", **flags,
        })
        return brain_chat.load_settings("user-a")

    def _all_on(self):
        return self._configure(include_projects=True, include_notes=True)

    # ─── Einstellungen ───────────────────────────────────────────────

    def test_both_switches_default_to_off(self):
        payload = self.client.get("/api/brain/chat/settings").get_json()
        self.assertFalse(payload["include_projects"])
        self.assertFalse(payload["include_notes"])
        self.assertNotIn("list_projects", payload["tools"])
        self.assertNotIn("list_notes", payload["tools"])
        self._configure()
        self.assertEqual(brain_chat.enabled_kinds(brain_chat.load_settings("user-a")), ())

    def test_a_switch_alone_is_saved_without_touching_the_connection(self):
        brain_chat.save_settings("user-a", {
            "base_url": "http://192.168.1.50:8080", "model": "local-model", "api_key": "secret",
        })
        response = self.client.post("/api/brain/chat/settings", headers=self._headers(),
                                    json={"include_projects": True})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["include_projects"])
        self.assertFalse(payload["include_notes"])
        self.assertIn("list_projects", payload["tools"])
        stored = brain_chat.load_settings("user-a")
        self.assertEqual(stored["model"], "local-model")
        self.assertEqual(stored["api_key"], "secret")
        # Ein spaeteres Speichern der Verbindung laesst den Schalter stehen.
        self._configure()
        self.assertTrue(brain_chat.load_settings("user-a")["include_projects"])

    def test_a_switch_must_be_a_real_boolean(self):
        response = self.client.post("/api/brain/chat/settings", headers=self._headers(),
                                    json={"include_notes": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(brain_chat.load_settings("user-a")["include_notes"])

    def test_the_tools_follow_the_switches(self):
        names = lambda settings: {tool["function"]["name"] for tool in brain_chat.tool_specs(settings)}
        off = self._configure()
        self.assertEqual(names(off), names(None))
        projects = self._configure(include_projects=True, include_notes=False)
        self.assertEqual(names(projects) - names(off), {"list_projects", "get_project"})
        notes = self._configure(include_projects=False, include_notes=True)
        self.assertEqual(names(notes) - names(off), {"list_notes", "get_note"})

    def test_a_document_tool_does_not_run_while_its_switch_is_off(self):
        settings = self._configure(include_notes=True)
        with self.assertRaises(ToolInputError):
            brain_chat.run_tool("user-a", "list_projects", {}, settings)
        with self.assertRaises(ToolInputError):
            brain_chat.run_tool("user-a", "list_notes", {})
        self.assertIn("notes", brain_chat.run_tool("user-a", "list_notes", {}, settings))

    def test_the_system_prompt_only_mentions_enabled_sources(self):
        plain = brain_chat._ChatProfile("user-a", self._configure()).system_prompt()
        self.assertNotIn("list_projects", plain)
        self.assertNotIn("Privat/Familie", plain)
        self.assertEqual(plain, brain_chat._ChatProfile("user-a").system_prompt())
        both = brain_chat._ChatProfile("user-a", self._all_on()).system_prompt()
        self.assertIn("list_projects", both)
        self.assertIn("list_notes", both)
        self.assertIn("Privat/Familie", both)

    # ─── Projekte ────────────────────────────────────────────────────

    def test_projects_list_own_and_visible_family_files_only(self):
        payload = brain_chat_documents.run_list_projects("user-a")
        by_title = {item["title"]: item for item in payload["projects"]}
        self.assertEqual(set(by_title), {"Umzug", "Garten"})
        self.assertEqual(by_title["Umzug"]["id"], "personal:projects/umzug.md")
        self.assertEqual(by_title["Umzug"]["source"], "Privat")
        self.assertEqual((by_title["Umzug"]["tasks_open"], by_title["Umzug"]["tasks_done"]), (1, 1))
        self.assertEqual(by_title["Garten"]["id"], f"family:projects/{GARDEN_ID}.md")
        self.assertEqual(by_title["Garten"]["source"], "Familie")
        self.assertEqual((by_title["Garten"]["tasks_open"], by_title["Garten"]["tasks_done"]), (1, 1))
        self.assertIn("Beet umgraben", by_title["Garten"]["excerpt"])

    def test_the_other_user_sees_the_restricted_family_project(self):
        titles = {item["title"] for item in brain_chat_documents.run_list_projects("user-b")["projects"]}
        self.assertEqual(titles, {"Fremdes Projekt", "Garten", "Ueberraschungsparty"})

    def test_lock_and_sync_conflict_files_are_ignored(self):
        projects = brain_chat_documents.run_list_projects("user-a")["projects"]
        notes = brain_chat_documents.run_list_notes("user-a")["notes"]
        paths = [item["path"] for item in projects + notes]
        self.assertFalse([path for path in paths if "sync-conflict" in path or path.endswith(".lock")])
        conflict = f"family:projects/{GARDEN_ID}.sync-conflict-20261010-101010-ABCDEFG.md"
        with self.assertRaises(ToolInputError):
            brain_chat_documents.run_get_project("user-a", id=conflict)
        with self.assertRaises(ToolInputError):
            brain_chat_documents.run_get_note(
                "user-a", id="family:notes/familie-essen.sync-conflict-20261010-101010-ABCDEFG.md")

    def test_a_family_project_reads_tasks_status_and_comments(self):
        project = brain_chat_documents.run_get_project("user-a", id=f"family:projects/{GARDEN_ID}.md")
        self.assertEqual(project["title"], "Garten")
        self.assertEqual(project["source"], "Familie")
        self.assertEqual(project["path"], f"projects/{GARDEN_ID}.md")
        self.assertIn("Beet umgraben", project["text"])
        self.assertEqual(
            [(task["title"], task["done"], task["user"]) for task in project["tasks"]],
            [("Erde kaufen", False, "Alex"), ("Samen bestellen", True, "Sam")],
        )
        self.assertEqual(project["tasks"][0]["target_date"], "2026-10-20")
        self.assertEqual(project["comments"], [
            {"user": "Sam", "text": "Bitte Bio-Erde", "created_at": "2026-10-03T09:00:00"},
        ])
        self.assertNotIn("id: t1", json.dumps(project))

    def test_a_personal_project_reads_its_markdown_and_checkboxes(self):
        project = brain_chat_documents.run_get_project("user-a", id="personal:projects/umzug.md")
        self.assertEqual(project["source"], "Privat")
        self.assertIn("Kartons packen", project["text"])
        self.assertEqual([(task["title"], task["done"]) for task in project["tasks"]],
                         [("Kisten besorgen", False), ("Termin", True)])

    def test_hidden_foreign_and_escaping_ids_are_all_not_found(self):
        refused = [
            f"family:projects/{HIDDEN_ID}.md",
            "personal:../user-b/projects/fremd.md",
            "family:../user-b/projects/fremd.md",
            "personal:notes/rezepte.md",
            "personal:projects/fehlt.md",
            "archive:projects/x.md",
            "projects/umzug.md",
            "",
        ]
        for document_id in refused:
            with self.subTest(document_id=document_id):
                with self.assertRaises(ToolInputError) as caught:
                    brain_chat_documents.run_get_project("user-a", id=document_id)
                self.assertNotIn("Geheimplan", str(caught.exception))

    def test_a_symlink_never_leads_out_of_the_own_folder(self):
        link = self.data / "user-a/projects/link.md"
        link.symlink_to(self.data / "user-b/projects/fremd.md")
        titles = {item["title"] for item in brain_chat_documents.run_list_projects("user-a")["projects"]}
        self.assertNotIn("Fremdes Projekt", titles)
        with self.assertRaises(ToolInputError):
            brain_chat_documents.run_get_project("user-a", id="personal:projects/link.md")

    def test_a_query_and_a_limit_narrow_the_list(self):
        found = brain_chat_documents.run_list_projects("user-a", query="erde")
        self.assertEqual([item["title"] for item in found["projects"]], ["Garten"])
        limited = brain_chat_documents.run_list_projects("user-a", limit=1)
        self.assertEqual((limited["count"], limited["total"], limited["truncated"]), (1, 2, True))
        with self.assertRaises(ToolInputError):
            brain_chat_documents.run_list_projects("user-a", limit="viele")

    # ─── Notes ───────────────────────────────────────────────────────

    def test_notes_list_own_and_visible_family_notes_fail_closed(self):
        titles = {item["title"]: item["source"] for item in brain_chat_documents.run_list_notes("user-a")["notes"]}
        # „Kaputt" hat ungueltiges Frontmatter, „Nur Sam" ist user-b zugewiesen.
        self.assertEqual(titles, {"Rezepte": "Privat", "Familienessen": "Familie"})
        with self.assertRaises(ToolInputError):
            brain_chat_documents.run_get_note("user-a", id="family:notes/kaputt.md")
        with self.assertRaises(ToolInputError):
            brain_chat_documents.run_get_note("user-a", id="family:notes/nur-sam.md")

    def test_a_note_is_read_in_full_with_its_source(self):
        note = brain_chat_documents.run_get_note("user-a", id="family:notes/familie-essen.md")
        self.assertEqual(note["title"], "Familienessen")
        self.assertEqual(note["source"], "Familie")
        self.assertEqual(note["id"], "family:notes/familie-essen.md")
        self.assertIn("Sonntag bei Oma", note["text"])
        self.assertFalse(note["truncated"])

    def test_a_long_note_is_capped_and_marked(self):
        self._write("user-a/notes/lang.md", "# Lang\n\n" + "x" * (brain_chat_documents.MAX_DOCUMENT_CHARS + 50))
        note = brain_chat_documents.run_get_note("user-a", id="personal:notes/lang.md")
        self.assertTrue(note["truncated"])
        self.assertLessEqual(len(note["text"]), brain_chat_documents.MAX_DOCUMENT_CHARS)

    # ─── Nur lesen ───────────────────────────────────────────────────

    def test_the_tools_never_change_a_file(self):
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.data.rglob("*.md") if path.is_file()
        }
        brain_chat_documents.run_list_projects("user-a")
        brain_chat_documents.run_get_project("user-a", id=f"family:projects/{GARDEN_ID}.md")
        brain_chat_documents.run_get_project("user-a", id="personal:projects/umzug.md")
        brain_chat_documents.run_list_notes("user-a")
        brain_chat_documents.run_get_note("user-a", id="family:notes/familie-essen.md")
        after = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.data.rglob("*.md") if path.is_file()
        }
        self.assertEqual(before, after)

    # ─── Schleife ────────────────────────────────────────────────────

    def test_a_project_round_is_executed_and_traced_with_titles(self):
        settings = self._all_on()
        responses = [
            _completion(tool_calls=[_tool_call("list_projects", {})]),
            _completion(tool_calls=[_tool_call("get_project", {"id": f"family:projects/{GARDEN_ID}.md"},
                                               call_id="call-2")]),
            _completion(content="Im Projekt Garten (Familie) ist noch Erde zu kaufen."),
        ]
        with mock.patch.object(brain_chat, "_post", side_effect=responses) as post:
            result = brain_chat.answer("user-a", [{"role": "user", "content": "Was ist im Garten offen?"}],
                                       settings)
        self.assertEqual([step["tool"] for step in result["steps"]], ["list_projects", "get_project"])
        self.assertIn("Garten", result["steps"][0]["hits"])
        self.assertEqual(result["steps"][1]["hits"], ["Garten"])
        first = post.call_args_list[0].args[1]
        self.assertIn("get_note", {tool["function"]["name"] for tool in first["tools"]})
        self.assertIn("list_projects", first["messages"][0]["content"])
        self.assertIn("Erde kaufen", post.call_args_list[2].args[1]["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
