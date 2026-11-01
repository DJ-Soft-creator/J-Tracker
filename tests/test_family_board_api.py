"""Familie-Tab board: types, aggregates, pinning, permissions and archiving."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "family-board-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/opencode/journl-family-board-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import family
    import family_views
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class FamilyBoardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        main.DATA_DIR = data_dir
        main.USERS_PATH = data_dir / "users.json"
        family.DATA_DIR = data_dir
        family.FAMILY_DIR = data_dir / "family"
        family.PROJECTS_DIR = family.FAMILY_DIR / "projects"
        family.PLANNER_DIR = family.FAMILY_DIR / "planner"
        family.ARCHIVE_DIR = family.FAMILY_DIR / "archive"
        family.ARCHIVED_PROJECTS_DIR = family.ARCHIVE_DIR / "projects"
        family.PLANNER_FILE = family.PLANNER_DIR / "recurring.md"
        family.FAMILY_TASKS_FILE = family.FAMILY_DIR / "Familien-Aufgaben.md"
        data_dir.mkdir(parents=True, exist_ok=True)
        main.USERS_PATH.write_text(json.dumps({
            "users": [
                {"id": "user-a", "username": "Alex", "password": "test"},
                {"id": "user-b", "username": "Bea", "password": "test"},
                {"id": "user-admin", "username": "Chef", "password": "test", "admin": True},
            ]
        }), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        self._login("user-a")

    def tearDown(self):
        self.temp_dir.cleanup()

    # ─── helpers ─────────────────────────────────────────────────────
    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf-test"

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def _write_project(self, project_id, **fields):
        family._ensure_dirs()
        project = {
            "id": project_id,
            "title": fields.get("title", project_id),
            "type": fields.get("type", "bucket"),
            "template_id": fields.get("template_id", "bucket"),
            "target_file": fields.get("target_file", ""),
            "assigned_users": fields.get("assigned_users", []),
            "created_at": fields.get("created_at", "2026-01-01T10:00:00+00:00"),
            "created_by": fields.get("created_by", "user-a"),
            "updated_at": fields.get("updated_at", ""),
            "tasks": fields.get("tasks", []),
            "comments": [],
        }
        path = family.PROJECTS_DIR / f"{project_id}.md"
        path.write_text(family.serialize_project(project), encoding="utf-8")
        return path

    @staticmethod
    def _task(task_id, title, user="", completed=False):
        return {
            "id": task_id, "title": title, "user": user, "target_date": "",
            "completed": completed, "created_at": "2026-01-01T10:00:00+00:00",
            "created_by": "user-a",
        }

    def _board(self):
        response = self.client.get("/api/family/board")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    # ─── Typen und Bestandsdaten ─────────────────────────────────────
    def test_legacy_file_without_type_stays_a_bucket(self):
        family._ensure_dirs()
        (family.PROJECTS_DIR / "urlaub.md").write_text(
            "---\nid: urlaub\ntitle: Urlaub\ntemplate_id: projekt\ntarget_file: \n"
            "assigned_users: []\ncreated_at: 2026-01-01T10:00:00+00:00\ncreated_by: user-a\n---\n\n"
            "## Aufgaben\n- [ ] id: t1 | title: Koffer packen | user:  | target-date: \n\n## Kommentare\n",
            encoding="utf-8",
        )
        board = self._board()
        self.assertEqual([item["title"] for item in board["aufgaben"]], ["Urlaub"])
        self.assertEqual(board["projekte"], [])
        self.assertEqual(board["notizen"], [])

    def test_documents_are_split_across_the_three_subtabs(self):
        self._write_project("bucket-1", title="Einkaufen", type="bucket")
        self._write_project("projekt-1", title="Wohnzimmer renovieren", type="projekt")
        self._write_project("notiz-1", title="Infos Kinder", type="notiz")
        board = self._board()
        self.assertEqual([item["title"] for item in board["aufgaben"]], ["Einkaufen"])
        self.assertEqual([item["title"] for item in board["projekte"]], ["Wohnzimmer renovieren"])
        self.assertEqual([item["title"] for item in board["notizen"]], ["Infos Kinder"])

    # ─── Persoenlicher Bucket ────────────────────────────────────────
    def test_personal_bucket_is_created_lazily_and_stays_private(self):
        board = self._board()
        self.assertTrue(board["personal"]["personal"])
        self.assertEqual(board["personal"]["id"], family_views.personal_bucket_id("user-a"))
        self.assertFalse(board["personal"]["can_delete"])

        # Bea darf den fremden persoenlichen Bucket weder sehen noch oeffnen.
        self._login("user-b")
        other_board = self._board()
        own_ids = {item["id"] for item in other_board["aufgaben"]}
        self.assertNotIn(family_views.personal_bucket_id("user-a"), own_ids)
        detail = self.client.get("/api/family/project/" + family_views.personal_bucket_id("user-a"))
        self.assertEqual(detail.status_code, 404)

    def test_assigned_task_from_a_restricted_project_surfaces_with_its_origin(self):
        self._write_project(
            "geheim", title="Wohnzimmer renovieren", type="projekt",
            assigned_users=["user-b"],
            tasks=[self._task("t-paint", "Wand streichen", user="user-a")],
        )
        board = self._board()
        # Das Projekt selbst bleibt unsichtbar ...
        self.assertEqual(board["projekte"], [])
        self.assertEqual(self.client.get("/api/family/project/geheim").status_code, 404)
        # ... die zugewiesene Aufgabe erscheint mit Herkunft, aber ohne Zugriff.
        tasks = board["personal"]["tasks"]
        self.assertEqual([task["title"] for task in tasks], ["Wand streichen"])
        self.assertEqual(tasks[0]["origin_title"], "Wohnzimmer renovieren")
        self.assertFalse(tasks[0]["origin_accessible"])

    def test_assignee_may_complete_a_task_from_a_restricted_project(self):
        self._write_project(
            "geheim", title="Renovierung", type="projekt", assigned_users=["user-b"],
            tasks=[
                self._task("t-mine", "Wand streichen", user="user-a"),
                self._task("t-theirs", "Boden verlegen", user="user-b"),
            ],
        )
        mine = self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": "geheim", "task_id": "t-mine", "completed": True,
        })
        self.assertEqual(mine.status_code, 200)
        # Eine fremde Aufgabe im selben Dokument bleibt gesperrt: die Task-ID
        # darf kein Umweg um assigned_users sein.
        theirs = self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": "geheim", "task_id": "t-theirs", "completed": True,
        })
        self.assertEqual(theirs.status_code, 404)
        project = family._load_project("geheim")
        states = {task["id"]: task["completed"] for task in project["tasks"]}
        self.assertEqual(states, {"t-mine": True, "t-theirs": False})

    def test_accidentally_checked_task_stays_resettable_after_a_reload(self):
        """Abgehakte Aufgaben fallen aus „Meine Aufgaben“; „Zuletzt erledigt“ fuehrt sie weiter."""
        self._write_project("geheim", title="Renovierung", type="projekt", assigned_users=["user-b"], tasks=[
            self._task("t-mine", "Wand streichen", user="user-a"),
            self._task("t-theirs", "Boden verlegen", user="user-b", completed=True),
        ])
        self._write_project("einkauf", title="Wocheneinkauf", template_id="einkaufsliste", tasks=[
            self._task("i-milch", "Milch", user="user-a", completed=True),
        ])
        self.assertEqual(self._board()["recently_completed"], [])
        self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": "geheim", "task_id": "t-mine", "completed": True,
        })
        recent = self._board()["recently_completed"]
        # Fremde Aufgaben aus gesperrten Dokumenten und Einkaufsartikel bleiben draussen.
        self.assertEqual([(item["id"], item["origin_id"]) for item in recent], [("t-mine", "geheim")])
        self.assertEqual(recent[0]["completed_by_display"], "Alex")
        reset = self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": recent[0]["origin_id"], "task_id": recent[0]["id"], "completed": False,
        })
        self.assertEqual(reset.status_code, 200)
        board = self._board()
        self.assertEqual(board["recently_completed"], [])
        self.assertEqual([task["title"] for task in board["personal"]["tasks"]], ["Wand streichen"])

    def test_unassigned_bucket_appears_only_when_it_holds_something(self):
        self._write_project("haushalt", title="Haushalt", tasks=[
            self._task("t-open", "Müll rausbringen", user="user-a"),
        ])
        self.assertIsNone(self._board()["unassigned"])

        self._write_project("garten", title="Garten", tasks=[
            self._task("t-free", "Hecke schneiden"),
        ])
        board = self._board()
        self.assertIsNotNone(board["unassigned"])
        self.assertEqual([task["title"] for task in board["unassigned"]["tasks"]], ["Hecke schneiden"])
        self.assertEqual(board["unassigned"]["tasks"][0]["origin_title"], "Garten")

    def test_shopping_items_stay_out_of_the_aggregate_buckets(self):
        # Der Einkaufslisten-Import setzt den Ersteller als `user`; ohne
        # Ausschluss landet die halbe Liste in "Meine Aufgaben".
        self._write_project("einkauf", title="Wocheneinkauf", template_id="einkaufsliste", tasks=[
            self._task("i-milch", "Milch", user="user-a"),
            self._task("i-brot", "Brot"),
        ])
        self._write_project("haushalt", title="Haushalt", tasks=[
            self._task("t-muell", "Müll rausbringen", user="user-a"),
            self._task("t-frei", "Hecke schneiden"),
        ])
        board = self._board()
        self.assertEqual([task["title"] for task in board["personal"]["tasks"]], ["Müll rausbringen"])
        self.assertEqual([task["title"] for task in board["unassigned"]["tasks"]], ["Hecke schneiden"])
        # Die Liste selbst bleibt im Aufgaben-Subtab ganz normal sichtbar.
        einkauf = next(item for item in board["aufgaben"] if item["id"] == "einkauf")
        self.assertEqual([task["title"] for task in einkauf["tasks"]], ["Milch", "Brot"])
        self.assertTrue(einkauf["exclude_from_personal"])

    def test_exclusion_can_be_switched_per_bucket_and_survives_a_round_trip(self):
        self._write_project("einkauf", title="Wocheneinkauf", template_id="einkaufsliste", tasks=[
            self._task("i-milch", "Milch", user="user-a"),
        ])
        self._write_project("haushalt", title="Haushalt", tasks=[
            self._task("t-muell", "Müll rausbringen", user="user-a"),
        ])

        # Einkaufsliste bewusst wieder aufnehmen ...
        opt_in = self.client.put("/api/family/project/einkauf", headers=self.headers, json={
            "exclude_from_personal": False,
        })
        self.assertEqual(opt_in.status_code, 200)
        # ... und einen normalen Bucket bewusst ausschliessen.
        opt_out = self.client.put("/api/family/project/haushalt", headers=self.headers, json={
            "exclude_from_personal": True,
        })
        self.assertEqual(opt_out.status_code, 200)

        board = self._board()
        self.assertEqual([task["title"] for task in board["personal"]["tasks"]], ["Milch"])
        # Der abgewählte Standard darf beim erneuten Lesen nicht zurückkehren.
        self.assertFalse(family._load_project("einkauf")["exclude_from_personal"])
        self.assertTrue(family._load_project("haushalt")["exclude_from_personal"])

    def test_exclusion_flag_must_be_boolean(self):
        self._write_project("haushalt", title="Haushalt")
        response = self.client.put("/api/family/project/haushalt", headers=self.headers, json={
            "exclude_from_personal": "ja",
        })
        self.assertEqual(response.status_code, 400)

    def test_planned_tasks_still_reach_the_personal_bucket(self):
        today = main.get_tz_aware_now()[0].date().isoformat()
        created = self.client.post("/api/family/planner", headers=self.headers, json={
            "title": "Müll rausbringen", "user": "user-a",
            "recurrence": "daily", "start_date": today, "active": True,
        })
        self.assertEqual(created.status_code, 201)
        titles = [task["title"] for task in self._board()["personal"]["tasks"]]
        self.assertIn("Müll rausbringen", titles)

    # ─── Einzelne Aufgabe anlegen ────────────────────────────────────
    def test_single_task_can_be_added_to_a_project_and_shows_in_both_views(self):
        self._write_project("projekt-1", title="Wohnzimmer renovieren", type="projekt")
        response = self.client.post("/api/family/project/projekt-1/task", headers=self.headers, json={
            "title": "Wand streichen", "user": "user-a", "target_date": "2026-09-01",
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))

        project = family._load_project("projekt-1")
        self.assertEqual([task["title"] for task in project["tasks"]], ["Wand streichen"])
        self.assertEqual(project["tasks"][0]["user"], "user-a")
        self.assertEqual(project["tasks"][0]["target_date"], "2026-09-01")

        board = self._board()
        personal = [task["title"] for task in board["personal"]["tasks"]]
        self.assertEqual(personal, ["Wand streichen"])
        self.assertEqual(board["personal"]["tasks"][0]["origin_title"], "Wohnzimmer renovieren")
        in_project = next(item for item in board["projekte"] if item["id"] == "projekt-1")
        self.assertEqual([task["title"] for task in in_project["tasks"]], ["Wand streichen"])

    def test_single_task_lands_in_the_personal_bucket_without_a_project(self):
        bucket_id = self._board()["personal"]["id"]
        response = self.client.post("/api/family/project/" + bucket_id + "/task", headers=self.headers, json={
            "title": "Zahnarzttermin machen",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual([task["title"] for task in self._board()["personal"]["tasks"]], ["Zahnarzttermin machen"])

    def test_adding_a_task_appends_and_never_replaces(self):
        self._write_project("haushalt", title="Haushalt", tasks=[
            self._task("t-alt", "Müll rausbringen", user="user-b"),
        ])
        self.client.post("/api/family/project/haushalt/task", headers=self.headers, json={"title": "Neu"})
        titles = [task["title"] for task in family._load_project("haushalt")["tasks"]]
        self.assertEqual(titles, ["Müll rausbringen", "Neu"])

    def test_task_creation_validates_target_assignee_date_and_title(self):
        self._write_project("haushalt", title="Haushalt", assigned_users=["user-b"])
        self._write_project("offen", title="Offen")
        cases = [
            ("/api/family/project/haushalt/task", {"title": "X"}, 404),           # nicht freigegeben
            ("/api/family/project/gibtsnicht/task", {"title": "X"}, 404),
            ("/api/family/project/wiederkehrende-aufgaben/task", {"title": "X"}, 404),
            ("/api/family/project/offen/task", {"title": "X", "user": "fremd"}, 400),
            ("/api/family/project/offen/task", {"title": "X", "target_date": "01.09.2026"}, 400),
            ("/api/family/project/offen/task", {"title": "Mit | Pipe"}, 400),
            ("/api/family/project/offen/task", {"title": "  "}, 400),
        ]
        for url, payload, expected in cases:
            response = self.client.post(url, headers=self.headers, json=payload)
            self.assertEqual(response.status_code, expected, f"{url} {payload}")
        self.assertEqual(family._load_project("offen")["tasks"], [])
        self.assertFalse((family.PROJECTS_DIR / "wiederkehrende-aufgaben.md").exists())

    # ─── Reihenfolge und Anheften ────────────────────────────────────
    def test_documents_are_ordered_by_last_edit_then_by_pins(self):
        self._write_project("alt", title="Alt", updated_at="2026-01-01T08:00:00+00:00")
        self._write_project("neu", title="Neu", updated_at="2026-03-01T08:00:00+00:00")
        self._write_project("mitte", title="Mitte", updated_at="2026-02-01T08:00:00+00:00")
        self.assertEqual([item["title"] for item in self._board()["aufgaben"]], ["Neu", "Mitte", "Alt"])

        pinned = self.client.put("/api/family/pins", headers=self.headers, json={
            "scope": "aufgaben", "ids": ["alt"],
        })
        self.assertEqual(pinned.status_code, 200)
        board = self._board()
        self.assertEqual([item["title"] for item in board["aufgaben"]], ["Alt", "Neu", "Mitte"])
        self.assertTrue(board["aufgaben"][0]["pinned"])

    def test_pins_are_per_user_and_reject_unknown_scopes(self):
        self._write_project("alt", title="Alt", updated_at="2026-01-01T08:00:00+00:00")
        self._write_project("neu", title="Neu", updated_at="2026-03-01T08:00:00+00:00")
        self.client.put("/api/family/pins", headers=self.headers, json={"scope": "aufgaben", "ids": ["alt"]})
        self._login("user-b")
        self.assertEqual([item["title"] for item in self._board()["aufgaben"]], ["Neu", "Alt"])

        bad_scope = self.client.put("/api/family/pins", headers=self.headers, json={
            "scope": "irgendwas", "ids": [],
        })
        self.assertEqual(bad_scope.status_code, 400)
        bad_id = self.client.put("/api/family/pins", headers=self.headers, json={
            "scope": "aufgaben", "ids": ["../etc/passwd"],
        })
        self.assertEqual(bad_id.status_code, 400)

    def test_completing_a_task_updates_the_documents_last_edit(self):
        self._write_project("haushalt", title="Haushalt", updated_at="2026-01-01T08:00:00+00:00",
                            tasks=[self._task("t1", "Müll rausbringen", user="user-a")])
        self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": "haushalt", "task_id": "t1", "completed": True,
        })
        project = family._load_project("haushalt")
        self.assertGreater(project["updated_at"], "2026-01-01T08:00:00+00:00")

    # ─── Rechte ──────────────────────────────────────────────────────
    def test_only_admins_assign_documents_to_users(self):
        self._write_project("bucket-1", title="Urlaub")
        denied = self.client.put("/api/family/project/bucket-1", headers=self.headers, json={
            "assigned_users": ["user-a"],
        })
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(family._load_project("bucket-1")["assigned_users"], [])

        self._login("user-admin")
        allowed = self.client.put("/api/family/project/bucket-1", headers=self.headers, json={
            "assigned_users": ["user-a", "user-admin"],
        })
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(family._load_project("bucket-1")["assigned_users"], ["user-a", "user-admin"])

    def test_creating_with_a_visibility_needs_admin_but_renaming_does_not(self):
        restricted = self.client.post("/api/family/project", headers=self.headers, json={
            "title": "Nur für mich", "template_id": "bucket", "assigned_users": ["user-a"],
        })
        self.assertEqual(restricted.status_code, 403)

        created = self.client.post("/api/family/project", headers=self.headers, json={
            "title": "Wohnzimmer renovieren", "template_id": "projekt", "type": "projekt",
        })
        self.assertEqual(created.status_code, 201)
        project_id = created.get_json()["id"]

        self._login("user-b")
        renamed = self.client.put("/api/family/project/" + project_id, headers=self.headers, json={
            "title": "Wohnzimmer fertig streichen",
        })
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(family._load_project(project_id)["title"], "Wohnzimmer fertig streichen")

    def test_delete_is_limited_to_the_creator_or_an_admin(self):
        self._write_project("bucket-1", title="Urlaub", created_by="user-a")
        self._login("user-b")
        denied = self.client.delete("/api/family/project/bucket-1", headers=self.headers)
        self.assertEqual(denied.status_code, 403)
        self.assertTrue((family.PROJECTS_DIR / "bucket-1.md").exists())

        self._login("user-admin")
        allowed = self.client.delete("/api/family/project/bucket-1", headers=self.headers)
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse((family.PROJECTS_DIR / "bucket-1.md").exists())

    def test_the_personal_bucket_cannot_be_deleted_or_shared(self):
        board = self._board()
        bucket_id = board["personal"]["id"]
        self._login("user-admin")
        self.assertEqual(
            self.client.delete("/api/family/project/" + bucket_id, headers=self.headers).status_code,
            404,  # fuer den Admin ist der fremde Bucket schon nicht sichtbar
        )
        self._login("user-a")
        self.assertEqual(
            self.client.delete("/api/family/project/" + bucket_id, headers=self.headers).status_code,
            403,
        )
        self.assertTrue((family.PROJECTS_DIR / f"{bucket_id}.md").exists())

    def test_titles_with_schema_delimiters_are_rejected(self):
        for title in ("Urlaub | 2026", "Zeile\nZeile", "x" * 201, "   "):
            response = self.client.post("/api/family/project", headers=self.headers, json={
                "title": title, "template_id": "bucket",
            })
            self.assertEqual(response.status_code, 400, title)

    # ─── Projektabschluss ────────────────────────────────────────────
    def test_archive_list_carries_display_names_for_the_read_view(self):
        """Das Archiv ist seit 4.7.16 im Familie-Tab lesbar.

        Die Archivdatei speichert nur IDs. Ohne aufgeloeste Namen zeigte die
        Lesesicht rohe Benutzer-IDs - der Client fuehrt bewusst kein eigenes
        Benutzerverzeichnis.
        """
        self._write_project("projekt-1", title="Kueche", assigned_users=["user-a"])
        family._archive_task("projekt-1", {
            "id": "task-1", "title": "Fliesen bestellen", "user": "user-b",
            "completed_at": "2026-09-10T08:00:00+00:00", "completed_by": "user-a",
            "target_date": "", "created_at": "2026-09-01T10:00:00+00:00",
            "created_by": "user-a",
        })

        items = self.client.get("/api/family/archive").get_json()["items"]

        self.assertEqual([item["title"] for item in items], ["Fliesen bestellen"])
        self.assertEqual(items[0]["user_display"], "Bea")
        self.assertEqual(items[0]["completed_by_display"], "Alex")
        self.assertEqual(items[0]["project_id"], "projekt-1")

    def test_archive_stays_invisible_without_access_to_the_origin(self):
        self._write_project("geheim", title="Geheim", assigned_users=["user-b"])
        family._archive_task("geheim", {
            "id": "task-2", "title": "Nicht sichtbar", "user": "user-b",
            "completed_at": "2026-09-10T08:00:00+00:00", "completed_by": "user-b",
            "target_date": "", "created_at": "", "created_by": "user-b",
        })

        self.assertEqual(self.client.get("/api/family/archive").get_json()["items"], [])

    def test_completing_a_project_moves_it_into_the_archive(self):
        self._write_project("projekt-1", title="Wohnzimmer renovieren", type="projekt")
        response = self.client.post("/api/family/project/projekt-1/complete", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200)
        self.assertFalse((family.PROJECTS_DIR / "projekt-1.md").exists())
        archived = family.ARCHIVED_PROJECTS_DIR / "projekt-1.md"
        self.assertTrue(archived.exists())
        self.assertIn("archived_at:", archived.read_text(encoding="utf-8"))
        self.assertEqual(self._board()["projekte"], [])

    def test_only_projects_can_be_completed(self):
        self._write_project("notiz-1", title="Infos Kinder", type="notiz")
        response = self.client.post("/api/family/project/notiz-1/complete", headers=self.headers, json={})
        self.assertEqual(response.status_code, 400)
        self.assertTrue((family.PROJECTS_DIR / "notiz-1.md").exists())

    # ─── Einkaufslisten ──────────────────────────────────────────────
    def test_shopping_lists_are_listed_and_can_receive_a_chosen_import(self):
        self._write_project("einkauf-a", title="Wocheneinkauf", template_id="einkaufsliste")
        self._write_project("einkauf-b", title="Baumarkt", template_id="einkaufsliste")
        self._write_project("haushalt", title="Haushalt")

        listing = self.client.get("/api/family/shopping-lists").get_json()["items"]
        self.assertEqual({item["title"] for item in listing}, {"Wocheneinkauf", "Baumarkt"})

        added = family.import_shopping_lines(
            text="## Kühlung\n- Milch\n- Butter",
            target_project="einkauf-b",
            target_file="einkaufsliste.md",
            user_uid="",
            created_by="user-a",
        )
        self.assertEqual(added, 2)
        target = family._load_project("einkauf-b")
        self.assertEqual([task["title"] for task in target["tasks"]], ["Milch", "Butter"])
        self.assertEqual(target["tasks"][0]["gruppe"], "Kühlung")
        self.assertEqual(family._load_project("einkauf-a")["tasks"], [])

    def test_import_into_a_restricted_list_is_refused(self):
        self._write_project("privat", title="Privat", template_id="einkaufsliste",
                            assigned_users=["user-b"])
        with self.assertRaises(LookupError):
            family.import_shopping_lines(
                text="- Milch", target_project="privat", target_file="einkaufsliste.md",
                user_uid="", created_by="user-a",
            )
        self.assertEqual(family._load_project("privat")["tasks"], [])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class FamilyRawMarkdownEditorTests(unittest.TestCase):
    """The Schreiben-tab markdown editor for Projekte/Notizen (raw endpoint).

    Regression coverage for the bug where a hand-typed checklist line without
    machine fields (``- [ ] Kauf Milch``) silently hid the whole note from the
    Familie board, and for the accompanying free-text ``notes`` section.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        main.DATA_DIR = data_dir
        main.USERS_PATH = data_dir / "users.json"
        family.DATA_DIR = data_dir
        family.FAMILY_DIR = data_dir / "family"
        family.PROJECTS_DIR = family.FAMILY_DIR / "projects"
        family.PLANNER_DIR = family.FAMILY_DIR / "planner"
        family.ARCHIVE_DIR = family.FAMILY_DIR / "archive"
        family.ARCHIVED_PROJECTS_DIR = family.ARCHIVE_DIR / "projects"
        family.PLANNER_FILE = family.PLANNER_DIR / "recurring.md"
        family.FAMILY_TASKS_FILE = family.FAMILY_DIR / "Familien-Aufgaben.md"
        data_dir.mkdir(parents=True, exist_ok=True)
        main.USERS_PATH.write_text(json.dumps({
            "users": [{"id": "user-a", "username": "Alex", "password": "test"}]
        }), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf-test"
        family._ensure_dirs()
        self.project = {
            "id": "notiz-1", "title": "Test-Note", "type": "notiz",
            "template_id": "notiz", "target_file": "", "assigned_users": [],
            "created_at": "2026-01-01T10:00:00+00:00", "created_by": "user-a",
            "updated_at": "", "tasks": [], "comments": [],
        }
        (family.PROJECTS_DIR / "notiz-1.md").write_text(
            family.serialize_project(self.project), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def test_hand_typed_checklist_line_becomes_a_real_task_instead_of_hiding_the_note(self):
        raw = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        edited = raw.replace("## Aufgaben\n", "## Aufgaben\n- [ ] Kauf Milch\n", 1)
        response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                    json={"content": edited})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        project = family._load_project("notiz-1")
        self.assertTrue(family._project_visible(project, "user-a"))
        self.assertEqual([t["title"] for t in project["tasks"]], ["Kauf Milch"])
        self.assertTrue(project["tasks"][0]["id"])
        self.assertEqual(project["tasks"][0]["created_by"], "user-a")

        board = self.client.get("/api/family/board").get_json()
        self.assertEqual([d["id"] for d in board["notizen"]], ["notiz-1"])

    def test_an_empty_checkbox_line_is_ignored_not_treated_as_broken(self):
        # A snippet like "- [ ] " (see write-snippets.js) can land in the
        # text with nothing typed after it yet - that must stay harmless.
        raw = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        edited = raw.replace("## Aufgaben\n", "## Aufgaben\n- [ ]\n", 1)
        response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                    json={"content": edited})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        project = family._load_project("notiz-1")
        self.assertTrue(family._project_visible(project, "user-a"))
        self.assertEqual(project["tasks"], [])

    def test_free_text_survives_a_save_and_a_later_task_toggle(self):
        raw = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        edited = raw.replace(
            "## Aufgaben\n", "Erster Absatz.\n\nZweiter Absatz mit Details.\n\n## Aufgaben\n", 1)
        response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                    json={"content": edited})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(
            family._load_project("notiz-1")["notes"],
            "Erster Absatz.\n\nZweiter Absatz mit Details.",
        )

        # A save through an unrelated path (task-editor round trip) must not
        # drop the free text again - it goes through the same serializer.
        editor_put = self.client.put("/api/family/project/notiz-1/editor", headers=self.headers,
                                      json={"tasks": [], "comments": []})
        self.assertEqual(editor_put.status_code, 200, editor_put.get_data(as_text=True))
        self.assertEqual(
            family._load_project("notiz-1")["notes"],
            "Erster Absatz.\n\nZweiter Absatz mit Details.",
        )

    def test_free_text_below_the_task_list_survives_and_reaches_the_board(self):
        """Notizen werden von oben nach unten geschrieben: erst Aufgaben, dann Text.

        Frueher fiel jede Zeile unter "## Aufgaben", die keine Aufgabenzeile
        war, beim Speichern still aus der Datei - im Familie-Tab war deshalb
        ausser den Aufgaben nie Text zu sehen.
        """
        raw = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        edited = raw.replace(
            "## Aufgaben\n",
            "## Aufgaben\n- [ ] Angebot pruefen\n\nDazu der Hinweis.\n\nZweiter Absatz.\n",
            1,
        )
        response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                    json={"content": edited})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        project = family._load_project("notiz-1")
        self.assertEqual([t["title"] for t in project["tasks"]], ["Angebot pruefen"])
        self.assertEqual(project["notes_after"], "Dazu der Hinweis.\n\nZweiter Absatz.")

        # Ein Abhaken laeuft durch denselben Serializer und darf den Text
        # genauso wenig verlieren wie das Anlegen.
        task_id = project["tasks"][0]["id"]
        toggle = self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": "notiz-1", "task_id": task_id, "completed": True,
        })
        self.assertEqual(toggle.status_code, 200, toggle.get_data(as_text=True))
        self.assertEqual(
            family._load_project("notiz-1")["notes_after"],
            "Dazu der Hinweis.\n\nZweiter Absatz.",
        )

        board = self.client.get("/api/family/board").get_json()
        note = next(d for d in board["notizen"] if d["id"] == "notiz-1")
        self.assertEqual(note["notes_after"], "Dazu der Hinweis.\n\nZweiter Absatz.")

    def test_an_own_heading_in_the_note_text_survives_a_save(self):
        """Nur "## Aufgaben" und "## Kommentare" sind Ablagefaecher.

        Jede andere Ueberschrift ist Gliederung des Notiztextes und fiel
        frueher beim Speichern still heraus.
        """
        raw = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        edited = raw.replace("## Aufgaben\n", "## Rezept\n\nZwei Eier.\n\n## Aufgaben\n", 1)
        response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                    json={"content": edited})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(family._load_project("notiz-1")["notes"], "## Rezept\n\nZwei Eier.")

    def test_duplicate_task_ids_are_resolved_instead_of_invalidating_the_project(self):
        raw = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        dup_line = "- [ ] id: dup-1 | title: Erste | user: | target-date: |\n"
        edited = raw.replace("## Aufgaben\n", "## Aufgaben\n" + dup_line + dup_line, 1)
        response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                    json={"content": edited})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        project = family._load_project("notiz-1")
        self.assertTrue(family._project_visible(project, "user-a"))
        self.assertEqual(len(project["tasks"]), 2)
        self.assertEqual(len({t["id"] for t in project["tasks"]}), 2)

    def test_a_broken_note_can_still_be_reopened_and_repaired(self):
        # Simulate the pre-fix corruption: a plain checklist line saved
        # verbatim invalidates the project for the strict (default) parser.
        broken = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        broken = broken.replace("## Aufgaben\n", "## Aufgaben\n- [ ] Kauf Milch\n", 1)
        (family.PROJECTS_DIR / "notiz-1.md").write_text(broken, encoding="utf-8")
        self.assertFalse(family._project_visible(family._load_project("notiz-1"), "user-a"))

        # Hidden from the board, but the raw editor (access-only check) can
        # still open and re-save it, which repairs it.
        self.assertNotIn("notiz-1", [d["id"] for d in self.client.get("/api/family/board").get_json()["notizen"]])
        get_response = self.client.get("/api/family/project/notiz-1/raw")
        self.assertEqual(get_response.status_code, 200)
        put_response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                        json={"content": get_response.get_json()["content"]})
        self.assertEqual(put_response.status_code, 200)
        self.assertTrue(family._project_visible(family._load_project("notiz-1"), "user-a"))

    def test_raw_write_rejects_a_frontmatter_id_mismatch(self):
        raw = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        tampered = raw.replace("id: notiz-1", "id: notiz-2", 1)
        response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                    json={"content": tampered})
        self.assertEqual(response.status_code, 400)

    def test_hand_typed_comment_without_a_leading_dash_is_kept_not_dropped(self):
        raw = self.client.get("/api/family/project/notiz-1/raw").get_json()["content"]
        edited = raw.replace("## Kommentare\n", "## Kommentare\nErinnerung an Termin\n", 1)
        response = self.client.put("/api/family/project/notiz-1/raw", headers=self.headers,
                                    json={"content": edited})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        project = family._load_project("notiz-1")
        self.assertEqual([c["text"] for c in project["comments"]], ["Erinnerung an Termin"])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class FamilyTypeMigrationScriptTests(unittest.TestCase):
    """The PROD migration only reclassifies; it never rewrites content."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.projects_dir = Path(self.temp_dir.name) / "projects"
        self.projects_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT_DIR / "archive/migrations/migrate_family_types.py"),
             "--projects-dir", str(self.projects_dir), *args],
            capture_output=True, text=True,
        )

    def _write(self, name, text):
        (self.projects_dir / name).write_text(text, encoding="utf-8")

    LEGACY = (
        "---\nid: {pid}\ntitle: {title}\ntemplate_id: projekt\ntarget_file: \n"
        "assigned_users: []\ncreated_at: 2026-01-01T10:00:00+00:00\ncreated_by: user-a\n---\n\n"
        "## Aufgaben\n- [ ] id: t1 | title: Etwas tun | user:  | target-date: \n\n## Kommentare\n"
    )

    def test_dry_run_changes_nothing(self):
        self._write("urlaub.md", self.LEGACY.format(pid="urlaub", title="Urlaub"))
        before = (self.projects_dir / "urlaub.md").read_text(encoding="utf-8")
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.projects_dir / "urlaub.md").read_text(encoding="utf-8"), before)

    def test_mapping_sets_types_and_is_idempotent(self):
        self._write("urlaub.md", self.LEGACY.format(pid="urlaub", title="Urlaub"))
        self._write("kinder.md", self.LEGACY.format(pid="kinder", title="Infos Kinder"))
        mapping = Path(self.temp_dir.name) / "typen.txt"
        mapping.write_text("# Zuordnung\nkinder;notiz\n", encoding="utf-8")

        self.assertEqual(self._run("--map", str(mapping)).returncode, 0)
        kinder = (self.projects_dir / "kinder.md").read_text(encoding="utf-8")
        urlaub = (self.projects_dir / "urlaub.md").read_text(encoding="utf-8")
        self.assertIn("type: notiz", kinder)
        self.assertIn("type: bucket", urlaub)
        # Inhalte bleiben unangetastet.
        self.assertIn("- [ ] id: t1 | title: Etwas tun | user:  | target-date: ", kinder)
        self.assertIn("updated_at:", kinder)

        self.assertEqual(self._run("--map", str(mapping)).returncode, 0)
        self.assertEqual((self.projects_dir / "kinder.md").read_text(encoding="utf-8"), kinder)

    def test_broken_frontmatter_is_skipped_not_guessed(self):
        self._write("kaputt.md", "kein frontmatter\n- [ ] id: t1 | title: X | user:  | target-date: \n")
        before = (self.projects_dir / "kaputt.md").read_text(encoding="utf-8")
        result = self._run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("übersprungen", result.stderr + result.stdout)
        self.assertEqual((self.projects_dir / "kaputt.md").read_text(encoding="utf-8"), before)

    def _run_guided(self, answers):
        """Drive the walkthrough over a pipe, forcing --interactive."""
        return subprocess.run(
            [sys.executable, str(ROOT_DIR / "archive/migrations/migrate_family_types.py"),
             "--projects-dir", str(self.projects_dir), "--interactive"],
            input="\n".join(answers) + "\n", capture_output=True, text=True,
        )

    def test_guided_walkthrough_applies_the_chosen_types(self):
        self._write("aaa.md", self.LEGACY.format(pid="aaa", title="Urlaub"))
        self._write("bbb.md", self.LEGACY.format(pid="bbb", title="Infos Kinder"))
        # alphabetische Reihenfolge: aaa behalten, bbb → Notiz, dann bestätigen
        result = self._run_guided(["", "n", "j"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("type: bucket", (self.projects_dir / "aaa.md").read_text(encoding="utf-8"))
        self.assertIn("type: notiz", (self.projects_dir / "bbb.md").read_text(encoding="utf-8"))

    def test_guided_walkthrough_writes_nothing_when_declined(self):
        self._write("aaa.md", self.LEGACY.format(pid="aaa", title="Urlaub"))
        before = (self.projects_dir / "aaa.md").read_text(encoding="utf-8")
        for answers in (["q"], ["p", "n"]):  # abgebrochen bzw. Rückfrage verneint
            self.assertEqual(self._run_guided(answers).returncode, 0)
            self.assertEqual((self.projects_dir / "aaa.md").read_text(encoding="utf-8"), before)

    def test_guided_mode_is_off_without_a_terminal(self):
        """Pipes, Cron und Tests dürfen nie auf eine Eingabe warten."""
        self._write("aaa.md", self.LEGACY.format(pid="aaa", title="Urlaub"))
        result = self._run("--dry-run")  # kein --interactive, stdout ist eine Pipe
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("[b]ucket", result.stdout)

    def test_guided_mode_and_mapping_are_mutually_exclusive(self):
        mapping = Path(self.temp_dir.name) / "typen.txt"
        mapping.write_text("", encoding="utf-8")
        result = self._run("--interactive", "--map", str(mapping))
        self.assertEqual(result.returncode, 1)

    def test_unknown_type_in_the_mapping_aborts(self):
        self._write("urlaub.md", self.LEGACY.format(pid="urlaub", title="Urlaub"))
        mapping = Path(self.temp_dir.name) / "typen.txt"
        mapping.write_text("urlaub;quatsch\n", encoding="utf-8")
        result = self._run("--map", str(mapping))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("type:", (self.projects_dir / "urlaub.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
