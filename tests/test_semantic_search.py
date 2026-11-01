"""Semantische Suche: Zerlegen, Abgleich, Isolation, Fusion, Einstellungen, Routen.

Qdrant und der Embedding-Endpunkt sind durch Attrappen ersetzt; geprueft wird,
was die App mit ihnen macht. Die wichtigste Zusage steht in
``SemanticIsolationTest``: kein Konto sieht je einen Treffer, den es in der
Brain-Suche nicht auch saehe.
"""

import json
import unittest
from datetime import datetime
from unittest import mock

try:
    from .brain_api_case import FLASK_AVAILABLE, BrainApiCase
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import FLASK_AVAILABLE, BrainApiCase

if FLASK_AVAILABLE:
    import brain
    import brain_chat
    import mcp_server
    import mcp_tools
    import embeddings
    import semantic_index
    import semantic_search
    import semantic_settings
    import vector_store


def _unit(seed):
    vector = [0.0] * embeddings.DIMS
    vector[seed % embeddings.DIMS] = 1.0
    return vector


class FakeStore:
    """Qdrant im Speicher: Collections -> {id: payload}."""

    def __init__(self):
        self.collections = {}
        self.forced_hits = []

    def install(self, case):
        patches = {
            "available": lambda: True,
            "ensure_collection": lambda name, dims: self.collections.setdefault(name, {}),
            "delete_collection": lambda name: self.collections.pop(name, None),
            "stored_hashes": lambda name: {
                pid: (payload["semantic_hash"], payload["model"])
                for pid, payload in self.collections.get(name, {}).items()
            },
            "delete_points": lambda name, ids: [self.collections.get(name, {}).pop(pid, None) for pid in ids],
            "upsert": lambda name, points: self.collections.setdefault(name, {}).update(
                {point["id"]: point["payload"] for point in points}
            ),
            "search": self.search,
            "point_vector": lambda name, point_id: _unit(1) if point_id in self.collections.get(name, {}) else None,
        }
        for name, replacement in patches.items():
            patcher = mock.patch.object(vector_store, name, replacement)
            patcher.start()
            case.addCleanup(patcher.stop)

    def search(self, name, vector, **kwargs):
        hits = [
            {"id": pid, "score": 0.8, "payload": payload}
            for pid, payload in self.collections.get(name, {}).items()
            if not kwargs.get("sources") or payload["source"] in kwargs["sources"]
        ]
        return hits + [hit for hit in self.forced_hits if hit.pop("collection", name) == name]


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class SemanticBuildingBlocksTest(BrainApiCase):
    def test_truncate_keeps_dims_and_unit_length(self):
        vector = embeddings.truncate([3.0, 4.0] + [0.0] * 2558)
        self.assertEqual(len(vector), embeddings.DIMS)
        self.assertAlmostEqual(sum(value * value for value in vector), 1.0)
        with self.assertRaises(embeddings.EmbeddingError):
            embeddings.truncate([1.0] * 10)

    def test_chunk_text_splits_long_text_with_overlap(self):
        text = "\n\n".join("Absatz {} ".format(number) + "wort " * 80 for number in range(20))
        chunks = semantic_index.chunk_text(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= semantic_index.CHUNK_CHARS for chunk in chunks))
        self.assertEqual(semantic_index.chunk_text("kurz"), ["kurz"])
        self.assertEqual(semantic_index.chunk_text("   "), [])

    def test_clean_text_drops_markers_and_hashes(self):
        raw = ('<!-- jt:media {"sha256": "' + "ab12" * 16 + '"} -->\n'
               "Sprachnachricht | Aufnahmezeit: 08:15\n\n\n\n"
               "Rücken tut weh 3f2a1c4e-9b8d-4e7f-a6c5-1d2e3f4a5b6c #gesundheit")
        cleaned = semantic_index.clean_text(raw)
        self.assertNotIn("jt:media", cleaned)
        self.assertNotIn("3f2a1c4e", cleaned)
        self.assertNotIn("\n\n\n", cleaned)
        self.assertIn("Rücken tut weh", cleaned)
        self.assertIn("#gesundheit", cleaned)
        self.assertEqual(semantic_index.clean_text("<!-- jt:media {} -->"), "")

    def test_planned_points_cover_sources_and_skip_technical_files(self):
        documents = semantic_index.personal_documents("user-a")
        planned = semantic_index.planned_points(documents, "embed-model")
        sources = {item["payload"]["source"] for item in planned.values()}
        paths = {item["payload"]["path"] for item in planned.values()}
        self.assertIn("journal", sources)
        self.assertIn("notes", sources)
        self.assertNotIn("notes/README.md", paths)
        self.assertEqual(planned, semantic_index.planned_points(documents, "embed-model"))
        journal = next(item for item in planned.values() if item["payload"]["source"] == "journal")
        self.assertTrue(journal["text"].startswith("Journal · 2026-08-03"))
        self.assertEqual(journal["payload"]["day"], 20260803)

    def test_user_collections_are_distinct_and_validated(self):
        self.assertNotEqual(vector_store.user_collection("user-a"), vector_store.user_collection("user-b"))
        for bad in ("", "../x", "a b", None):
            with self.assertRaises(vector_store.VectorStoreError):
                vector_store.user_collection(bad)

    def test_fuse_marks_origin(self):
        words = [{"key": "a", "word_score": 2.0}, {"key": "b", "word_score": 1.0}]
        vectors = [{"key": "b", "vector_score": 0.7}, {"key": "c", "vector_score": 0.6}]
        fused = semantic_search.fuse(words, vectors)
        self.assertEqual(fused[0]["key"], "b")
        self.assertEqual({item["key"]: item["origin"] for item in fused}, {"a": "word", "b": "both", "c": "semantic"})

    def test_http_502_names_a_chat_profile(self):
        error = embeddings.urlerror.HTTPError("http://x", 502, "Bad Gateway", {}, None)
        with mock.patch.object(embeddings, "_post", side_effect=error), \
                mock.patch.object(embeddings.agent_projects, "validated_base_url", return_value="http://10.0.0.2:8080"):
            with self.assertRaises(embeddings.EmbeddingError) as raised:
                embeddings.probe({"base_url": "http://10.0.0.2:8080", "model": "chat-model"})
        self.assertIn("Embedding-Modell", str(raised.exception))

    def test_job_due_respects_time_once_per_day_and_retries(self):
        settings = {"job_enabled": True, "schedule_time": "03:00"}
        night = datetime(2026, 10, 31, 3, 5)
        self.assertFalse(semantic_index.job_due(settings, {}, datetime(2026, 10, 31, 2, 59)))
        self.assertTrue(semantic_index.job_due(settings, {}, night))
        self.assertFalse(semantic_index.job_due(settings, {"done_on": "2026-10-31"}, night))
        exhausted = {"day": "2026-10-31", "attempts": semantic_index.MAX_ATTEMPTS}
        self.assertFalse(semantic_index.job_due(settings, exhausted, night))
        self.assertFalse(semantic_index.job_due({**settings, "job_enabled": False}, {}, night))


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class SemanticSyncTest(BrainApiCase):
    def setUp(self):
        super().setUp()
        self.store = FakeStore()
        self.store.install(self)
        self.embedded = []

        def embed(connection, texts, batch_size=8):
            self.embedded.extend(texts)
            return [_unit(len(self.embedded) + index) for index in range(len(texts))]

        patcher = mock.patch.object(embeddings, "embed_documents", embed)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.connection = {"base_url": "http://10.0.0.2:8080", "model": "embed-a", "api_key": ""}

    def _sync(self, connection=None):
        name = vector_store.user_collection("user-a")
        return semantic_index.sync_collection(
            name, semantic_index.personal_documents("user-a"), connection or self.connection, batch_size=4,
        )

    def test_second_run_embeds_nothing_and_changes_are_picked_up(self):
        first = self._sync()
        self.assertGreater(first["embedded"], 0)
        self.assertEqual(self._sync()["embedded"], 0)
        self._write("user-a/notes/keep.md", "# Keep\n\nA changed note.\n")
        self.assertEqual(self._sync()["embedded"], 1)
        (self.data_dir / "user-a/notes/keep.md").unlink()
        self.assertEqual(self._sync()["removed"], 1)

    def test_model_change_reembeds_everything(self):
        first = self._sync()
        changed = self._sync({**self.connection, "model": "embed-b"})
        self.assertEqual(changed["embedded"], first["planned"])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class SemanticIsolationTest(BrainApiCase):
    def setUp(self):
        super().setUp()
        self.store = FakeStore()
        self.store.install(self)
        self._patch(embeddings, "embed_documents", lambda connection, texts, batch_size=8: [_unit(1)] * len(texts))
        self._patch(embeddings, "embed_query", lambda connection, query: _unit(1))
        self._patch(semantic_settings, "load_global", lambda: {
            "base_url": "http://10.0.0.2:8080", "model": "embed-a", "api_key": "", "job_enabled": False,
            "schedule_time": "03:00", "batch_size": 8, "min_score": 0.4,
        })
        self._write("user-b/notes/private.md", "# Private\n\nNur fuer Bea.\n")
        connection = semantic_settings.connection(semantic_settings.load_global())
        for user_id in ("user-a", "user-b"):
            semantic_index.sync_collection(
                vector_store.user_collection(user_id), semantic_index.personal_documents(user_id),
                connection, batch_size=8,
            )
        semantic_index.sync_collection(
            vector_store.FAMILY_COLLECTION, semantic_index.family_documents(), connection, batch_size=8,
        )

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _paths(self, user_id):
        payload = semantic_search.search(user_id, "irgendwas", sources=list(semantic_settings.SOURCES),
                                         mode="semantic", limit=200)
        self.assertEqual(payload["semantic"]["status"], "ok")
        return {item["path"] for item in payload["results"]}

    def test_family_collection_holds_secret_but_user_a_never_sees_it(self):
        family_paths = {payload["path"] for payload in self.store.collections[vector_store.FAMILY_COLLECTION].values()}
        self.assertIn("projects/secret.md", family_paths)
        paths = self._paths("user-a")
        self.assertIn("projects/visible.md", paths)
        self.assertNotIn("projects/secret.md", paths)
        self.assertNotIn("notes/private.md", paths)

    def test_foreign_hit_injected_into_own_collection_is_dropped(self):
        own = vector_store.user_collection("user-a")
        self.store.forced_hits.append({
            "collection": own, "id": "x", "score": 0.99,
            "payload": {"source": "notes", "path": "notes/private.md", "anchor": "line:1", "heading": "# Private"},
        })
        self.assertNotIn("notes/private.md", self._paths("user-a"))

    def test_semantic_outage_still_returns_word_hits(self):
        self._patch(embeddings, "embed_query", mock.Mock(side_effect=embeddings.EmbeddingError("Mac schläft")))
        payload = semantic_search.search("user-a", "lasting", sources=["notes"], mode="hybrid")
        self.assertEqual(payload["semantic"], {"status": "unavailable", "reason": "Mac schläft"})
        self.assertIn("notes/keep.md", {item["path"] for item in payload["results"]})

    def test_cards_route_serves_brain_cards_without_foreign_hits(self):
        semantic_settings.save_user("user-a", {"enabled": True, "sources": list(semantic_settings.SOURCES)})
        payload = self.client.get("/api/brain/semantic/cards?q=irgendwas").get_json()
        self.assertEqual(payload["semantic"]["status"], "ok")
        self.assertEqual(payload["order"], "relevance")
        paths = {card["path"] for card in payload["results"]}
        self.assertIn("projects/visible.md", paths)
        self.assertNotIn("projects/secret.md", paths)
        self.assertNotIn("notes/private.md", paths)
        card = payload["results"][0]
        for field in ("fingerprint", "doc_id", "snippet", "source_label", "origin"):
            self.assertIn(field, card)

    def test_cards_route_sends_nothing_when_semantic_side_fails(self):
        semantic_settings.save_user("user-a", {"enabled": True})
        self._patch(embeddings, "embed_query", mock.Mock(side_effect=embeddings.EmbeddingError("Mac schläft")))
        payload = self.client.get("/api/brain/semantic/cards?q=lasting").get_json()
        self.assertEqual(payload, {"results": [], "semantic": {"status": "unavailable", "reason": "Mac schläft"}})

    def test_similar_route_lists_related_cards_but_not_the_card_itself(self):
        self._enable()
        card = self.client.get("/api/brain/semantic/cards?q=irgendwas").get_json()["results"][0]
        response = self.client.get("/api/brain/semantic/similar?doc_id={}&fingerprint={}".format(
            card["doc_id"], card["fingerprint"]))
        self.assertEqual(response.status_code, 200, response.get_json())
        results = response.get_json()["results"]
        self.assertTrue(results)
        self.assertNotIn(card["fingerprint"], {item["fingerprint"] for item in results})
        self.assertNotIn("projects/secret.md", {item["path"] for item in results})
        self.assertNotIn("notes/private.md", {item["path"] for item in results})
        missing = self.client.get("/api/brain/semantic/similar?doc_id=nope&fingerprint=nope")
        self.assertEqual(missing.status_code, 404)

    def test_cards_route_without_opt_in_is_off(self):
        payload = self.client.get("/api/brain/semantic/cards?q=lasting").get_json()
        self.assertEqual(payload["semantic"]["status"], "off")
        self.assertEqual(payload["results"], [])


    # ─── MCP und Brain-Chat ──────────────────────────────────────────
    def _enable(self):
        semantic_settings.save_user("user-a", {"enabled": True, "sources": list(semantic_settings.SOURCES)})

    def test_mcp_areas_stay_apart(self):
        self._enable()
        personal = mcp_tools.run_semantic_search("user-a", query="irgendwas", sources=("personal",), limit=30)
        self.assertTrue(personal["results"])
        self.assertEqual({hit["source"] for hit in personal["results"]}, {"personal"})
        family = mcp_tools.run_semantic_search("user-a", query="irgendwas", sources=("family",), limit=30)
        paths = {hit["path"] for hit in family["results"]}
        self.assertEqual({hit["source"] for hit in family["results"]}, {"family"})
        self.assertIn("projects/visible.md", paths)
        self.assertNotIn("projects/secret.md", paths)
        with self.assertRaises(mcp_tools.ToolInputError):
            mcp_tools.run_semantic_search("user-a", query="x", sources=("personal",), include=["family"])

    def test_find_similar_excludes_itself_and_respects_area(self):
        self._enable()
        hit = mcp_tools.run_semantic_search("user-a", query="irgendwas", sources=("personal",))["results"][0]
        similar = mcp_tools.run_find_similar("user-a", path=hit["path"], anchor=hit["anchor"], sources=("personal",))
        self.assertNotIn((hit["path"], hit["anchor"]), {(item["path"], item["anchor"]) for item in similar["results"]})
        self.assertEqual({item["source"] for item in similar["results"]}, {"personal"})
        with self.assertRaises(mcp_tools.ToolInputError):
            mcp_tools.run_find_similar("user-a", path=hit["path"], anchor=hit["anchor"], sources=("family",))
        with self.assertRaises(mcp_tools.ToolInputError):
            mcp_tools.run_find_similar("user-a", path="projects/secret.md", anchor="line:1", sources=("family",))

    def test_mcp_tools_need_the_personal_opt_in(self):
        with self.assertRaises(mcp_tools.ToolUnavailable):
            mcp_tools.run_semantic_search("user-a", query="irgendwas", sources=("personal",))
        names = {tool["name"] for tool in mcp_server.tools_for(mcp_server.AREAS["family"])}
        self.assertIn("semantic_search", names)
        self.assertIn("find_similar", names)

    def test_brain_chat_gets_semantic_tools_only_with_its_switch(self):
        self._enable()
        names = lambda settings: {tool["function"]["name"] for tool in brain_chat.tool_specs(settings)}
        self.assertNotIn("semantic_search", names({}))
        self.assertIn("semantic_search", names({"semantic_search": True}))
        with self.assertRaises(mcp_tools.ToolInputError):
            brain_chat.run_tool("user-a", "semantic_search", {"query": "irgendwas"}, {})
        payload = brain_chat.run_tool("user-a", "semantic_search", {"query": "irgendwas"}, {"semantic_search": True})
        self.assertEqual({hit["source"] for hit in payload["results"]}, {"personal"})
        saved = brain_chat.save_settings("user-a", {"semantic_search": True})
        self.assertTrue(saved["semantic_search"])
        self.assertTrue(brain_chat.public_settings(brain_chat.load_settings("user-a"))["semantic_search"])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class SemanticRoutesTest(BrainApiCase):
    def _make_admin(self, user_id="user-a"):
        data = json.loads((self.data_dir / "users.json").read_text(encoding="utf-8"))
        for user in data["users"]:
            user["admin"] = user["id"] == user_id
        (self.data_dir / "users.json").write_text(json.dumps(data), encoding="utf-8")

    def test_non_admin_cannot_touch_global_settings(self):
        response = self.client.get("/api/brain/semantic/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("global", response.get_json())
        for path in ("/api/brain/semantic/settings/global", "/api/brain/semantic/index/run", "/api/brain/semantic/test"):
            self.assertEqual(self.client.post(path, json={}, headers=self.headers).status_code, 403, path)
        self.assertEqual(self.client.get("/api/brain/semantic/status").status_code, 403)

    def test_admin_settings_hide_api_key(self):
        self._make_admin()
        with mock.patch.object(semantic_settings.agent_projects, "validated_base_url", return_value="http://10.0.0.2:8080"):
            response = self.client.post("/api/brain/semantic/settings/global", headers=self.headers, json={
                "base_url": "http://10.0.0.2:8080", "model": "qwen3-embed-4b-e", "api_key": "geheim",
                "schedule_time": "02:30",
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn("geheim", response.get_data(as_text=True))
        settings = self.client.get("/api/brain/semantic/settings").get_json()
        self.assertTrue(settings["global"]["has_api_key"])
        self.assertEqual(settings["global"]["schedule_time"], "02:30")
        self.assertNotIn("geheim", json.dumps(settings))

    def test_user_settings_roundtrip_and_reject_unknown_sources(self):
        response = self.client.post("/api/brain/semantic/settings/user", headers=self.headers,
                                    json={"enabled": True, "sources": ["journal", "family", "evil"]})
        self.assertEqual(response.get_json()["user"], {"enabled": True, "sources": ["journal", "family"], "rerank": False})
        self.assertEqual(semantic_settings.load_user("user-b")["enabled"], False)

    def test_search_without_opt_in_stays_word_only(self):
        payload = self.client.get("/api/brain/semantic/search?q=lasting&source=notes").get_json()
        self.assertEqual(payload["semantic"]["status"], "off")
        self.assertIn("notes/keep.md", {item["path"] for item in payload["results"]})


if __name__ == "__main__":
    unittest.main()
