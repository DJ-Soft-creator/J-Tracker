import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "brain-api-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/opencode/journl-brain-api-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import brain
    import family
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class BrainApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self._old_main_data_dir = main.DATA_DIR
        self._old_users_path = main.USERS_PATH
        self._old_brain_data_dir = brain.DATA_DIR
        self._old_brain_family_dir = brain.FAMILY_DIR
        self._old_tagging_data_dir = brain.tagging_module.DATA_DIR
        self._old_tagging_family_dir = brain.tagging_module.FAMILY_DIR
        self._old_tagging_index_path = brain.tagging_module.INDEX_PATH
        self._old_tagging_snapshot = brain.tagging_module._snapshot
        self._old_family_data_dir = family.DATA_DIR
        self._old_family_dir = family.FAMILY_DIR
        self._old_family_projects_dir = family.PROJECTS_DIR
        self._old_family_planner_dir = family.PLANNER_DIR
        self._old_family_archive_dir = family.ARCHIVE_DIR
        self._old_family_planner_file = family.PLANNER_FILE
        self._old_family_tasks_file = family.FAMILY_TASKS_FILE
        main.DATA_DIR = self.data_dir
        main.USERS_PATH = self.data_dir / "users.json"
        brain.DATA_DIR = self.data_dir
        brain.FAMILY_DIR = self.data_dir / "family"
        brain.tagging_module.DATA_DIR = self.data_dir
        brain.tagging_module.FAMILY_DIR = self.data_dir / "family"
        brain.tagging_module.INDEX_PATH = self.data_dir / "indexes/hashtag_index.json"
        brain.tagging_module._snapshot = None
        family.DATA_DIR = self.data_dir
        family.FAMILY_DIR = self.data_dir / "family"
        family.PROJECTS_DIR = family.FAMILY_DIR / "projects"
        family.PLANNER_DIR = family.FAMILY_DIR / "planner"
        family.ARCHIVE_DIR = family.FAMILY_DIR / "archive"
        family.PLANNER_FILE = family.PLANNER_DIR / "recurring.md"
        family.FAMILY_TASKS_FILE = family.FAMILY_DIR / "Familien-Aufgaben.md"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        main.USERS_PATH.write_text(json.dumps({
            "users": [
                {"id": "user-a", "username": "TestuserA", "password": "test"},
                {"id": "user-b", "username": "TestuserB", "password": "test"},
            ]
        }), encoding="utf-8")
        self._write_sources()
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "user-a"
            session["csrf_token"] = "csrf-test"
        self.enqueue = mock.patch.object(brain, "enqueue_rebuild", return_value=True)
        self.enqueue.start()

    def tearDown(self):
        self.enqueue.stop()
        main.DATA_DIR = self._old_main_data_dir
        main.USERS_PATH = self._old_users_path
        brain.DATA_DIR = self._old_brain_data_dir
        brain.FAMILY_DIR = self._old_brain_family_dir
        brain.tagging_module.DATA_DIR = self._old_tagging_data_dir
        brain.tagging_module.FAMILY_DIR = self._old_tagging_family_dir
        brain.tagging_module.INDEX_PATH = self._old_tagging_index_path
        brain.tagging_module._snapshot = self._old_tagging_snapshot
        family.DATA_DIR = self._old_family_data_dir
        family.FAMILY_DIR = self._old_family_dir
        family.PROJECTS_DIR = self._old_family_projects_dir
        family.PLANNER_DIR = self._old_family_planner_dir
        family.ARCHIVE_DIR = self._old_family_archive_dir
        family.PLANNER_FILE = self._old_family_planner_file
        family.FAMILY_TASKS_FILE = self._old_family_tasks_file
        self.temp_dir.cleanup()

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def _write(self, relative, content):
        path = self.data_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_sources(self):
        self._write(
            "user-a/hashtag_catalog.json",
            json.dumps({"version": 1, "canonical": ["focus", "work"], "aliases": {}, "proposals": ["unapproved"]}),
        )
        self.journal_path = self._write(
            "user-a/2026/08/03/Journal_2026-08-03.md",
            "# Journal 2026-08-03\n\n"
            "___\n\n"
            "## Thema: Time:10:00:00\n"
            "Visible journal text\n"
            "  - [ ] Personal task #work\n\n"
            "___\n\n"
            "___\n\n"
            "## Brainablage: Tags: Focus, Work **Datum & Uhrzeit: 2026-08-03 11:00:00**\n"
            "Stored note\n\n"
            "___\n",
        )
        self._write("user-a/projects/alpha.md", "# Alpha\n\n- [ ] Project task\n")
        self._write("user-a/notes/keep.md", "# Keep\n\nA lasting note.\n")
        self._write("user-a/notes/README.md", "Technical note that must not be indexed.\n")
        self._write("_Archiv/Projekte/reference.md", "# Reference\n\nArchive text.\n")
        self._write(
            "family/projects/visible.md",
            "---\nassigned_users: [user-a]\n---\n\n# Visible Family\n\n- [ ] Family visible task\n",
        )
        self._write(
            "family/projects/secret.md",
            "---\nassigned_users: [user-b]\n---\n\n# Secret Family\n\nSecret family text #secret\n",
        )

    def test_search_timeline_and_tag_counts_only_include_allowed_sources(self):
        timeline = self.client.get("/api/brain/search")
        self.assertEqual(timeline.status_code, 200)
        results = timeline.get_json()["results"]
        paths = {item["path"] for item in results}
        self.assertIn("2026/08/03/Journal_2026-08-03.md", paths)
        self.assertIn("projects/alpha.md", paths)
        self.assertIn("notes/keep.md", paths)
        self.assertIn("projects/visible.md", paths)
        self.assertIn("reference.md", paths)
        self.assertNotIn("notes/README.md", paths)
        self.assertNotIn("projects/secret.md", paths)
        self.assertEqual({item["kind"] for item in results if item["path"] == "notes/keep.md"}, {"note"})
        self.assertEqual({item["kind"] for item in results if item["path"] == "projects/alpha.md"}, {"project"})
        self.assertEqual(next(item["source_label"] for item in results if item["path"] == "reference.md"), "Archiv")

    def test_bootstrap_builds_initial_brain_view_from_one_visible_snapshot(self):
        with mock.patch.object(brain, "_visible_documents", wraps=brain._visible_documents) as visible:
            response = self.client.get("/api/brain/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(visible.call_count, 1)
        self.assertTrue(payload["results"])
        self.assertEqual(
            [(project["path"], project["title"]) for project in payload["projects"]],
            [("projects/alpha.md", "Alpha")],
        )
        self.assertIn("catalog", payload)
        self.assertIn("tags", payload)

    def test_search_can_be_limited_to_journals_and_an_inclusive_date_range(self):
        response = self.client.get(
            "/api/brain/search?kind=journal&start_date=2026-08-03&end_date=2026-08-03"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item["path"] for item in response.get_json()["results"]},
            {"2026/08/03/Journal_2026-08-03.md"},
        )
        invalid = self.client.get("/api/brain/search?start_date=2026-08-04&end_date=2026-08-03")
        self.assertEqual(invalid.status_code, 400)

    def test_search_pagination_keeps_the_unlimited_api_default(self):
        unlimited = self.client.get("/api/brain/search").get_json()
        first = self.client.get("/api/brain/search?limit=2").get_json()
        second = self.client.get("/api/brain/search?limit=2&offset=2").get_json()

        self.assertGreater(len(unlimited["results"]), 2)
        self.assertEqual(len(first["results"]), 2)
        self.assertEqual(first["total"], len(unlimited["results"]))
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        self.assertEqual(second["results"], unlimited["results"][2:4])
        self.assertEqual(self.client.get("/api/brain/search?limit=-1").status_code, 400)

    def test_file_lists_support_hashtag_filters(self):
        self._write("user-a/notes/tagged.md", "# Tagged\n\nRelevant #focus note.\n")
        self.assertEqual(
            [item["path"] for item in self.client.get("/api/brain/notes?tags=focus").get_json()["notes"]],
            ["notes/tagged.md"],
        )

        hidden = self.client.get("/api/brain/search?q=secret")
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.get_json()["results"], [])

        tags = self.client.get("/api/brain/tags").get_json()["tags"]
        names = {item["name"] for item in tags}
        self.assertIn("focus", names)
        self.assertIn("work", names)
        self.assertNotIn("secret", names)

    def test_search_filters_visible_blocks_by_tag(self):
        response = self.client.get("/api/brain/search?tags=focus")

        self.assertEqual(response.status_code, 200)
        results = response.get_json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "2026/08/03/Journal_2026-08-03.md")
        self.assertIn("focus", results[0]["tags"])

    def test_tag_count_deduplicates_tags_in_the_same_visible_block(self):
        documents = [{"blocks": [{"fingerprint": "block-1", "tags": ["focus", "focus"]}]}]
        with mock.patch.object(brain, "_visible_documents", return_value=(documents, False)):
            response = self.client.get("/api/brain/tags")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["tags"],
            [{"name": "focus", "count": 1, "scope": "personal"}],
        )

    def test_ai_api_allows_five_minutes_for_response(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "classified"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            result = main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {
                    "max_tokens": 1234,
                    "temperature": 0.1,
                    "response_format": {"type": "json_schema"},
                },
                "journal",
            )

        self.assertEqual(result, "classified")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 300)
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["max_tokens"], 1234)
        self.assertEqual(payload["temperature"], 0.1)
        self.assertEqual(payload["response_format"], {"type": "json_schema"})

    def test_ai_api_rejects_reasoning_only_for_required_content(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "", "reasoning_content": "Still thinking"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "required structured output"):
                main._call_ai_api(
                    {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                    {"require_content": True},
                    "journal",
                )

    def test_ai_api_keeps_reasoning_fallback_for_other_functions(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "", "reasoning_content": "Useful answer"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            result = main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {},
                "journal",
            )

        self.assertEqual(result, "Useful answer")

    def test_historical_tagging_accepts_only_local_provider(self):
        config = {
            "ai_providers": [
                {"id": "openrouter", "model": "remote"},
                {"id": "lm_test", "model": "local"},
            ],
            "historical_tagging_ai": {"system_prompt": "test"},
        }
        report = {"processed": 0, "skipped": 0, "errors": [], "proposals": []}

        with mock.patch.object(main, "load_config", return_value=config), mock.patch.object(
            brain.historical_tagging, "run_historical_tagging", return_value=report
        ) as run_tagging:
            remote = self.client.post(
                "/api/brain/tagging/run",
                json={"start_date": "2026-08-01", "end_date": "2026-08-04", "provider_id": "openrouter"},
                headers=self.headers,
            )
            local = self.client.post(
                "/api/brain/tagging/run",
                json={"start_date": "2026-08-01", "end_date": "2026-08-04", "provider_id": "lm_test"},
                headers=self.headers,
            )

        self.assertEqual(remote.status_code, 400)
        self.assertEqual(local.status_code, 200)
        self.assertEqual(run_tagging.call_count, 1)
        self.assertEqual(run_tagging.call_args.args[3]["id"], "lm_test")

    def test_personal_file_lists_are_separated_recursive_and_filterable(self):
        self._write("user-a/notes/garden/example-notes.md", "# Example\n\nExample content.\n")
        self._write("user-a/notes/empty.md", "")
        self._write("user-a/projects/home/renovation.md", "# Renovation\n\nPaint the kitchen.\n")

        notes_response = self.client.get("/api/brain/notes")
        projects_response = self.client.get("/api/brain/projects")
        self.assertEqual(notes_response.status_code, 200)
        self.assertEqual(projects_response.status_code, 200)
        notes = notes_response.get_json()["notes"]
        projects = projects_response.get_json()["projects"]
        note_paths = {item["path"] for item in notes}
        project_paths = {item["path"] for item in projects}
        self.assertEqual(note_paths, {"notes/keep.md", "notes/garden/example-notes.md", "notes/empty.md"})
        self.assertEqual(project_paths, {"projects/alpha.md", "projects/home/renovation.md"})
        self.assertNotIn("notes/README.md", note_paths)
        self.assertNotIn("projects/visible.md", project_paths)

        nested = next(item for item in notes if item["path"] == "notes/garden/example-notes.md")
        self.assertEqual(nested["doc_id"], "personal:notes/garden/example-notes.md")
        self.assertEqual(nested["kind"], "note")
        self.assertEqual(nested["title"], "Example")
        self.assertTrue(nested["modified_at"].endswith("+00:00"))
        self.assertEqual(
            [item["path"] for item in self.client.get("/api/brain/notes?q=example").get_json()["notes"]],
            ["notes/garden/example-notes.md"],
        )
        self.assertEqual(
            [item["path"] for item in self.client.get("/api/brain/projects?q=kitchen").get_json()["projects"]],
            ["projects/home/renovation.md"],
        )

    def test_personal_file_creation_collisions_are_immediately_editable_and_searchable(self):
        first = self.client.post("/api/brain/notes", headers=self.headers, json={"title": "Weekly Review"})
        second = self.client.post("/api/brain/notes", headers=self.headers, json={"title": "Weekly Review"})
        project = self.client.post("/api/brain/projects", headers=self.headers, json={"title": "Alpha"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(project.status_code, 201)
        first_note = first.get_json()["note"]
        second_note = second.get_json()["note"]
        created_project = project.get_json()["project"]
        self.assertEqual(first_note["path"], "notes/weekly-review.md")
        self.assertEqual(second_note["path"], "notes/weekly-review-2.md")
        self.assertEqual(created_project["path"], "projects/alpha-2.md")
        self.assertEqual(first_note["doc_id"], "personal:notes/weekly-review.md")
        self.assertEqual(first_note["kind"], "note")
        self.assertEqual(
            (self.data_dir / "user-a/notes/weekly-review.md").read_text(encoding="utf-8"),
            "# Weekly Review\n\n",
        )

        opened = self.client.get("/api/brain/document", query_string={"doc_id": first_note["doc_id"]})
        self.assertEqual(opened.status_code, 200)
        payload = opened.get_json()
        saved = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": first_note["doc_id"],
            "content": "# Weekly Review\n\nFresh searchable summary.\n",
            "content_hash": payload["content_hash"],
        })
        self.assertEqual(saved.status_code, 200)
        results = self.client.get("/api/brain/search?q=fresh+searchable").get_json()["results"]
        self.assertEqual([(item["path"], item["kind"]) for item in results], [(first_note["path"], "note")])
        listed = self.client.get("/api/brain/notes?q=fresh+searchable").get_json()["notes"]
        self.assertEqual([item["doc_id"] for item in listed], [first_note["doc_id"]])

    def test_personal_file_endpoints_require_auth_csrf_and_valid_titles(self):
        anonymous = main.app.test_client()
        self.assertEqual(anonymous.get("/api/brain/notes").status_code, 401)
        self.assertEqual(anonymous.get("/api/brain/projects").status_code, 401)
        self.assertEqual(anonymous.post("/api/brain/notes", json={"title": "Hidden"}).status_code, 401)
        self.assertEqual(self.client.post("/api/brain/notes", json={"title": "No CSRF"}).status_code, 403)
        self.assertEqual(self.client.post("/api/brain/projects", json={"title": "No CSRF"}).status_code, 403)
        self.assertEqual(self.client.post("/api/brain/notes", headers=self.headers, json={"title": 42}).status_code, 400)
        self.assertEqual(self.client.post("/api/brain/projects", headers=self.headers, json={"title": "\n"}).status_code, 400)
        self.assertEqual(self.client.post("/api/brain/notes", headers=self.headers, json={"title": "\ud800"}).status_code, 400)

    def test_personal_file_creation_avoids_technical_names_and_symlinked_directories(self):
        reserved = self.client.post("/api/brain/notes", headers=self.headers, json={"title": "Security"})
        self.assertEqual(reserved.status_code, 201)
        reserved_note = reserved.get_json()["note"]
        self.assertEqual(reserved_note["path"], "notes/security-2.md")
        self.assertEqual(
            self.client.get("/api/brain/document", query_string={"doc_id": reserved_note["doc_id"]}).status_code,
            200,
        )

        uppercase = self._write("user-a/notes/uppercase.MD", "# Uppercase\n")
        self.assertTrue(uppercase.is_file())
        self.assertEqual(
            self.client.get("/api/brain/document", query_string={"doc_id": "personal:notes/uppercase.MD"}).status_code,
            404,
        )

        notes_directory = self.data_dir / "user-a/notes"
        notes_directory.rename(self.data_dir / "user-a/notes-original")
        with tempfile.TemporaryDirectory() as outside_directory:
            notes_directory.symlink_to(outside_directory, target_is_directory=True)
            blocked = self.client.post("/api/brain/notes", headers=self.headers, json={"title": "Outside"})
            self.assertEqual(blocked.status_code, 409)
            self.assertFalse((Path(outside_directory) / "outside.md").exists())

    def test_personal_file_creation_rejects_symlinked_user_root(self):
        user_root = self.data_dir / "user-a"
        user_root.rename(self.data_dir / "user-a-original")
        with tempfile.TemporaryDirectory() as outside_directory:
            user_root.symlink_to(outside_directory, target_is_directory=True)
            blocked = self.client.post("/api/brain/projects", headers=self.headers, json={"title": "Outside"})
            self.assertEqual(blocked.status_code, 409)
            self.assertFalse((Path(outside_directory) / "projects/outside.md").exists())

    def test_family_file_creation_assigns_creator_and_access_is_managed_separately(self):
        self._write("family/notes/shared.md", "---\nassigned_users: []\n---\n\n# Shared Note\n\nVisible to everyone.\n")
        self._write("family/notes/private.md", "---\nassigned_users: [user-b]\n---\n\n# Private Note\n")

        listed = self.client.get("/api/brain/family")
        self.assertEqual(listed.status_code, 200)
        payload = listed.get_json()
        self.assertEqual({item["path"] for item in payload["notes"]}, {"notes/shared.md"})
        self.assertEqual({item["path"] for item in payload["projects"]}, {"projects/visible.md"})
        self.assertEqual(
            [item["path"] for item in self.client.get("/api/brain/family?q=everyone").get_json()["notes"]],
            ["notes/shared.md"],
        )

        note_response = self.client.post("/api/brain/family/notes", headers=self.headers, json={"title": "Family Review"})
        project_response = self.client.post("/api/brain/family/projects", headers=self.headers, json={"title": "Garden Project"})
        self.assertEqual(note_response.status_code, 201)
        self.assertEqual(project_response.status_code, 201)
        note = note_response.get_json()["note"]
        project = project_response.get_json()["project"]
        note_content = (self.data_dir / "family" / note["path"]).read_text(encoding="utf-8")
        project_content = (self.data_dir / "family" / project["path"]).read_text(encoding="utf-8")
        self.assertIn("assigned_users: [user-a]\n", note_content)
        self.assertIn("created_by: user-a\n", note_content)
        self.assertIn("# Family Review\n", note_content)
        self.assertIn("assigned_users: [user-a]\n", project_content)
        self.assertIn("title: Garden Project\n", project_content)
        self.assertIn("## Aufgaben\n", project_content)
        opened = self.client.get("/api/brain/document", query_string={"doc_id": note["doc_id"]})
        self.assertEqual(opened.status_code, 200)
        editor = opened.get_json()
        self.assertNotIn("assigned_users", editor["content"])
        self.assertTrue(editor["management"]["can_manage"])

        access = self.client.put("/api/brain/document/access", headers=self.headers, json={
            "doc_id": note["doc_id"],
            "assigned_users": ["user-b"],
            "content_hash": editor["content_hash"],
        })
        self.assertEqual(access.status_code, 200)
        self.assertEqual(access.get_json()["assigned_users"], ["user-a", "user-b"])
        family_files = self.client.get("/api/brain/family").get_json()
        created_note = next(item for item in family_files["notes"] if item["doc_id"] == note["doc_id"])
        created_project = next(item for item in family_files["projects"] if item["doc_id"] == project["doc_id"])
        self.assertTrue(created_note["management"]["can_manage"])
        self.assertTrue(created_project["management"]["can_manage"])

        with self.client.session_transaction() as session:
            session["user_id"] = "user-b"
        visible_to_user_b = self.client.get("/api/brain/family").get_json()
        self.assertIn(note["doc_id"], {item["doc_id"] for item in visible_to_user_b["notes"]})
        self.assertNotIn(project["doc_id"], {item["doc_id"] for item in visible_to_user_b["projects"]})
        forbidden = self.client.put("/api/brain/document/access", headers=self.headers, json={
            "doc_id": note["doc_id"],
            "assigned_users": ["user-a", "user-b"],
            "content_hash": access.get_json()["content_hash"],
        })
        self.assertEqual(forbidden.status_code, 403)

    def test_document_tags_include_approval_status(self):
        self._write("user-a/notes/tagged.md", "# Tagged\n\n#work #unapproved\n")
        opened = self.client.get("/api/brain/document", query_string={"doc_id": "personal:notes/tagged.md"})
        self.assertEqual(opened.status_code, 200)
        tags = {item["name"]: item["approved"] for item in opened.get_json()["tags"]}
        self.assertEqual(tags, {"work": True, "unapproved": False})

    def test_task_toggle_and_metadata_update_original_markdown(self):
        projects = self.client.post("/api/brain/projects", headers=self.headers, json={"title": "Work"})
        self.assertEqual(projects.status_code, 201)
        project_path = projects.get_json()["project"]["path"]

        tasks = self.client.get("/api/brain/tasks?status=open").get_json()["tasks"]
        task = next(item for item in tasks if item["text"] == "Personal task #work")
        metadata = self.client.post("/api/brain/metadata", headers=self.headers, json={
            "doc_id": task["doc_id"],
            "reference_type": "task",
            "fingerprint": task["fingerprint"],
            "tags": ["Urgent", "Work"],
            "priority": "high",
            "project": project_path,
        })
        self.assertEqual(metadata.status_code, 200)

        toggled = self.client.post("/api/brain/task/toggle", headers=self.headers, json={
            "doc_id": task["doc_id"],
            "fingerprint": task["fingerprint"],
            "completed": True,
        })
        self.assertEqual(toggled.status_code, 200)
        self.assertIn("- [x] Personal task #work", self.journal_path.read_text(encoding="utf-8"))

        done = self.client.get("/api/brain/tasks?status=done&priority=high").get_json()["tasks"]
        updated = next(item for item in done if item["fingerprint"] == task["fingerprint"])
        self.assertTrue(updated["completed"])
        self.assertEqual(updated["project"], project_path)
        self.assertIn("urgent", updated["tags"])

    def test_family_editor_requires_confirmation_and_archive_is_read_only(self):
        visible_doc = "family:projects/visible.md"
        opened = self.client.get("/api/brain/document", query_string={"doc_id": visible_doc})
        self.assertEqual(opened.status_code, 200)
        payload = opened.get_json()

        no_confirmation = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": visible_doc,
            "content": payload["content"] + "\nChanged\n",
            "content_hash": payload["content_hash"],
        })
        self.assertEqual(no_confirmation.status_code, 400)

        confirmed = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": visible_doc,
            "content": payload["content"] + "\nChanged\n",
            "content_hash": payload["content_hash"],
            "confirm_family_edit": True,
        })
        self.assertEqual(confirmed.status_code, 200)
        self.assertIn("Changed", (self.data_dir / "family/projects/visible.md").read_text(encoding="utf-8"))

        hidden = self.client.get("/api/brain/document", query_string={"doc_id": "family:projects/secret.md"})
        self.assertEqual(hidden.status_code, 404)

        archive = self.client.get("/api/brain/document", query_string={"doc_id": "archive:reference.md"})
        self.assertEqual(archive.status_code, 200)
        archive_payload = archive.get_json()
        self.assertTrue(archive_payload["read_only"])
        read_only = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": "archive:reference.md",
            "content": "Changed",
            "content_hash": archive_payload["content_hash"],
        })
        self.assertEqual(read_only.status_code, 403)

    def test_document_save_requires_current_hash(self):
        opened = self.client.get("/api/brain/document", query_string={"doc_id": "personal:notes/keep.md"})
        self.assertEqual(opened.status_code, 200)
        payload = opened.get_json()

        missing_hash = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": payload["doc_id"],
            "content": "# Keep\n\nChanged\n",
        })
        self.assertEqual(missing_hash.status_code, 400)

        (self.data_dir / "user-a/notes/keep.md").write_text("# Keep\n\nExternal change\n", encoding="utf-8")
        conflict = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": payload["doc_id"],
            "content": "# Keep\n\nChanged\n",
            "content_hash": payload["content_hash"],
        })
        self.assertEqual(conflict.status_code, 409)

    def test_structured_family_toggle_preserves_completion_audit_and_reopens(self):
        family_path = self._write(
            "family/projects/family-project.md",
            "---\n"
            "id: family-project\n"
            "assigned_users: [user-a]\n"
            "---\n\n"
            "# Family Project\n\n"
            "## Aufgaben\n"
            "- [ ] id: family-task | title: Shared task | user: user-a | target-date: 2026-08-03\n",
        )
        tasks = self.client.get("/api/brain/tasks?status=open").get_json()["tasks"]
        task = next(item for item in tasks if "title: Shared task" in item["text"])
        metadata = self.client.post("/api/brain/metadata", headers=self.headers, json={
            "doc_id": task["doc_id"],
            "reference_type": "task",
            "fingerprint": task["fingerprint"],
            "tags": ["Family Manual"],
            "priority": "high",
        })
        self.assertEqual(metadata.status_code, 200)

        completed = self.client.post("/api/brain/task/toggle", headers=self.headers, json={
            "doc_id": task["doc_id"],
            "fingerprint": task["fingerprint"],
            "completed": True,
        })
        self.assertEqual(completed.status_code, 200)
        persisted = family_path.read_text(encoding="utf-8")
        self.assertIn("- [x] id: family-task", persisted)
        self.assertIn("completed_at:", persisted)
        self.assertIn("completed_by: user-a", persisted)

        done = self.client.get("/api/brain/tasks?status=done").get_json()["tasks"]
        reopened_task = next(item for item in done if item["doc_id"] == task["doc_id"])
        self.assertEqual(reopened_task["fingerprint"], task["fingerprint"])
        self.assertEqual(reopened_task["priority"], "high")
        self.assertIn("family manual", reopened_task["manual_tags"])
        reopened = self.client.post("/api/brain/task/toggle", headers=self.headers, json={
            "doc_id": reopened_task["doc_id"],
            "fingerprint": reopened_task["fingerprint"],
            "completed": False,
        })
        self.assertEqual(reopened.status_code, 200)
        persisted = family_path.read_text(encoding="utf-8")
        self.assertIn("- [ ] id: family-task", persisted)
        self.assertNotIn("completed_at:", persisted)
        self.assertNotIn("completed_by:", persisted)

    def test_family_task_completion_rejects_project_path_traversal(self):
        result = family.set_task_completion("../../user-a/projects/alpha", "task", "user-a", True)
        self.assertTrue(result["forbidden"])
        self.assertFalse(result["found"])

    def test_indexed_family_visibility_is_rechecked_from_source(self):
        self._write(
            "family/hashtag_catalog.json",
            json.dumps({"version": 1, "canonical": ["revoked"], "aliases": {}, "proposals": []}),
        )
        brain.rebuild_family_index()
        visible_path = self.data_dir / "family/projects/visible.md"
        visible_path.write_text(
            "---\nassigned_users: [user-b]\n---\n\n# Visible Family\n\nHidden after index #revoked\n",
            encoding="utf-8",
        )
        response = self.client.get("/api/brain/search?q=hidden")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["results"], [])
        tags = self.client.get("/api/brain/tags").get_json()["tags"]
        revoked = next(tag for tag in tags if tag["name"] == "revoked")
        self.assertEqual(revoked["count"], 0)
        self.assertEqual(revoked["scope"], "family")

    def test_non_admin_can_filter_by_shared_family_hashtags_without_occurrences(self):
        self._write(
            "family/hashtag_catalog.json",
            json.dumps({"version": 1, "canonical": ["shared"], "aliases": {}, "proposals": []}),
        )

        response = self.client.get("/api/brain/tags")

        self.assertEqual(response.status_code, 200)
        shared = next(tag for tag in response.get_json()["tags"] if tag["name"] == "shared")
        self.assertEqual(shared, {"name": "shared", "count": 0, "scope": "family"})
        self.assertEqual(self.client.get("/api/brain/search?tags=shared").get_json()["results"], [])
        catalog = self.client.get("/api/brain/tag-catalog").get_json()
        self.assertIn("shared", catalog["catalog"]["family"]["canonical"])
        self.assertFalse(catalog["can_manage_family"])

    def test_only_boolean_admin_can_manage_family_hashtags(self):
        family_catalog = self._write(
            "family/hashtag_catalog.json",
            json.dumps({"version": 1, "canonical": [], "aliases": {}, "proposals": ["shared"]}),
        )
        users = json.loads(main.USERS_PATH.read_text(encoding="utf-8"))
        users["users"][0]["admin"] = "true"
        main.USERS_PATH.write_text(json.dumps(users), encoding="utf-8")

        denied_catalog = self.client.get("/api/brain/tag-catalog")
        self.assertFalse(denied_catalog.get_json()["can_manage_family"])
        denied = self.client.post("/api/brain/tag-catalog", headers=self.headers, json={
            "scope": "family", "action": "approve", "tag": "shared",
        })
        self.assertEqual(denied.status_code, 403)

        users["users"][0]["admin"] = True
        main.USERS_PATH.write_text(json.dumps(users), encoding="utf-8")
        allowed_catalog = self.client.get("/api/brain/tag-catalog")
        self.assertTrue(allowed_catalog.get_json()["can_manage_family"])
        allowed = self.client.post("/api/brain/tag-catalog", headers=self.headers, json={
            "scope": "family", "action": "approve", "tag": "shared",
        })
        self.assertEqual(allowed.status_code, 200)
        persisted = json.loads(family_catalog.read_text(encoding="utf-8"))
        self.assertIn("shared", persisted["canonical"])

    def test_rebuild_proposes_hashtags_from_personal_notes(self):
        self._write("user-a/notes/new-tag.md", "# Note\n\nText with #needs-approval.\n")

        brain.rebuild_user_index("user-a")

        catalog = brain.tagging_module.read_catalog("user-a")
        self.assertIn("needs-approval", catalog["proposals"])

    def test_indexed_family_content_is_replaced_from_current_source(self):
        brain.rebuild_family_index()
        visible_path = self.data_dir / "family/projects/visible.md"
        visible_path.write_text(
            "---\nassigned_users: [user-a]\n---\n\n# Visible Family\n\nCurrent replacement text\n",
            encoding="utf-8",
        )

        stale = self.client.get("/api/brain/search?q=family+visible+task")
        current = self.client.get("/api/brain/search?q=current+replacement")
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.get_json()["results"], [])
        self.assertEqual(len(current.get_json()["results"]), 1)
        self.assertTrue(current.get_json()["index_pending"])

    def test_family_frontmatter_and_legacy_archive_fail_closed(self):
        self._write(
            "family/projects/crlf.md",
            "---\r\nid: crlf\r\nassigned_users:\r\n  - user-a\r\n---\r\n\r\n# CRLF\r\n\r\nVisible CRLF text\r\n",
        )
        self._write(
            "family/projects/malformed.md",
            "---\r\nid: malformed\r\nassigned_users: [user-b\r\n---\r\n\r\n# Malformed\r\n\r\nMust stay hidden\r\n",
        )
        self._write("family/archive/legacy.md", "# Legacy\n\nUnverifiable archive secret\n")

        visible = self.client.get("/api/brain/search?q=visible+crlf").get_json()["results"]
        malformed = self.client.get("/api/brain/search?q=must+stay+hidden").get_json()["results"]
        legacy = self.client.get("/api/brain/search?q=unverifiable+archive").get_json()["results"]
        self.assertEqual(len(visible), 1)
        self.assertEqual(malformed, [])
        self.assertEqual(legacy, [])
        family_projects = self.client.get("/api/family/projects").get_json()["projects"]
        project_ids = {project["id"] for project in family_projects}
        self.assertIn("crlf", project_ids)
        self.assertNotIn("malformed", project_ids)

    def test_family_archive_and_duplicate_task_permissions_fail_closed(self):
        self._write(
            "family/projects/secret-parent.md",
            "---\nid: secret-parent\nassigned_users: [user-b]\n---\n\n# Secret\n\n"
            "## Aufgaben\n- [x] id: secret-task | title: Secret | user: user-b | target-date: 2026-08-03\n",
        )
        self._write(
            "family/archive/secret-task.md",
            "---\nid: secret-task\nuser: user-b\nproject_id: secret-parent\n---\n\n## Secret\n",
        )
        self._write(
            "family/projects/duplicate.md",
            "---\nid: duplicate\nassigned_users: [user-a]\n---\n\n# Duplicate\n\n## Aufgaben\n"
            "- [ ] id: same-id | title: Duplicate one | user: user-a | target-date: 2026-08-03\n"
            "- [ ] id: same-id | title: Duplicate two | user: user-a | target-date: 2026-08-04\n",
        )

        archive = self.client.get("/api/family/archive")
        duplicate_project = self.client.get("/api/family/project/duplicate")
        brain_tasks = self.client.get("/api/brain/tasks?q=duplicate").get_json()["tasks"]
        archive_attempt = self.client.post("/api/family/archive/secret-task", headers=self.headers)
        self.assertEqual(archive.status_code, 200)
        self.assertEqual(archive.get_json()["items"], [])
        self.assertEqual(duplicate_project.status_code, 404)
        self.assertEqual(brain_tasks, [])
        self.assertEqual(archive_attempt.status_code, 404)
        self.assertTrue((self.data_dir / "family/projects/secret-parent.md").is_file())

    def test_direct_document_access_is_markdown_only_and_preamble_is_indexed(self):
        self._write("user-a/notes/private.txt", "Non-Markdown secret")
        self._write(
            "user-a/notes/preamble.md",
            "Introductory searchable text\n- [ ] Preamble task\n\n# Details\n\nBody\n",
        )

        opened = self.client.get("/api/brain/document", query_string={"doc_id": "personal:notes/private.txt"})
        results = self.client.get("/api/brain/search?q=introductory+searchable").get_json()["results"]
        tasks = self.client.get("/api/brain/tasks?q=preamble+task").get_json()["tasks"]
        self.assertEqual(opened.status_code, 404)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(tasks), 1)

    def test_empty_note_remains_in_timeline_and_editable(self):
        brain.rebuild_user_index("user-a")
        doc_id = "personal:notes/keep.md"
        index_path = brain._index_file(brain._index_dir("personal", "user-a"), doc_id)
        self.assertTrue(index_path.is_file())
        opened = self.client.get("/api/brain/document", query_string={"doc_id": doc_id}).get_json()

        saved = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": doc_id,
            "content": "",
            "content_hash": opened["content_hash"],
        })
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(index_path.exists())
        self.assertEqual(self.client.get("/api/brain/search?q=lasting+note").get_json()["results"], [])
        timeline = self.client.get("/api/brain/search").get_json()["results"]
        self.assertIn("notes/keep.md", {item["path"] for item in timeline})
        reopened = self.client.get("/api/brain/document", query_string={"doc_id": doc_id})
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.get_json()["content"], "")

    def test_existing_index_discovers_new_and_changed_personal_sources(self):
        brain.rebuild_user_index("user-a")
        self._write("user-a/notes/beispiel-notiz.md", "# Gartenbeispiel\n\nExampleinhalt.\n")
        self._write("user-a/projects/garten/planung.md", "# Garten\n\nGartenbeispiel.\n")
        (self.data_dir / "user-a/notes/keep.md").write_text(
            "# Keep\n\nExtern geaenderte Notiz.\n",
            encoding="utf-8",
        )

        response = self.client.get("/api/brain/search")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        paths = {item["path"] for item in payload["results"]}
        self.assertIn("notes/beispiel-notiz.md", paths)
        self.assertIn("projects/garten/planung.md", paths)
        self.assertTrue(payload["index_pending"])
        changed = self.client.get("/api/brain/search?q=extern+geaenderte").get_json()["results"]
        self.assertEqual(len(changed), 1)

        opened = self.client.get(
            "/api/brain/document",
            query_string={"doc_id": "personal:notes/beispiel-notiz.md"},
        )
        self.assertEqual(opened.status_code, 200)
        self.assertIn("FicusExample", opened.get_json()["content"])
        saved = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": "personal:notes/beispiel-notiz.md",
            "content": "# Gartenbeispiel\n\nBeispielinhalt mit mehreren Stichworten.\n",
            "content_hash": opened.get_json()["content_hash"],
        })
        self.assertEqual(saved.status_code, 200)
        self.assertIn("FicusExample", (self.data_dir / "user-a/notes/beispiel-notiz.md").read_text(encoding="utf-8"))

    def test_incomplete_index_falls_back_to_sources(self):
        brain.rebuild_user_index("user-a")
        status_path = brain._index_dir("personal", "user-a") / "status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["document_count"] += 1
        status_path.write_text(json.dumps(status), encoding="utf-8")

        response = self.client.get("/api/brain/search?q=lasting+note")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["results"]), 1)
        self.assertTrue(response.get_json()["index_pending"])

    def test_archive_has_independent_index(self):
        self.assertEqual(brain.rebuild_archive_index(), 1)
        archive_index = brain._index_dir("archive")
        self.assertNotEqual(archive_index, brain._index_dir("family"))
        documents = brain._read_index_documents("archive")
        self.assertEqual([document["doc_id"] for document in documents], ["archive:reference.md"])

    def test_template_assignment_uses_created_entry_line(self):
        original = self.journal_path.read_text(encoding="utf-8")
        line_hint = original.count("\n") + 1
        self.journal_path.write_text(
            original + "___\n\n- [ ] Personal task #work\n\n___\n",
            encoding="utf-8",
        )

        saved = brain.record_template_assignment(
            "user-a",
            self.journal_path,
            "projects/alpha.md",
            "Personal task #work",
            line_hint,
        )
        self.assertTrue(saved)
        document = brain._build_document(
            "personal", self.data_dir / "user-a", self.journal_path, "journal"
        )
        matching = [
            task for block in document["blocks"] for task in block["tasks"]
            if task["text"] == "Personal task #work"
        ]
        self.assertEqual(len(matching), 2)
        metadata = json.loads(
            (self.data_dir / "user-a/brain_metadata.json").read_text(encoding="utf-8")
        )["annotations"]
        self.assertNotIn(matching[0]["fingerprint"], metadata)
        self.assertEqual(metadata[matching[1]["fingerprint"]]["project"], "projects/alpha.md")

    def test_preserved_mtime_still_detects_external_note_change(self):
        brain.rebuild_user_index("user-a")
        path = self.data_dir / "user-a/notes/keep.md"
        before = path.stat()
        path.write_text("# Keep\n\nA changed note.\n", encoding="utf-8")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

        response = self.client.get("/api/brain/search?q=changed+note")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["results"]), 1)
        self.assertTrue(response.get_json()["index_pending"])

    def test_non_owner_worker_persists_rebuild_request(self):
        self.enqueue.stop()
        try:
            with mock.patch.object(brain, "start_index_worker", return_value=False):
                self.assertTrue(brain.enqueue_rebuild("user-a"))
            self.assertEqual(brain._pop_rebuild_request(), "user-a")
            self.assertIsNone(brain._pop_rebuild_request())
        finally:
            self.enqueue.start()

    def test_header_only_journal_does_not_keep_index_pending(self):
        self._write(
            "user-a/2026/08/04/Journal_2026-08-04.md",
            "# Journal 2026-08-04\n\n",
        )
        brain.rebuild_user_index("user-a")
        brain.rebuild_family_index()
        brain.rebuild_archive_index()

        response = self.client.get("/api/brain/search")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["index_pending"])

    def test_duplicate_timestamp_tasks_have_distinct_toggle_targets(self):
        duplicate_path = self._write(
            "user-a/2026/08/04/Journal_2026-08-04.md",
            "# Journal 2026-08-04\n\n"
            "___\n\n## Thema: Time:12:00:00\n- [ ] Duplicate collision task\n\n___\n\n"
            "___\n\n## Thema: Time:12:00:00\n- [ ] Duplicate collision task\n\n___\n",
        )
        tasks = self.client.get("/api/brain/tasks?q=duplicate+collision").get_json()["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len({task["fingerprint"] for task in tasks}), 2)

        second = max(tasks, key=lambda task: task["start_line"])
        toggled = self.client.post("/api/brain/task/toggle", headers=self.headers, json={
            "doc_id": second["doc_id"],
            "fingerprint": second["fingerprint"],
            "completed": True,
        })
        self.assertEqual(toggled.status_code, 200)
        content = duplicate_path.read_text(encoding="utf-8")
        self.assertEqual(content.count("- [ ] Duplicate collision task"), 1)
        self.assertEqual(content.count("- [x] Duplicate collision task"), 1)

    def test_rebuild_migrates_legacy_metadata_fingerprint(self):
        document = brain._build_document(
            "personal",
            self.data_dir / "user-a",
            self.data_dir / "user-a/notes/keep.md",
            "note",
        )
        block = document["blocks"][0]
        self.assertNotEqual(block["fingerprint"], block["legacy_fingerprint"])
        metadata_path = self.data_dir / "user-a/brain_metadata.json"
        metadata_path.write_text(json.dumps({
            "version": 1,
            "annotations": {
                block["legacy_fingerprint"]: {
                    "kind": "block",
                    "doc_id": document["doc_id"],
                    "path": document["path"],
                    "stable_anchor": block["stable_anchor"],
                    "tags": ["legacy"],
                    "priority": "high",
                    "project": "projects/alpha.md",
                    "orphaned": False,
                }
            },
        }), encoding="utf-8")

        brain.rebuild_user_index("user-a")
        migrated = json.loads(metadata_path.read_text(encoding="utf-8"))["annotations"]
        self.assertNotIn(block["legacy_fingerprint"], migrated)
        self.assertEqual(migrated[block["fingerprint"]]["tags"], ["legacy"])
        result = self.client.get("/api/brain/search?q=lasting+note").get_json()["results"][0]
        self.assertIn("legacy", result["manual_tags"])

    def test_ambiguous_legacy_task_metadata_stays_unassigned(self):
        duplicate_path = self._write(
            "user-a/2026/08/04/Journal_2026-08-04.md",
            "# Journal 2026-08-04\n\n"
            "___\n\n## Thema: Time:12:00:00\n- [ ] Ambiguous legacy task\n\n___\n\n"
            "___\n\n## Thema: Time:12:00:00\n- [ ] Ambiguous legacy task\n\n___\n",
        )
        document = brain._build_document(
            "personal", self.data_dir / "user-a", duplicate_path, "journal"
        )
        matching = [
            task for block in document["blocks"] for task in block["tasks"]
            if task["text"] == "Ambiguous legacy task"
        ]
        self.assertEqual(matching[0]["legacy_fingerprint"], matching[1]["legacy_fingerprint"])
        self._write("user-a/brain_metadata.json", json.dumps({
            "version": 1,
            "annotations": {
                matching[0]["legacy_fingerprint"]: {
                    "kind": "task",
                    "doc_id": document["doc_id"],
                    "path": document["path"],
                    "stable_anchor": "timestamp:12:00:00",
                    "task_text": brain._task_identity("Ambiguous legacy task"),
                    "task_occurrence": 1,
                    "tags": ["must-not-move"],
                    "priority": "high",
                    "project": "",
                    "orphaned": True,
                }
            },
        }))

        tasks = self.client.get("/api/brain/tasks?q=ambiguous+legacy").get_json()["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all("must-not-move" not in task["manual_tags"] for task in tasks))

    def test_metadata_updates_are_partial_and_corrupt_file_is_preserved(self):
        result = next(
            item for item in self.client.get("/api/brain/search?q=stored+note").get_json()["results"]
            if item["path"].endswith("Journal_2026-08-03.md")
        )
        priority = self.client.post("/api/brain/metadata", headers=self.headers, json={
            "doc_id": result["doc_id"],
            "reference_type": "block",
            "fingerprint": result["fingerprint"],
            "priority": "high",
        })
        self.assertEqual(priority.status_code, 200)
        refreshed = next(
            item for item in self.client.get("/api/brain/search?q=stored+note").get_json()["results"]
            if item["doc_id"] == result["doc_id"]
        )
        self.assertEqual(refreshed["priority"], "high")
        self.assertEqual(refreshed["manual_tags"], [])
        self.assertIn("focus", refreshed["tags"])

        tags = self.client.post("/api/brain/metadata", headers=self.headers, json={
            "doc_id": result["doc_id"],
            "reference_type": "block",
            "fingerprint": result["fingerprint"],
            "tags": ["Manual"],
        })
        self.assertEqual(tags.status_code, 200)
        self.assertEqual(tags.get_json()["metadata"]["priority"], "high")

        metadata_path = self.data_dir / "user-a/brain_metadata.json"
        metadata_path.write_text("{broken", encoding="utf-8")
        corrupt = self.client.post("/api/brain/metadata", headers=self.headers, json={
            "doc_id": result["doc_id"],
            "reference_type": "block",
            "fingerprint": result["fingerprint"],
            "priority": "low",
        })
        self.assertEqual(corrupt.status_code, 409)
        self.assertEqual(metadata_path.read_text(encoding="utf-8"), "{broken")

        invalid_schema = '{"annotations":{"bad":"value"}}'
        metadata_path.write_text(invalid_schema, encoding="utf-8")
        readable = self.client.get("/api/brain/search?q=stored+note")
        schema_update = self.client.post("/api/brain/metadata", headers=self.headers, json={
            "doc_id": result["doc_id"],
            "reference_type": "block",
            "fingerprint": result["fingerprint"],
            "priority": "low",
        })
        self.assertEqual(readable.status_code, 200)
        self.assertEqual(schema_update.status_code, 409)
        self.assertEqual(metadata_path.read_text(encoding="utf-8"), invalid_schema)

    def test_rebuild_keeps_unmatched_metadata_as_orphaned(self):
        tasks = self.client.get("/api/brain/tasks?status=open").get_json()["tasks"]
        task = next(item for item in tasks if item["text"] == "Personal task #work")
        self.client.post("/api/brain/metadata", headers=self.headers, json={
            "doc_id": task["doc_id"],
            "reference_type": "task",
            "fingerprint": task["fingerprint"],
            "tags": ["keep"],
            "priority": "normal",
            "project": "",
        })
        self.journal_path.write_text("# Journal 2026-08-03\n", encoding="utf-8")
        brain.rebuild_user_index("user-a")
        metadata = json.loads((self.data_dir / "user-a/brain_metadata.json").read_text(encoding="utf-8"))
        annotation = metadata["annotations"][task["fingerprint"]]
        self.assertTrue(annotation["orphaned"])
        self.assertTrue((self.data_dir / "user-a/indexes/brain_index/status.json").is_file())

    def test_journal_transaction_serializes_with_brain_task_toggle(self):
        document = brain._build_document("personal", self.data_dir / "user-a", self.journal_path, "journal")
        task = next(task for block in document["blocks"] for task in block["tasks"] if task["text"] == "Personal task #work")
        entered = threading.Event()
        release = threading.Event()
        toggle_started = threading.Event()
        toggle_result = {}

        def append_under_lock():
            def append(current):
                entered.set()
                release.wait(timeout=2)
                return current + "\nConcurrent journal entry\n"

            main._update_journal_file(self.journal_path, append)

        def toggle_task():
            toggle_started.set()
            toggle_result.update(brain._toggle_markdown_task(self.journal_path, task, True))

        writer = threading.Thread(target=append_under_lock)
        writer.start()
        self.assertTrue(entered.wait(timeout=1))

        toggler = threading.Thread(target=toggle_task)
        toggler.start()
        self.assertTrue(toggle_started.wait(timeout=1))
        self.assertTrue(toggler.is_alive())
        release.set()
        writer.join(timeout=2)
        toggler.join(timeout=2)

        self.assertFalse(writer.is_alive())
        self.assertFalse(toggler.is_alive())
        self.assertTrue(toggle_result["ok"])
        content = self.journal_path.read_text(encoding="utf-8")
        self.assertIn("Concurrent journal entry", content)
        self.assertIn("- [x] Personal task #work", content)


if __name__ == "__main__":
    unittest.main()
