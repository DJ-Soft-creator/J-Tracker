"""Brain search, catalog, file and editor API behavior tests."""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "brain-api-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/opencode/journl-brain-api-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import background_runs
    import brain
    import family
    import main


try:
    from .brain_api_case import BrainApiCase
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase


class BrainApiTests(BrainApiCase):
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

    def test_a_models_hashtag_is_visible_on_the_card_and_marked_as_machine_origin(self):
        import tagging

        content = self.journal_path.read_text(encoding="utf-8")
        body, _ = tagging.strip_footer(content)
        # Mirrors a tagging run: the model's tag joins the block's own tags, and
        # the ``ai`` map records that it was the model who added it.
        blocks = [
            {**block, "raw_tags": sorted(set(block["raw_tags"]) | {"focus"})}
            if block["anchor"] == "2026-08-03 11:00:00" else block
            for block in tagging.journal_blocks(body, "2026-08-03")
        ]
        self.journal_path.write_text(
            body.rstrip() + "\n\n" + tagging.render_footer(
                blocks, [tagging.read_catalog("user-a")],
                {"2026-08-03 11:00:00": ["focus"]}, "2026-08-03T09:00:00+00:00",
            ),
            encoding="utf-8",
        )
        brain.rebuild_user_index("user-a")

        results = self.client.get("/api/brain/search").get_json()["results"]
        card = next(item for item in results if "Stored note" in item["snippet"])

        # The tag is nowhere in the block text, so it can only come from the footer.
        self.assertNotIn("#focus", card["snippet"])
        self.assertIn("focus", card["tags"])
        self.assertEqual(card["ai_tags"], ["focus"])

    def _tag_block_by_model(self, tag, anchor="2026-08-03 11:00:00"):
        """Write ``tag`` into the footer as a machine hashtag, like a tagging run."""
        import tagging

        tagging.propose_tags("user-a", [tag])
        content = self.journal_path.read_text(encoding="utf-8")
        body, _ = tagging.strip_footer(content)
        blocks = [
            {**block, "raw_tags": sorted(set(block["raw_tags"]) | {tag})}
            if block["anchor"] == anchor else block
            for block in tagging.journal_blocks(body, "2026-08-03")
        ]
        self.journal_path.write_text(
            body.rstrip() + "\n\n" + tagging.render_footer(
                blocks, [tagging.read_catalog("user-a")],
                {anchor: [tag]}, "2026-08-03T09:00:00+00:00",
            ),
            encoding="utf-8",
        )
        brain.rebuild_user_index("user-a")

    def test_plain_search_finds_a_hashtag_that_only_lives_in_the_footer(self):
        self._tag_block_by_model("kifokus")

        results = self.client.get("/api/brain/search?q=kifokus").get_json()["results"]
        snippets = [item["snippet"] for item in results]

        self.assertEqual(len(results), 1)
        self.assertIn("Stored note", snippets[0])
        # The hashtag is not in the text at all - only the footer carries it.
        self.assertNotIn("kifokus", snippets[0])

    def test_ki_prefix_narrows_the_search_to_machine_assigned_hashtags(self):
        self._tag_block_by_model("kifokus")

        machine = self.client.get("/api/brain/search?q=ki:kifokus").get_json()["results"]
        any_machine = self.client.get("/api/brain/search?q=ki:").get_json()["results"]
        without_machine = self.client.get("/api/brain/search?q=-ki:").get_json()["results"]
        wrong_tag = self.client.get("/api/brain/search?q=ki:work").get_json()["results"]

        self.assertEqual([item["path"] for item in machine], ["2026/08/03/Journal_2026-08-03.md"])
        self.assertTrue(all("Stored note" in item["snippet"] for item in machine))
        self.assertEqual([item["snippet"] for item in any_machine], [item["snippet"] for item in machine])
        self.assertTrue(all("Stored note" not in item["snippet"] for item in without_machine))
        # ``work`` is the user's own hashtag on that block, not the model's.
        self.assertEqual(wrong_tag, [])

    def test_the_tag_list_says_how_many_hits_a_model_assigned(self):
        self._tag_block_by_model("kifokus")

        tags = {item["name"]: item for item in self.client.get("/api/brain/tags").get_json()["tags"]}

        # Machine origin is a property of the hit, not of the hashtag: #work is
        # the user's own on both blocks, #kifokus exists only through the model.
        self.assertEqual((tags["kifokus"]["count"], tags["kifokus"]["ai_count"]), (1, 1))
        self.assertEqual(tags["work"]["ai_count"], 0)

    def test_a_ki_query_narrows_the_block_facets_to_what_the_model_tagged(self):
        self._tag_block_by_model("kifokus")

        unfiltered = self.client.get("/api/brain/tags?facet=block").get_json()["tags"]
        machine = self.client.get("/api/brain/tags?facet=block&q=ki:").get_json()["tags"]

        self.assertEqual({item["name"] for item in machine if item["count"]}, {"focus", "kifokus", "work"})
        # Only the model-tagged block counts now, so #work drops from two to one.
        self.assertEqual({item["name"]: item["count"] for item in unfiltered}["work"], 2)
        self.assertEqual({item["name"]: item["count"] for item in machine}["work"], 1)

    def test_block_facets_only_count_the_tags_of_the_matching_blocks(self):
        document_wide = self.client.get("/api/brain/tags?tags=focus").get_json()["tags"]
        block_wide = self.client.get("/api/brain/tags?tags=focus&facet=block").get_json()["tags"]

        counts = {item["name"]: item["count"] for item in document_wide}
        drilled = {item["name"]: item["count"] for item in block_wide}

        # The day's other block carries #work without #focus: it belongs to the
        # document count, but must not widen the drill-down under #focus.
        self.assertEqual(counts["work"], 2)
        self.assertEqual(drilled["work"], 1)
        self.assertEqual(drilled["focus"], 1)

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

    def test_proposed_tags_are_offered_and_usable_as_filters(self):
        """A hashtag must be findable before one decides whether to approve it."""
        self._write("user-a/notes/fresh.md", "# Fresh\n\nStill undecided #unapproved note.\n")

        tags = self.client.get("/api/brain/tags").get_json()["tags"]
        proposal = next(item for item in tags if item["name"] == "unapproved")
        self.assertEqual(proposal["count"], 1)
        self.assertEqual(proposal["scope"], "personal")

        results = self.client.get("/api/brain/search?tags=unapproved").get_json()["results"]
        self.assertEqual([item["path"] for item in results], ["notes/fresh.md"])
        notes = self.client.get("/api/brain/notes?tags=unapproved").get_json()["notes"]
        self.assertEqual([item["path"] for item in notes], ["notes/fresh.md"])
        excluded = self.client.get("/api/brain/search?exclude_tags=unapproved").get_json()["results"]
        self.assertNotIn("notes/fresh.md", {item["path"] for item in excluded})

    def test_search_filters_visible_blocks_by_tag(self):
        response = self.client.get("/api/brain/search?tags=focus")

        self.assertEqual(response.status_code, 200)
        results = response.get_json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "2026/08/03/Journal_2026-08-03.md")
        self.assertIn("focus", results[0]["tags"])
        opened = self.client.get("/api/brain/document", query_string={
            "doc_id": results[0]["doc_id"], "fingerprint": results[0]["fingerprint"],
        })
        self.assertEqual(opened.status_code, 200)
        block = opened.get_json()
        self.assertEqual(block["block_content"], results[0]["snippet"])
        self.assertLessEqual(block["block_start_line"], block["block_end_line"])

    def test_search_excludes_blocks_matching_excluded_tag(self):
        baseline = self.client.get("/api/brain/search").get_json()["results"]
        response = self.client.get("/api/brain/search?exclude_tags=focus")

        self.assertEqual(response.status_code, 200)
        results = response.get_json()["results"]
        self.assertEqual(len(results), len(baseline) - 1)
        self.assertFalse(any("focus" in item["tags"] for item in results))
        self.assertTrue(any(item["path"] == "2026/08/03/Journal_2026-08-03.md" for item in results))

        combined = self.client.get("/api/brain/search?tags=work&exclude_tags=focus").get_json()["results"]
        self.assertEqual(len(combined), 1)
        self.assertIn("work", combined[0]["tags"])
        self.assertNotIn("focus", combined[0]["tags"])

    def test_tags_endpoint_narrows_facet_by_excluded_tag(self):
        tags = self.client.get("/api/brain/tags").get_json()["tags"]
        self.assertIn("focus", {item["name"] for item in tags})

        narrowed = self.client.get("/api/brain/tags?exclude_tags=focus").get_json()["tags"]
        self.assertNotIn("focus", {item["name"] for item in narrowed})

    def test_theme_group_widens_the_result_instead_of_narrowing_it(self):
        """``any_tags`` is what makes a second, unrelated topic reachable at all."""
        self._write("user-a/notes/fresh.md", "# Fresh\n\nStill undecided #unapproved note.\n")

        drilled = self.client.get("/api/brain/search?tags=focus&tags=unapproved").get_json()["results"]
        self.assertEqual(drilled, [])

        widened = self.client.get("/api/brain/search?any_tags=focus&any_tags=unapproved").get_json()
        paths = {item["path"] for item in widened["results"]}
        self.assertEqual(paths, {"2026/08/03/Journal_2026-08-03.md", "notes/fresh.md"})

    def test_selected_tags_refine_the_theme_group(self):
        """Themes are an OR gate, the drill-down tags narrow what got through."""
        self._write("user-a/notes/fresh.md", "# Fresh\n\nStill undecided #unapproved note.\n")

        response = self.client.get(
            "/api/brain/search?any_tags=focus&any_tags=unapproved&tags=work"
        ).get_json()["results"]

        self.assertEqual([item["path"] for item in response], ["2026/08/03/Journal_2026-08-03.md"])
        self.assertIn("focus", response[0]["tags"])

    def test_excluded_tag_beats_the_theme_group(self):
        combined = self.client.get("/api/brain/search?any_tags=focus&exclude_tags=focus")

        self.assertEqual(combined.status_code, 200)
        self.assertEqual(combined.get_json()["results"], [])

    def test_facets_cover_every_theme_of_the_group(self):
        """The pickers must keep offering the siblings of all themes, not only one."""
        self._write("user-a/notes/fresh.md", "# Fresh\n\nStill undecided #unapproved note.\n")

        narrowed = self.client.get("/api/brain/tags?facet=block&tags=focus").get_json()["tags"]
        self.assertNotIn("unapproved", {item["name"] for item in narrowed})

        widened = self.client.get(
            "/api/brain/tags?facet=block&any_tags=focus&any_tags=unapproved"
        ).get_json()["tags"]
        names = {item["name"] for item in widened}
        self.assertIn("focus", names)
        self.assertIn("unapproved", names)
        self.assertIn("work", names)

    def test_file_lists_honour_the_theme_group(self):
        self._write("user-a/notes/tagged.md", "# Tagged\n\nRelevant #focus note.\n")
        self._write("user-a/notes/fresh.md", "# Fresh\n\nStill undecided #unapproved note.\n")

        notes = self.client.get("/api/brain/notes?any_tags=focus&any_tags=unapproved").get_json()["notes"]

        self.assertEqual(
            {item["path"] for item in notes},
            {"notes/tagged.md", "notes/fresh.md"},
        )

    def test_file_lists_exclude_tagged_files(self):
        self._write("user-a/notes/tagged.md", "# Tagged\n\nRelevant #focus note.\n")
        self._write("user-a/notes/other.md", "# Other\n\nUnrelated #work note.\n")

        notes = self.client.get("/api/brain/notes?exclude_tags=focus").get_json()["notes"]
        paths = {item["path"] for item in notes}
        self.assertIn("notes/other.md", paths)
        self.assertIn("notes/keep.md", paths)
        self.assertNotIn("notes/tagged.md", paths)

    def test_tag_count_deduplicates_tags_in_the_same_visible_block(self):
        documents = [{"blocks": [{"fingerprint": "block-1", "tags": ["focus", "focus"]}]}]
        with mock.patch.object(brain, "_visible_documents", return_value=(documents, False)):
            response = self.client.get("/api/brain/tags")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["tags"],
            [{"name": "focus", "count": 1, "ai_count": 0, "scope": "personal"}],
        )

    def test_tag_filter_only_offers_tags_from_matching_documents(self):
        catalog = {
            "personal": {"canonical": ["focus", "work", "plan"], "aliases": {}, "ai_workflows": {}},
            "family": {"canonical": [], "aliases": {}, "ai_workflows": {}},
        }
        documents = [
            {"source": "personal", "blocks": [{"tags": ["focus", "work"]}]},
            {"source": "personal", "blocks": [{"tags": ["focus", "plan"]}]},
            {"source": "personal", "blocks": [{"tags": ["work"]}]},
        ]
        with mock.patch.object(brain, "_visible_documents", return_value=(documents, False)), mock.patch.object(
            brain.tagging_module, "catalog_view", return_value=catalog
        ):
            response = self.client.get("/api/brain/tags?tags=focus")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item["name"] for item in response.get_json()["tags"]},
            {"focus", "work", "plan"},
        )

    def test_a_chat_session_in_the_day_folder_is_indexed_and_read_only(self):
        """Der Chatverlauf liegt neben dem Journal — und muss auffindbar sein.

        Ohne die Art ``chat`` in ``_personal_kind`` waere die Datei fuer den Index
        unsichtbar: sie liegt im Tagesordner, heisst aber nicht ``Journal_*.md``.
        """
        self._write(
            "user-a/2026/08/31/Chat_2026-08-31_14-05-12.md",
            "---\nid: c1\ntype: brain-chat-session\nmodel: local\n---\n\n"
            "# Chat mit den Notizen\n\n___\n\n"
            "## Frage | Datum & Uhrzeit: 2026-08-31 14:05:12\n\nWorum ging es bei Segelboot?\n",
        )
        brain.rebuild_user_index("user-a")

        results = self.client.get("/api/brain/search?q=Segelboot").get_json()["results"]
        paths = {item["path"] for item in results}
        self.assertIn("2026/08/31/Chat_2026-08-31_14-05-12.md", paths)
        found = next(item for item in results if item["path"].startswith("2026/08/31/Chat"))
        self.assertEqual(found["kind"], "chat")
        self.assertTrue(found["read_only"])

    def test_a_chat_session_is_not_mistaken_for_a_journal(self):
        """Die Verschlagwortung darf den Chat nie anfassen — er ist kein Journal."""
        self._write(
            "user-a/2026/08/31/Chat_2026-08-31_14-05-12.md",
            "---\ntype: brain-chat-session\n---\n\n# Chat\n\nEin Verlauf.\n",
        )
        journals = [path.name for path in brain.tagging_module.journal_paths("user-a")]
        self.assertNotIn("Chat_2026-08-31_14-05-12.md", journals)
        self.assertEqual(brain.ignored_markdown_files("user-a")["count"], 0)

    def test_historical_tagging_start_requires_a_saved_local_connection(self):
        """Ohne gespeicherten lokalen Endpunkt startet kein Lauf."""
        with mock.patch.object(background_runs, "start", return_value={
            "id": "run-1", "status": "queued"
        }) as start_run:
            missing = self.client.post(
                "/api/brain/tagging/start",
                json={"start_date": "2026-08-01", "end_date": "2026-08-04"},
                headers=self.headers,
            )
            brain.historical_tagging.save_settings(
                "user-a", {"base_url": "http://127.0.0.1:1234", "model": "local"},
            )
            local = self.client.post(
                "/api/brain/tagging/start",
                json={"start_date": "2026-08-01", "end_date": "2026-08-04"},
                headers=self.headers,
            )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(local.status_code, 202)
        self.assertEqual(start_run.call_count, 1)
        self.assertEqual(start_run.call_args.args[1], brain.historical_tagging.RUN_KIND)

    def test_tagging_settings_and_models_refuse_a_public_endpoint(self):
        """Die Verbindung bleibt lokal — sonst waeren Journale unterwegs."""
        remote = self.client.post(
            "/api/brain/tagging/settings",
            json={"base_url": "http://example.com:1234", "model": "remote"},
            headers=self.headers,
        )
        saved = self.client.post(
            "/api/brain/tagging/settings",
            json={"base_url": "http://127.0.0.1:1234", "model": "local",
                  "daily": True, "daily_hour": 7},
            headers=self.headers,
        )
        bad_hour = self.client.post(
            "/api/brain/tagging/settings",
            json={"base_url": "http://127.0.0.1:1234", "model": "local", "daily_hour": 24},
            headers=self.headers,
        )
        public = self.client.get("/api/brain/tagging/settings")

        self.assertEqual(remote.status_code, 400)
        self.assertEqual(bad_hour.status_code, 400)
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.get_json()["configured"])
        self.assertEqual(public.get_json()["base_url"], "http://127.0.0.1:1234")
        self.assertTrue(public.get_json()["enabled"])
        self.assertEqual(public.get_json()["schedule_time"], "07:00")
        self.assertNotIn("api_key", public.get_json())

    def test_the_tagging_job_runs_with_its_own_range_and_mode(self):
        brain.historical_tagging.save_settings(
            "user-a", {"base_url": "http://127.0.0.1:1234", "model": "local",
                       "range_days": 3, "mode": "refresh"},
        )
        started = {}

        def fake_start(user_id, kind, params, **options):
            started.update(params=params, options=options)
            return {"id": "run-2", "kind": kind, "status": "queued", "params": params}

        with mock.patch.object(background_runs, "start", side_effect=fake_start), \
                mock.patch.object(brain.historical_tagging, "_local_now",
                                  return_value=datetime(2026, 10, 5, 9, 0)):
            response = self.client.post("/api/brain/tagging/job/run", json={"preview": True},
                                        headers=self.headers)
        job = self.client.get("/api/brain/tagging/job")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            {key: started["params"][key] for key in ("start_date", "end_date", "mode", "preview")},
            {"start_date": "2026-10-03", "end_date": "2026-10-05", "mode": "refresh", "preview": True},
        )
        self.assertEqual(response.get_json()["run"]["scope"]["mode"], "refresh")
        self.assertEqual(job.status_code, 200)
        self.assertEqual(job.get_json()["settings"]["range_days"], 3)
        self.assertNotIn("api_key", job.get_json()["settings"])

    def test_the_tagging_job_needs_a_connection_and_the_csrf_token(self):
        refused = self.client.post("/api/brain/tagging/job/run", json={}, headers=self.headers)
        no_token = self.client.post("/api/brain/tagging/job/run", json={})
        self.assertEqual(refused.status_code, 400)
        self.assertIn(no_token.status_code, (400, 403))

    def test_historical_tagging_start_refuses_a_second_run(self):
        """Zwei Laeufe wuerden dieselben Journale gleichzeitig umschreiben."""
        brain.historical_tagging.save_settings(
            "user-a", {"base_url": "http://127.0.0.1:1234", "model": "local"},
        )
        running = {"id": "run-1", "kind": brain.historical_tagging.RUN_KIND, "status": "running"}
        with mock.patch.object(
            background_runs, "start", side_effect=background_runs.RunConflict("läuft schon")
        ), mock.patch.object(background_runs, "active", return_value=running):
            response = self.client.post(
                "/api/brain/tagging/start",
                json={"start_date": "2026-08-01", "end_date": "2026-08-04"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["run"]["id"], "run-1")

    def test_historical_tagging_start_validates_the_date_range(self):
        with mock.patch.object(main, "load_config", return_value={}):
            reversed_range = self.client.post(
                "/api/brain/tagging/start",
                json={"start_date": "2026-08-04", "end_date": "2026-08-01"},
                headers=self.headers,
            )
            stale_without_date = self.client.post(
                "/api/brain/tagging/start",
                json={"start_date": "2026-08-01", "end_date": "2026-08-04", "mode": "stale"},
                headers=self.headers,
            )

        self.assertEqual(reversed_range.status_code, 400)
        self.assertEqual(stale_without_date.status_code, 400)

    def test_personal_file_lists_are_separated_recursive_and_filterable(self):
        self._write("user-a/notes/garden/plants.md", "# Plants\n\nMonstera and fern.\n")
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
        self.assertEqual(note_paths, {"notes/keep.md", "notes/garden/plants.md", "notes/empty.md"})
        self.assertEqual(project_paths, {"projects/alpha.md", "projects/home/renovation.md"})
        self.assertNotIn("notes/README.md", note_paths)
        self.assertNotIn("projects/visible.md", project_paths)

        nested = next(item for item in notes if item["path"] == "notes/garden/plants.md")
        self.assertEqual(nested["doc_id"], "personal:notes/garden/plants.md")
        self.assertEqual(nested["kind"], "note")
        self.assertEqual(nested["title"], "Plants")
        self.assertTrue(nested["modified_at"].endswith("+00:00"))
        self.assertEqual(
            [item["path"] for item in self.client.get("/api/brain/notes?q=monstera").get_json()["notes"]],
            ["notes/garden/plants.md"],
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
        # Die Karte zeigt die Formulierung, nicht die Maschinenzeile der Datei.
        task = next(item for item in tasks if item["text"] == "Shared task (fällig 2026-08-03)")
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
        self.assertEqual(shared, {"name": "shared", "count": 0, "ai_count": 0, "scope": "family"})
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



if __name__ == "__main__":
    unittest.main()
