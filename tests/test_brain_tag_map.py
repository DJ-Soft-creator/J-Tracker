"""Hashtag-Map: Haeufigkeit, Verbindungen, Verlauf - und dieselbe Sichtbarkeit wie die Suche."""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import brain_tag_map  # noqa: E402

try:
    from .brain_api_case import BrainApiCase
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase


def documents():
    return [
        {"date": "2026-08-03", "blocks": [{"tags": ["work", "focus"]}, {"tags": ["work"]}]},
        {"date": "2026-09-10", "blocks": [{"tags": ["work", "focus", "family"]}]},
        {"date": "", "blocks": [{"tags": ["note"]}]},
    ]


class BuildTagMapTests(unittest.TestCase):
    def build(self, **options):
        return brain_tag_map.build_tag_map(documents(), lambda doc, block: set(block["tags"]), **options)

    def test_counts_months_and_dates(self):
        result = self.build()
        tags = {item["name"]: item for item in result["tags"]}
        self.assertEqual(tags["work"]["count"], 3)
        self.assertEqual(tags["work"]["months"], {"2026-08": 2, "2026-09": 1})
        self.assertEqual((tags["work"]["first"], tags["work"]["last"]), ("2026-08-03", "2026-09-10"))
        self.assertEqual(tags["note"]["first"], "")  # undatiert, aber gezaehlt
        self.assertEqual(result["months"], ["2026-08", "2026-09"])
        self.assertEqual(result["blocks"], 4)

    def test_edges_count_shared_blocks_with_a_normalised_strength(self):
        edges = {(edge["a"], edge["b"]): edge for edge in self.build()["edges"]}
        self.assertEqual(edges[("focus", "work")]["weight"], 2)
        self.assertAlmostEqual(edges[("focus", "work")]["strength"], 2 / (2 * 3) ** 0.5, places=3)
        self.assertEqual(edges[("family", "focus")]["weight"], 1)

    def test_a_date_range_skips_undated_and_outside_content(self):
        result = self.build(start=date(2026, 9, 1), end=date(2026, 9, 30))
        self.assertEqual(sorted(item["name"] for item in result["tags"]), ["family", "focus", "work"])

    def test_daily_lists_every_day_of_a_short_range(self):
        result = self.build(start=date(2026, 9, 9), end=date(2026, 9, 11), daily=True)
        self.assertEqual(result["granularity"], "day")
        self.assertEqual(result["days"], ["2026-09-09", "2026-09-10", "2026-09-11"])
        tags = {item["name"]: item for item in result["tags"]}
        self.assertEqual(tags["work"]["days"], {"2026-09-10": 1})
        # Ohne Start gibt es keine Tagesspalten - "alles" waeren zu viele.
        self.assertEqual(self.build(daily=True)["granularity"], "month")
        self.assertNotIn("days", self.build()["tags"][0])

    def test_match_brings_in_tags_beyond_the_limit_with_their_edges(self):
        result = self.build(max_tags=1, match="#FAM")
        self.assertEqual([item["name"] for item in result["tags"]], ["work", "family"])
        self.assertEqual([(edge["a"], edge["b"]) for edge in result["edges"]], [("family", "work")])

    def test_include_brings_in_named_tags_and_names_lists_all(self):
        result = self.build(max_tags=1, include={"family", "unknown"}, approved={"work"})
        self.assertEqual([item["name"] for item in result["tags"]], ["work", "family"])
        self.assertEqual(result["names"][0], {"name": "work", "count": 3, "approved": True})
        self.assertEqual({item["name"] for item in result["names"]}, {"work", "focus", "family", "note"})

    def test_suggestions_name_a_reason_and_skip_approved_and_hidden(self):
        result = self.build(approved={"work"})
        tags = {item["name"]: item for item in result["tags"]}
        self.assertTrue(tags["work"]["approved"])
        self.assertFalse(tags["focus"]["approved"])
        suggestions = {item["name"]: item for item in result["suggestions"]}
        self.assertEqual(suggestions["focus"]["reasons"], [{"kind": "partner", "tag": "work", "weight": 2}])
        self.assertNotIn("work", suggestions)    # schon freigegeben
        self.assertNotIn("family", suggestions)  # nur einmal gemeinsam: kein Grund
        self.assertNotIn("focus", {item["name"] for item in self.build(approved={"work"}, skip={"focus"})["suggestions"]})
        self.assertNotIn("suggestions", self.build())

    def test_growing_and_isolated_suggestions(self):
        docs = [{"date": "2026-01-01", "blocks": [{"tags": ["alt"]}]}]
        docs += [{"date": f"2026-01-{day:02d}", "blocks": [{"tags": ["neu"]}]} for day in (25, 28, 30)]
        docs += [{"date": f"2026-01-{day:02d}", "blocks": [{"tags": ["solo"]}]} for day in (2, 3, 28, 30)]
        result = brain_tag_map.build_tag_map(docs, lambda doc, block: set(block["tags"]), approved=set())
        reasons = {item["name"]: [reason["kind"] for reason in item["reasons"]] for item in result["suggestions"]}
        self.assertIn("growing", reasons["neu"])
        self.assertNotIn("growing", reasons["solo"])  # gleich oft frueh wie spaet
        self.assertIn("isolated", reasons["solo"])
        self.assertNotIn("alt", reasons)

    def test_brain_categories_ignore_hubs_as_bridges(self):
        blocks = [{"tags": ["a1", "a2", "a3", "info"]}, {"tags": ["b1", "b2", "b3", "info"]},
                  {"tags": ["docker", "docker-compose"]}, {"tags": ["einzeln"]}]
        docs = [{"date": f"2026-01-0{day}", "blocks": blocks} for day in range(1, 6)]
        result = brain_tag_map.build_tag_map(docs, lambda doc, block: set(block["tags"]))
        category = {item["name"]: item["category"] for item in result["tags"]}
        # #info steht neben a und b, verschmilzt sie aber nicht zu einem Feld.
        self.assertEqual(len({category["a1"], category["a2"], category["a3"]}), 1)
        self.assertEqual(len({category["b1"], category["b2"], category["b3"]}), 1)
        self.assertNotEqual(category["a1"], category["b1"])
        self.assertIn(category["info"], {category["a1"], category["b1"]})
        self.assertEqual(result["hubs"], ["info"])
        labels = [item["label"] for item in result["categories"]]
        self.assertEqual(labels[category["a1"]], "a1")
        self.assertEqual(result["categories"][-1], {"label": "Sonstige", "size": 1, "count": 5, "other": True})
        self.assertEqual(category["einzeln"], len(result["categories"]) - 1)
        # Gleiche Daten, gleiches Bild.
        again = brain_tag_map.build_tag_map(docs, lambda doc, block: set(block["tags"]))
        self.assertEqual(again["categories"], result["categories"])

    def test_abstraction_puts_hubs_inside_and_subterms_outside(self):
        blocks = [{"tags": ["a1", "a2", "a3", "info"]}, {"tags": ["b1", "b2", "b3", "info"]},
                  {"tags": ["docker", "docker-compose"]}]
        docs = [{"date": "2026-01-01", "blocks": blocks}]
        level = {item["name"]: item["abstraction"]
                 for item in brain_tag_map.build_tag_map(docs, lambda doc, block: set(block["tags"]))["tags"]}
        self.assertEqual(level["info"], 1.0)
        self.assertGreater(level["docker"], level["docker-compose"])
        self.assertEqual(min(level.values()), 0.0)

    def test_edges_grow_with_the_tag_count(self):
        self.assertEqual(brain_tag_map.edges_for(80), 480)
        self.assertEqual(brain_tag_map.edges_for(10), brain_tag_map.DEFAULT_MAX_EDGES)
        self.assertEqual(brain_tag_map.edges_for(500), brain_tag_map.MAX_EDGES_LIMIT)

    def test_short_ranges_switch_to_days(self):
        today = date(2026, 10, 2)
        self.assertTrue(brain_tag_map.is_daily(date(2026, 9, 26), None, today))
        self.assertFalse(brain_tag_map.is_daily(date(2026, 7, 2), None, today))
        self.assertFalse(brain_tag_map.is_daily(None, today, today))

    def test_the_tag_limit_keeps_the_most_frequent_and_their_edges_only(self):
        result = self.build(max_tags=2)
        self.assertEqual([item["name"] for item in result["tags"]], ["work", "focus"])
        self.assertEqual([(edge["a"], edge["b"]) for edge in result["edges"]], [("focus", "work")])


class TagMapRouteTests(BrainApiCase):
    def test_route_uses_the_visible_tags_only(self):
        response = self.client.get("/api/brain/tag-map")
        self.assertEqual(response.status_code, 200)
        names = {item["name"] for item in response.get_json()["tags"]}
        self.assertIn("work", names)
        self.assertIn("focus", names)
        self.assertNotIn("secret", names)  # fremdes Familiendokument

    def test_family_switch_leaves_family_documents_out(self):
        self._write("family/hashtag_catalog.json",
                    '{"version": 1, "canonical": ["haushalt"], "aliases": {}, "proposals": []}')
        self._write(
            "family/projects/shared.md",
            "---\nassigned_users: [user-a]\n---\n\n# Shared\n\nEinkauf planen #haushalt\n",
        )
        names = lambda query: {item["name"] for item in self.client.get(f"/api/brain/tag-map{query}").get_json()["tags"]}
        self.assertIn("haushalt", names(""))
        self.assertNotIn("haushalt", names("?family=0"))
        self.assertIn("work", names("?family=0"))

    def test_route_marks_approval_and_hides_dismissed_suggestions(self):
        self._write(
            "user-a/2026/08/04/Journal_2026-08-04.md",
            "# Journal 2026-08-04\n\n___\n\n## Brainablage: Tags: Work, Unapproved **Datum & Uhrzeit: 2026-08-04 09:00:00**\nA\n\n___\n\n"
            "## Brainablage: Tags: Work, Unapproved **Datum & Uhrzeit: 2026-08-04 10:00:00**\nB\n\n___\n",
        )
        data = self.client.get("/api/brain/tag-map").get_json()
        tags = {item["name"]: item for item in data["tags"]}
        self.assertTrue(tags["work"]["approved"])
        self.assertFalse(tags["unapproved"]["approved"])
        suggestion = next(item for item in data["suggestions"] if item["name"] == "unapproved")
        self.assertEqual(suggestion["scope"], "personal")
        response = self.client.post("/api/brain/tag-catalog", headers=self.headers,
                                    json={"scope": "personal", "action": "map_hide", "tag": "unapproved"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("unapproved", response.get_json()["catalog"]["map_hidden"])
        self.assertIn("unapproved", response.get_json()["catalog"]["proposals"])  # bleibt nutzbar
        names = {item["name"] for item in self.client.get("/api/brain/tag-map").get_json()["suggestions"]}
        self.assertNotIn("unapproved", names)

    def test_excluded_tags_leave_the_map_with_their_edges(self):
        response = self.client.post("/api/brain/tag-catalog", headers=self.headers,
                                    json={"scope": "personal", "action": "map_exclude", "tag": "focus"})
        self.assertEqual(response.status_code, 200)
        data = self.client.get("/api/brain/tag-map").get_json()
        self.assertNotIn("focus", {item["name"] for item in data["tags"]})
        self.assertFalse(any("focus" in (edge["a"], edge["b"]) for edge in data["edges"]))
        self.assertEqual(data["excluded"], ["focus"])
        self.client.post("/api/brain/tag-catalog", headers=self.headers,
                         json={"scope": "personal", "action": "map_include", "tag": "focus"})
        self.assertIn("focus", {item["name"] for item in self.client.get("/api/brain/tag-map").get_json()["tags"]})

    def test_ai_exclusion_list_is_kept_in_the_personal_catalog(self):
        response = self.client.post("/api/brain/tag-catalog", headers=self.headers,
                                    json={"scope": "personal", "action": "ai_exclude", "tag": "#Info"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["catalog"]["ai_excluded"], ["info"])
        catalog = self.client.get("/api/brain/tag-catalog").get_json()["catalog"]
        self.assertEqual(catalog["personal"]["ai_excluded"], ["info"])
        self.client.post("/api/brain/tag-catalog", headers=self.headers,
                         json={"scope": "personal", "action": "ai_include", "tag": "info"})
        catalog = self.client.get("/api/brain/tag-catalog").get_json()["catalog"]
        self.assertEqual(catalog["personal"]["ai_excluded"], [])

    def test_catalog_offers_the_ai_exclusion_section(self):
        source = (ROOT / "app" / "static" / "brain.js").read_text(encoding="utf-8")
        self.assertIn("{ id: 'ai_excluded', label: 'KI-Ausschluss'", source)
        self.assertIn("case 'ai_excluded':", source)
        self.assertIn("action: 'ai_exclude'", source)
        self.assertIn("action: 'ai_include'", source)

    def test_layout_is_saved_per_view_clamped_and_resettable(self):
        self.assertEqual(self.client.get("/api/brain/tag-map/layout").get_json(), {"layouts": {}})
        response = self.client.put("/api/brain/tag-map/layout", headers=self.headers,
                                   json={"view": "cluster", "positions": {"Work": [0.25, 2.7], "focus": [-0.5, 0.5]}})
        self.assertEqual(response.status_code, 200)
        self.client.put("/api/brain/tag-map/layout", headers=self.headers,
                        json={"view": "overview", "positions": {"work": [0.1, 0.1]}})
        layouts = self.client.get("/api/brain/tag-map/layout").get_json()["layouts"]
        self.assertEqual(layouts["cluster"], {"work": [0.25, 2.0], "focus": [-0.5, 0.5]})
        self.assertIn("overview", layouts)
        self.client.delete("/api/brain/tag-map/layout?view=cluster", headers=self.headers)
        self.assertEqual(set(self.client.get("/api/brain/tag-map/layout").get_json()["layouts"]), {"overview"})

    def test_layout_rejects_bad_input_and_needs_csrf(self):
        bad = self.client.put("/api/brain/tag-map/layout", headers=self.headers,
                              json={"view": "orbit", "positions": {}})
        self.assertEqual(bad.status_code, 400)
        broken = self.client.put("/api/brain/tag-map/layout", headers=self.headers,
                                 json={"view": "cluster", "positions": {"work": ["x", 1]}})
        self.assertEqual(broken.status_code, 400)
        self.assertNotEqual(self.client.put("/api/brain/tag-map/layout",
                                            json={"view": "cluster", "positions": {}}).status_code, 200)

    def test_route_rejects_a_broken_range(self):
        response = self.client.get("/api/brain/tag-map?start_date=2026-09-10&end_date=2026-01-01")
        self.assertEqual(response.status_code, 400)



class TagMapFrontendTests(unittest.TestCase):
    def test_menu_entry_script_and_safe_text(self):
        for name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertIn('id="brain-menu-map"', template, name)
            self.assertLess(template.index("filename='brain-chat.js'"),
                            template.index("filename='brain-tag-map.js'"), name)
            # Die Sichten melden sich beim Kern an - sie muessen danach laden.
            self.assertLess(template.index("filename='brain-tag-map.js'"),
                            template.index("filename='brain-tag-map-views.js'"), name)
        source = (ROOT / "app" / "static" / "brain-tag-map.js").read_text(encoding="utf-8")
        views = (ROOT / "app" / "static" / "brain-tag-map-views.js").read_text(encoding="utf-8")
        for view in ("drawOverview", "drawBrain", "drawOrbit", "drawCluster", "drawTime"):
            self.assertIn(f"function {view}()", views)
        self.assertIn("window.journlBrainTagMapKit = kit", source)
        suggest = (ROOT / "app" / "static" / "brain-tag-map-list.js").read_text(encoding="utf-8")
        for name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertLess(template.index("filename='brain-tag-map.js'"),
                            template.index("filename='brain-tag-map-list.js'"), name)
        # Freigabe und Ausblenden laufen ueber die bestehende Katalog-Route.
        self.assertIn("action: 'approve'", source)
        self.assertIn("action: 'map_hide'", source)
        self.assertIn("params.set('match', state.filter)", source)
        # Hashtag-Liste: Freigegebene und Vorschlaege, Ankreuzen = Filter-Chip.
        self.assertIn("button(`#${item.name}`", suggest)
        self.assertIn("kit.togglePick(item.name)", suggest)
        self.assertIn("data-list-tab=\"approved\"", suggest)
        self.assertIn("params.set('include'", source)
        # Mehrere angekreuzte Vorschlaege auf einmal freigeben bzw. ausblenden.
        self.assertIn("kit.approveMany(allowed)", suggest)
        self.assertIn("async function approveMany(items)", source)
        self.assertNotIn("innerHTML = `#", suggest)
        brain = (ROOT / "app" / "static" / "brain.js").read_text(encoding="utf-8")
        self.assertIn("action: 'map_unhide'", brain)
        self.assertIn("/api/brain/tag-map", source)
        # Hashtag-Namen nur als Text, nie als HTML.
        self.assertIn("text.textContent = `#${tag.name}`", source)
        self.assertNotIn("innerHTML = `#", source)

    def test_map_links_into_the_block_filtered_search(self):
        source = (ROOT / "app" / "static" / "brain-tag-map.js").read_text(encoding="utf-8")
        for preset in ("'2d'", "'3d'", "'7d'", "'custom'"):
            self.assertIn(preset, source)
        self.assertIn("params.set('family', '0')", source)
        self.assertIn("view: 'overview'", source)  # Start ohne Hashtag in der Mitte
        self.assertIn("addEventListener('wheel'", source)
        self.assertIn("event.key === 'ArrowLeft'", source)
        # Knoten verschieben (Uebersicht/Cluster), Orbit anhalten/zuruecksetzen/drehen.
        self.assertIn("function draggable(group, point, onMove, onEnd)", source)
        self.assertIn('data-map-orbit="pause"', source)
        self.assertIn("data-map-reset", source)
        # Zuruecksetzen fuehrt zur Standardansicht ohne Filterung.
        self.assertIn("function resetAll()", source)
        self.assertIn("state.range = 'all';", source)
        self.assertIn("state.view = 'overview';", source)
        self.assertIn("data-map-search-clear", source)
        self.assertIn("spinBy(delta)", source)
        # Hirn: Ringe umschaltbar, Themenfelder vom Server statt im Browser.
        self.assertIn('data-map-view="brain"', source)
        self.assertIn("data-map-rings", source)
        self.assertNotIn("function assignClusters", source)
        self.assertIn("openEntries([a, b])", source)  # Kante: beide Hashtags
        self.assertIn("brainShowTagEntries(tags, range.start, range.end)", source)
        filters = (ROOT / "app" / "static" / "brain-tag-filters.js").read_text(encoding="utf-8")
        # Nur "Timeline & Suche" filtert je Block wie die Kanten der Map.
        self.assertIn("setBrainMode('search')", filters)
        self.assertIn("window.brainShowTagEntries = brainShowTagEntries", filters)

    def test_node_menu_drill_exclude_and_layout_wiring(self):
        source = (ROOT / "app" / "static" / "brain-tag-map.js").read_text(encoding="utf-8")
        actions = (ROOT / "app" / "static" / "brain-tag-map-actions.js").read_text(encoding="utf-8")
        views = (ROOT / "app" / "static" / "brain-tag-map-views.js").read_text(encoding="utf-8")
        # Doppelklick/Langdruecken oeffnen das Menue statt direkt zu springen.
        self.assertIn("kit.openMenu(tag, x, y)", source)
        for label in ("Fokussieren (reindrillen)", "Einträge in der Timeline", "Zum Filter der Map", "In der Map ausblenden"):
            self.assertIn(label, actions)
        self.assertIn("action: 'map_exclude'", actions)
        # Klick auf eine Linie: kleines Menue statt Direktsprung.
        self.assertIn("kit.openEdgeMenu(a, b, weight, event.clientX, event.clientY)", source)
        self.assertIn("Einträge mit beiden in der Timeline", actions)
        self.assertIn("/api/brain/tag-map/layout", actions)
        self.assertIn("state.drill[state.drill.length - 1]", views)
        self.assertIn("applySaved(nodes, 'cluster', width, height)", views)
        self.assertNotIn("innerHTML", actions)  # Namen nur als Text
        for name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertLess(template.index("filename='brain-tag-map.js'"),
                            template.index("filename='brain-tag-map-actions.js'"), name)


class CardDoubleTapTests(unittest.TestCase):
    def test_double_tap_opens_editor_in_today_and_expands_first_in_brain(self):
        source = (ROOT / "app" / "static" / "card-double-tap.js").read_text(encoding="utf-8")
        self.assertIn("window.openDashboardWriteEditor(today.id.slice('entry-'.length))", source)
        # Brain: erst ausklappen, dann Editor - und nur mit Bearbeiten-Knopf.
        self.assertLess(source.index("toggleBrainCardExpansion(expand)"), source.index("open(card.dataset.docId"))
        self.assertIn("card.querySelector('[data-brain-edit]')", source)
        self.assertIn("touch-action: manipulation", source)
        for name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertLess(template.index("filename='brain-cards.js'"), template.index("filename='card-double-tap.js'"), name)
            self.assertLess(template.index("filename='write-source-edit.js'"), template.index("filename='card-double-tap.js'"), name)


if __name__ == "__main__":
    unittest.main()
