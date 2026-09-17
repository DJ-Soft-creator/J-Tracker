import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
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


try:
    from .brain_api_case import BrainApiCase
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase


class BrainIndexResilienceTests(BrainApiCase):
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
        self._write("user-a/notes/Pflanzen.md", "# Pflanzen\n\nMonstera und Farn.\n")
        self._write("user-a/projects/garten/planung.md", "# Garten\n\nPflanzenprojekt.\n")
        (self.data_dir / "user-a/notes/keep.md").write_text(
            "# Keep\n\nExtern geaenderte Notiz.\n",
            encoding="utf-8",
        )

        response = self.client.get("/api/brain/search")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        paths = {item["path"] for item in payload["results"]}
        self.assertIn("notes/Pflanzen.md", paths)
        self.assertIn("projects/garten/planung.md", paths)
        self.assertTrue(payload["index_pending"])
        changed = self.client.get("/api/brain/search?q=extern+geaenderte").get_json()["results"]
        self.assertEqual(len(changed), 1)

        opened = self.client.get(
            "/api/brain/document",
            query_string={"doc_id": "personal:notes/Pflanzen.md"},
        )
        self.assertEqual(opened.status_code, 200)
        self.assertIn("Monstera", opened.get_json()["content"])
        saved = self.client.put("/api/brain/document", headers=self.headers, json={
            "doc_id": "personal:notes/Pflanzen.md",
            "content": "# Pflanzen\n\nMonstera, Farn und Ficus.\n",
            "content_hash": opened.get_json()["content_hash"],
        })
        self.assertEqual(saved.status_code, 200)
        self.assertIn("Ficus", (self.data_dir / "user-a/notes/Pflanzen.md").read_text(encoding="utf-8"))

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

    def test_quick_note_started_before_midnight_sorts_by_its_submit_time(self):
        # The session was begun on 02.08. and submitted after midnight, so the
        # entry lives in the 03.08. journal and must sort among that day.
        self.journal_path.write_text(
            "# Journal 2026-08-03\n\n"
            "___\n\n- 02.08. 23:51:07\nSpaeter Gedanke\n~~03.08. 00:04:22~~\n\n___\n\n"
            "___\n\n- 03.08. 08:00:00\nMorgens\n~~03.08. 08:01:00~~\n\n___\n",
            encoding="utf-8",
        )

        document = brain._build_document("personal", self.data_dir / "user-a", self.journal_path, "journal")

        sort_times = [
            datetime.fromtimestamp(block["sort_at"], brain._local_timezone()).isoformat()
            for block in document["blocks"]
        ]
        self.assertEqual(sort_times[0][:10], "2026-08-03")
        self.assertEqual(sort_times, sorted(sort_times))

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


class BrainIndexSnapshotContractTests(BrainApiCase):
    """The process-wide index snapshot is shared and must stay immutable.

    AI maintenance contract: ``_read_index_documents`` hands out the cached
    documents without copying them. That is only safe as long as
    ``_decorate_documents`` copies whatever it annotates - family and archive
    documents are one object for every account, while the annotations come from
    each user's own metadata file. Both tests below fail loudly if someone
    reintroduces in-place decoration.
    """

    def _family_visible_to_both(self):
        self._write(
            "family/projects/shared.md",
            "---\nassigned_users: [user-a, user-b]\n---\n\n# Shared Family\n\nShared family text\n",
        )
        brain.rebuild_family_index()

    def _shared_block(self, user_id):
        response = self.client.get("/api/brain/search?q=shared+family+text")
        self.assertEqual(response.status_code, 200)
        results = response.get_json()["results"]
        return next(item for item in results if item["path"] == "projects/shared.md")

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf-test"

    def test_one_users_annotation_does_not_reach_another_users_card(self):
        self._family_visible_to_both()
        card = self._shared_block("user-a")

        saved = self.client.post(
            "/api/brain/metadata",
            json={
                "doc_id": card["doc_id"],
                "reference_type": "block",
                "fingerprint": card["fingerprint"],
                "priority": "high",
                "tags": ["nur-fuer-alex"],
            },
            headers=self.headers,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self._shared_block("user-a")["priority"], "high")

        # The annotation belongs to Alex's request, not to the shared document.
        # Checking here rather than only after Bea's request matters: in-place
        # decoration would be overwritten by her own pass and stay invisible.
        for document in brain._read_index_documents("family"):
            for block in document["blocks"]:
                self.assertNotIn("priority", block)
                self.assertNotIn("manual_tags", block)

        self._login("user-b")
        for_bea = self._shared_block("user-b")
        self.assertEqual(for_bea["priority"], "normal")
        self.assertEqual(for_bea["manual_tags"], [])
        self.assertNotIn("nur-fuer-alex", for_bea["tags"])

    def test_the_index_walk_does_not_follow_a_symlinked_directory_out_of_the_root(self):
        # The walk no longer resolves every path to prove it stays inside the
        # root - it simply never descends into a linked directory. Same
        # guarantee, a different mechanism, so it needs its own test.
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "smuggled.md").write_text(
                "# Smuggled\n\nContent from outside the data directory.\n", encoding="utf-8",
            )
            (self.data_dir / "user-a/notes/linked").symlink_to(outside, target_is_directory=True)
            brain.rebuild_user_index("user-a")

            results = self.client.get("/api/brain/search?q=smuggled").get_json()["results"]
            self.assertEqual(results, [])
            paths = [
                item["path"]
                for item in self.client.get("/api/brain/search").get_json()["results"]
            ]
            self.assertFalse([path for path in paths if "linked" in path], paths)

    def test_a_search_leaves_the_cached_snapshot_untouched(self):
        self._family_visible_to_both()
        before = brain._read_index_documents("family")
        self.assertIsNotNone(before)
        fingerprint = json.dumps(before, sort_keys=True, default=str)

        self.assertEqual(self.client.get("/api/brain/search?q=shared").status_code, 200)
        self.assertEqual(self.client.get("/api/brain/tasks").status_code, 200)

        after = brain._read_index_documents("family")
        # Same objects, not just equal ones: the cache must not be copied per
        # request, which is what the removed deepcopy used to cost.
        self.assertIs(after, before)
        self.assertEqual(json.dumps(after, sort_keys=True, default=str), fingerprint)
        for document in after:
            for block in document["blocks"]:
                self.assertNotIn("priority", block)
                self.assertNotIn("manual_tags", block)
