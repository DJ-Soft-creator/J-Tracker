"""Protocol, authentication and isolation guarantees of the /mcp endpoint."""

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
MODERN = "2026-07-28"

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "mcp-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-mcp-import")
    sys.path.insert(0, str(ROOT / "app"))
    import brain
    import main
    import mcp_server
    import mcp_tools
    import tagging


@unittest.skipUnless(FLASK_AVAILABLE, "Flask/Pillow are not installed")
class McpServerTests(unittest.TestCase):
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
        self._write_journals()
        self.enqueue = mock.patch.object(brain, "enqueue_rebuild", return_value=True)
        self.enqueue.start()

    def tearDown(self):
        self.enqueue.stop()
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

    def _write_journals(self):
        for owner in ("user-a", "user-b"):
            tagging.update_catalog(owner, False, "approve", "focus")
            tagging.update_catalog(owner, False, "approve", "work")
        self._write(
            "user-a/2026/08/03/Journal_2026-08-03.md",
            "___\n\n## A | Datum & Uhrzeit: 2026-08-03 12:00:00\nErster Eintrag #focus #work\n\n___\n",
        )
        self._write(
            "user-a/2026/08/05/Journal_2026-08-05.md",
            "___\n\n## B | Datum & Uhrzeit: 2026-08-05 12:00:00\nZweiter Eintrag #focus\n\n___\n",
        )
        self._write(
            "user-b/2026/08/03/Journal_2026-08-03.md",
            "___\n\n## Fremd | Datum & Uhrzeit: 2026-08-03 09:00:00\nGeheim #focus\n\n___\n",
        )
        tagging.rebuild_index(["user-a", "user-b"])

    def token(self, user_id="user-a", scopes=("mcp",)):
        client = main.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf"
        response = client.post(
            "/api/share/token", headers={"X-CSRF-Token": "csrf"}, json={"scopes": list(scopes)},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["token"]

    def legacy(self, token, method, params=None, rpc_id=1, path="/mcp"):
        body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}
        return self.client.post(path, headers={"Authorization": "Bearer " + token}, json=body)

    def modern(self, token, method, params=None, rpc_id=1, headers=None):
        params = {**(params or {}), "_meta": {mcp_server.VERSION_META_KEY: MODERN}}
        body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        sent = {
            "Authorization": "Bearer " + token,
            "MCP-Protocol-Version": MODERN,
            "Mcp-Method": method,
        }
        if method == "tools/call":
            sent["Mcp-Name"] = params.get("name", "")
        sent.update(headers or {})
        return self.client.post("/mcp", headers=sent, json=body)

    def call(self, token, name, arguments, path="/mcp"):
        return self.legacy(token, "tools/call", {"name": name, "arguments": arguments}, path=path)

    def _write_family_note(self):
        self._write("family/Einkauf.md", "# Einkauf\n\nBrot besorgen #focus #haushalt\n")
        tagging.rebuild_index(["user-a", "user-b"])

    # ─── authentication ──────────────────────────────────────────────

    def test_missing_token_is_rejected_with_a_bearer_challenge(self):
        response = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers["WWW-Authenticate"])

    def test_a_share_token_without_the_mcp_scope_cannot_reach_the_tools(self):
        response = self.legacy(self.token(scopes=("upload",)), "tools/list")
        self.assertEqual(response.status_code, 401)

    def test_the_endpoint_never_accepts_a_session_cookie_instead_of_a_token(self):
        client = main.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = "user-a"
        response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response.status_code, 401)

    # ─── protocol ────────────────────────────────────────────────────

    def test_legacy_initialize_agrees_on_a_handshake_version(self):
        payload = self.legacy(
            self.token(), "initialize", {"protocolVersion": "2025-06-18"},
        ).get_json()
        self.assertEqual(payload["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(payload["result"]["serverInfo"]["name"], "journl")
        self.assertIn("tools", payload["result"]["capabilities"])

    def test_initialize_falls_back_to_a_supported_version_for_unknown_requests(self):
        payload = self.legacy(self.token(), "initialize", {"protocolVersion": "1999-01-01"}).get_json()
        self.assertEqual(payload["result"]["protocolVersion"], mcp_server.PREFERRED_LEGACY_VERSION)

    def test_server_discover_reports_both_eras(self):
        payload = self.modern(self.token(), "server/discover").get_json()
        self.assertEqual(payload["result"]["resultType"], "complete")
        self.assertIn(MODERN, payload["result"]["supportedVersions"])
        self.assertIn("2025-06-18", payload["result"]["supportedVersions"])

    def test_an_unsupported_version_lists_what_the_server_speaks(self):
        # The unknown version has to be declared in header *and* body, otherwise
        # the mismatch check would answer first.
        body = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
            "params": {"_meta": {mcp_server.VERSION_META_KEY: "1900-01-01"}},
        }
        response = self.client.post("/mcp", headers={
            "Authorization": "Bearer " + self.token(),
            "MCP-Protocol-Version": "1900-01-01", "Mcp-Method": "tools/list",
        }, json=body)
        self.assertEqual(response.status_code, 400)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], mcp_server.UNSUPPORTED_PROTOCOL_VERSION)
        self.assertEqual(error["data"]["requested"], "1900-01-01")
        self.assertIn(MODERN, error["data"]["supported"])

    def test_a_modern_request_must_agree_with_its_headers(self):
        response = self.modern(self.token(), "tools/list", headers={"Mcp-Method": "tools/call"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], mcp_server.HEADER_MISMATCH)

    def test_a_modern_tool_call_must_name_the_tool_in_its_header(self):
        token = self.token()
        response = self.modern(
            token, "tools/call",
            {"name": "get_blocks", "arguments": {"tags": ["focus"]}},
            headers={"Mcp-Name": "search"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], mcp_server.HEADER_MISMATCH)

    def test_a_modern_tool_call_with_matching_headers_succeeds(self):
        response = self.modern(
            self.token(), "tools/call", {"name": "get_blocks", "arguments": {"tags": ["focus"]}},
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertFalse(result["isError"])

    def test_notifications_are_acknowledged_without_a_body(self):
        response = self.client.post("/mcp", headers={"Authorization": "Bearer " + self.token()}, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_data(), b"")

    def test_unknown_methods_report_method_not_found(self):
        payload = self.legacy(self.token(), "resources/list").get_json()
        self.assertEqual(payload["error"]["code"], mcp_server.METHOD_NOT_FOUND)

    def test_the_removed_get_stream_is_not_offered(self):
        self.assertEqual(self.client.get("/mcp").status_code, 405)

    # ─── tools ───────────────────────────────────────────────────────

    def test_search_is_hidden_while_the_database_index_is_inactive(self):
        names = [tool["name"] for tool in self.legacy(self.token(), "tools/list").get_json()["result"]["tools"]]
        self.assertEqual(names, ["list_tags", "list_days", "get_blocks", "get_day"])

    def test_search_is_offered_once_the_database_index_is_active(self):
        with mock.patch.object(mcp_tools, "search_available", return_value=True):
            names = [tool["name"] for tool in self.legacy(self.token(), "tools/list").get_json()["result"]["tools"]]
        self.assertEqual(sorted(names), ["get_blocks", "get_day", "list_days", "list_tags", "search"])

    def test_calling_search_without_the_index_is_an_actionable_tool_error(self):
        result = self.call(self.token(), "search", {"query": "Eintrag"}).get_json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn("nicht aktiv", result["content"][0]["text"])

    def test_list_tags_reports_approved_and_proposed_tags_with_their_counts(self):
        self._write(
            "user-a/2026/08/09/Journal_2026-08-09.md",
            "___\n\n## C | Datum & Uhrzeit: 2026-08-09 12:00:00\nNoch offen #frisch\n\n___\n",
        )
        tagging.rebuild_index(["user-a"])

        tags = self.call(self.token(), "list_tags", {}).get_json()
        tags = {item["name"]: item for item in tags["result"]["structuredContent"]["tags"]}

        self.assertEqual(tags["focus"], {"name": "focus", "scope": "personal", "approved": True, "blocks": 2})
        self.assertEqual(tags["frisch"], {"name": "frisch", "scope": "personal", "approved": False, "blocks": 1})

    def test_list_tags_omits_a_blocked_tag(self):
        tagging.update_catalog("user-a", False, "block", "frisch")

        tags = self.call(self.token(), "list_tags", {}).get_json()
        names = [item["name"] for item in tags["result"]["structuredContent"]["tags"]]

        self.assertNotIn("frisch", names)

    def test_list_tags_never_counts_another_users_blocks(self):
        tags = self.call(self.token("user-b"), "list_tags", {}).get_json()
        tags = {item["name"]: item for item in tags["result"]["structuredContent"]["tags"]}

        self.assertEqual(tags["focus"]["blocks"], 1)

    def test_list_tags_is_always_offered(self):
        names = [tool["name"] for tool in self.legacy(self.token(), "tools/list").get_json()["result"]["tools"]]
        self.assertIn("list_tags", names)

    def test_get_blocks_returns_the_tagged_source_blocks(self):
        result = self.call(self.token(), "get_blocks", {"tags": ["focus"]}).get_json()["result"]
        blocks = result["structuredContent"]["blocks"]
        self.assertEqual(len(blocks), 2)
        self.assertEqual({block["date"] for block in blocks}, {"2026-08-03", "2026-08-05"})
        self.assertIn("Erster Eintrag", "\n".join(block["text"] for block in blocks))

    def test_get_blocks_intersects_every_requested_tag(self):
        result = self.call(self.token(), "get_blocks", {"tags": ["focus", "work"]}).get_json()["result"]
        blocks = result["structuredContent"]["blocks"]
        self.assertEqual([block["date"] for block in blocks], ["2026-08-03"])

    def test_get_blocks_can_narrow_to_a_single_day_and_a_range(self):
        token = self.token()
        single = self.call(token, "get_blocks", {
            "tags": ["focus"], "start_date": "2026-08-05", "end_date": "2026-08-05",
        }).get_json()["result"]["structuredContent"]["blocks"]
        self.assertEqual([block["date"] for block in single], ["2026-08-05"])

        span = self.call(token, "get_blocks", {
            "tags": ["focus"], "start_date": "2026-08-01", "end_date": "2026-08-04",
        }).get_json()["result"]["structuredContent"]["blocks"]
        self.assertEqual([block["date"] for block in span], ["2026-08-03"])

    def test_get_blocks_reaches_quick_note_and_clock_anchored_blocks(self):
        """The index normalises anchors, document blocks keep their own spelling."""
        self._write(
            "user-a/2026/08/06/Journal_2026-08-06.md",
            "___\n\n- 06.08. 14:59:44\nSchnellnotiz #focus\n~~06.08. 15:02:10~~\n\n___\n",
        )
        self._write(
            "user-a/2026/08/07/Journal_2026-08-07.md",
            "___\n\n- 14:59:44\nAlte Schnellnotiz #focus\n~~14:59:44~~\n\n___\n",
        )
        tagging.rebuild_index(["user-a"])

        blocks = self.call(self.token(), "get_blocks", {"tags": ["focus"]}).get_json()
        blocks = blocks["result"]["structuredContent"]["blocks"]

        texts = "\n".join(block["text"] for block in blocks)
        self.assertIn("Schnellnotiz", texts)
        self.assertIn("Alte Schnellnotiz", texts)
        self.assertEqual({block["date"] for block in blocks}, {
            "2026-08-03", "2026-08-05", "2026-08-06", "2026-08-07",
        })

    def test_get_blocks_finds_a_tag_that_is_only_a_proposal(self):
        self._write(
            "user-a/2026/08/09/Journal_2026-08-09.md",
            "___\n\n## C | Datum & Uhrzeit: 2026-08-09 12:00:00\nNoch nicht freigegeben #frisch\n\n___\n",
        )
        tagging.rebuild_index(["user-a"])
        self.assertNotIn("frisch", tagging.read_catalog("user-a")["canonical"])

        blocks = self.call(self.token(), "get_blocks", {"tags": ["frisch"]})
        blocks = blocks.get_json()["result"]["structuredContent"]["blocks"]

        self.assertEqual([block["date"] for block in blocks], ["2026-08-09"])

    def test_get_blocks_rejects_an_unusable_date_as_a_tool_error(self):
        result = self.call(self.token(), "get_blocks", {"tags": ["focus"], "start_date": "gestern"}).get_json()["result"]
        self.assertTrue(result["isError"])

    def test_get_blocks_requires_at_least_one_tag(self):
        result = self.call(self.token(), "get_blocks", {"tags": []}).get_json()["result"]
        self.assertTrue(result["isError"])

    def test_one_users_token_never_reveals_another_users_blocks(self):
        result = self.call(self.token("user-b"), "get_blocks", {"tags": ["focus"]}).get_json()["result"]
        texts = "\n".join(block["text"] for block in result["structuredContent"]["blocks"])
        self.assertIn("Geheim", texts)
        self.assertNotIn("Erster Eintrag", texts)

    def test_get_blocks_names_which_hashtags_a_model_assigned(self):
        path = self.data / "user-a/2026/08/03/Journal_2026-08-03.md"
        content = path.read_text(encoding="utf-8")
        body, footer = tagging.strip_footer(content)
        blocks = tagging.journal_blocks(body, "2026-08-03")
        path.write_text(
            body.rstrip() + "\n\n" + tagging.render_footer(
                blocks, [tagging.read_catalog("user-a")],
                {"2026-08-03 12:00:00": ["work"]}, "2026-08-03T10:00:00+00:00",
            ),
            encoding="utf-8",
        )
        tagging.rebuild_index(["user-a"])

        result = self.call(self.token(), "get_blocks", {"tags": ["work"]}).get_json()["result"]
        block = result["structuredContent"]["blocks"][0]

        self.assertEqual(sorted(block["tags"]), ["focus", "work"])
        self.assertEqual(block["ai_tags"], ["work"])

    def test_list_days_maps_each_day_to_its_hashtags(self):
        payload = self.call(self.token(), "list_days", {
            "start_date": "2026-08-01", "end_date": "2026-08-31",
        }).get_json()["result"]["structuredContent"]

        self.assertEqual(payload["days"], [
            {"date": "2026-08-03", "weekday": "Mo", "blocks": 1, "tags": {"focus": 1, "work": 1}},
            {"date": "2026-08-05", "weekday": "Mi", "blocks": 1, "tags": {"focus": 1}},
        ])
        self.assertFalse(payload["truncated"])

    def test_list_days_can_narrow_to_the_days_carrying_one_tag(self):
        payload = self.call(self.token(), "list_days", {"tags": ["work"]}).get_json()
        days = payload["result"]["structuredContent"]["days"]

        self.assertEqual([day["date"] for day in days], ["2026-08-03"])

    def test_list_days_keeps_the_newest_days_when_it_has_to_cut(self):
        payload = self.call(self.token(), "list_days", {"limit": 1}).get_json()
        payload = payload["result"]["structuredContent"]

        self.assertEqual([day["date"] for day in payload["days"]], ["2026-08-05"])
        self.assertTrue(payload["truncated"])

    def test_list_days_never_counts_another_users_day(self):
        days = self.call(self.token("user-b"), "list_days", {}).get_json()
        days = days["result"]["structuredContent"]["days"]

        self.assertEqual([day["date"] for day in days], ["2026-08-03"])
        self.assertEqual(days[0]["tags"], {"focus": 1})

    def test_get_day_returns_untagged_blocks_too(self):
        self._write(
            "user-a/2026/08/07/Journal_2026-08-07.md",
            "___\n\n## D | Datum & Uhrzeit: 2026-08-07 08:00:00\nOhne jeden Hashtag\n\n___\n",
        )

        result = self.call(self.token(), "get_day", {"start_date": "2026-08-07"}).get_json()["result"]
        blocks = result["structuredContent"]["blocks"]

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["tags"], [])
        self.assertIn("Ohne jeden Hashtag", blocks[0]["text"])

    def test_get_day_reads_a_short_span_and_refuses_a_long_one(self):
        result = self.call(self.token(), "get_day", {
            "start_date": "2026-08-03", "end_date": "2026-08-05",
        }).get_json()["result"]
        rejected = self.call(self.token(), "get_day", {
            "start_date": "2026-08-01", "end_date": "2026-08-31",
        }).get_json()["result"]

        self.assertEqual(
            [block["date"] for block in result["structuredContent"]["blocks"]],
            ["2026-08-03", "2026-08-05"],
        )
        self.assertTrue(rejected["isError"])
        self.assertIn("Tage", rejected["content"][0]["text"])

    def test_get_day_never_reads_another_users_journal(self):
        result = self.call(self.token("user-b"), "get_day", {"start_date": "2026-08-03"}).get_json()["result"]
        texts = "\n".join(block["text"] for block in result["structuredContent"]["blocks"])

        self.assertIn("Geheim", texts)
        self.assertNotIn("Erster Eintrag", texts)

    def test_the_family_server_offers_neither_day_tool(self):
        token = self.token(scopes=("mcp_family",))

        listed = self.legacy(token, "tools/list", path="/mcp/family").get_json()["result"]["tools"]
        called = self.call(token, "get_day", {"start_date": "2026-08-03"}, path="/mcp/family").get_json()

        self.assertEqual([tool["name"] for tool in listed], ["list_tags", "get_blocks"])
        self.assertEqual(called["error"]["code"], mcp_server.INVALID_PARAMS)

    def test_the_private_server_answers_under_its_explicit_path_too(self):
        names = [
            tool["name"] for tool in
            self.legacy(self.token(), "tools/list", path="/mcp/privat").get_json()["result"]["tools"]
        ]
        self.assertEqual(names, ["list_tags", "list_days", "get_blocks", "get_day"])

    def test_the_private_server_leaves_family_documents_out(self):
        self._write_family_note()

        result = self.call(self.token(), "get_blocks", {"tags": ["focus"]}).get_json()["result"]
        blocks = result["structuredContent"]["blocks"]

        self.assertEqual({block["source"] for block in blocks}, {"personal"})
        self.assertNotIn("Brot besorgen", "\n".join(block["text"] for block in blocks))

    def test_the_family_server_returns_family_documents(self):
        self._write_family_note()
        token = self.token(scopes=("mcp_family",))

        result = self.call(token, "get_blocks", {"tags": ["focus"]}, path="/mcp/family").get_json()["result"]
        blocks = result["structuredContent"]["blocks"]

        self.assertEqual({block["source"] for block in blocks}, {"family"})
        self.assertIn("Brot besorgen", "\n".join(block["text"] for block in blocks))

    def test_the_family_server_never_reaches_a_personal_journal(self):
        self._write_family_note()
        token = self.token(scopes=("mcp", "mcp_family"))

        result = self.call(token, "get_blocks", {"tags": ["focus"]}, path="/mcp/family").get_json()["result"]

        self.assertNotIn("Erster Eintrag", "\n".join(
            block["text"] for block in result["structuredContent"]["blocks"]
        ))

    def test_a_private_key_cannot_open_the_family_server(self):
        response = self.legacy(self.token(scopes=("mcp",)), "tools/list", path="/mcp/family")
        self.assertEqual(response.status_code, 401)

    def test_a_family_key_cannot_open_the_private_server(self):
        response = self.legacy(self.token(scopes=("mcp_family",)), "tools/list")
        self.assertEqual(response.status_code, 401)

    def test_the_family_server_lists_only_family_hashtags(self):
        self._write_family_note()
        token = self.token(scopes=("mcp_family",))

        tags = self.call(token, "list_tags", {}, path="/mcp/family").get_json()
        tags = {item["name"]: item for item in tags["result"]["structuredContent"]["tags"]}

        self.assertEqual(tags["haushalt"]["blocks"], 1)
        self.assertEqual(tags["focus"]["blocks"], 1)
        self.assertEqual(tags["focus"]["scope"], "family")
        self.assertNotIn("work", tags)

    def test_the_family_server_does_not_offer_full_text_search(self):
        token = self.token(scopes=("mcp_family",))
        with mock.patch.object(mcp_tools, "search_available", return_value=True):
            listed = self.legacy(token, "tools/list", path="/mcp/family").get_json()["result"]["tools"]
            called = self.call(token, "search", {"query": "Brot"}, path="/mcp/family").get_json()

        self.assertEqual([tool["name"] for tool in listed], ["list_tags", "get_blocks"])
        self.assertEqual(called["error"]["code"], mcp_server.INVALID_PARAMS)

    def test_a_client_cannot_widen_its_area_through_the_arguments(self):
        self._write_family_note()
        token = self.token(scopes=("mcp_family",))

        payload = self.call(
            token, "get_blocks", {"tags": ["focus"], "sources": ["personal"]}, path="/mcp/family",
        ).get_json()

        self.assertEqual(payload["error"]["code"], mcp_server.INVALID_PARAMS)

    def test_an_unknown_tool_is_a_protocol_error(self):
        payload = self.call(self.token(), "drop_everything", {}).get_json()
        self.assertEqual(payload["error"]["code"], mcp_server.INVALID_PARAMS)

    def test_unexpected_arguments_do_not_reach_the_tool(self):
        payload = self.call(self.token(), "get_blocks", {"tags": ["focus"], "rm": "-rf"}).get_json()
        self.assertEqual(payload["error"]["code"], mcp_server.INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
