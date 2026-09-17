"""Brain cards for the Family planner files (shopping lists, task projects).

AI maintenance contract: everything about how a generated Family list reaches
a Brain card belongs here — one card per document, readable wording instead of
the machine line, and a sort position that a checkmark does not move.
"""

import unittest
from datetime import datetime

try:
    from .brain_api_case import BrainApiCase, FLASK_AVAILABLE
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase, FLASK_AVAILABLE

if FLASK_AVAILABLE:
    import brain


SHOPPING_LIST = (
    "---\n"
    "id: einkaufsliste\n"
    "title: Einkaufsliste\n"
    "type: einkaufsliste\n"
    "assigned_users: [user-a]\n"
    "created_at: 2026-08-09T04:30:00+02:00\n"
    "created_by: 7393dec8-e56f-4f46-97f4-d18353369161\n"
    "---\n"
    "\n"
    "## Aufgaben\n"
    "- [x] id: 2a3fc603-e197-45bf-bcbd-a86448d95a86 | title: [ ] 1 kg Möhren | "
    "user: 7393dec8-e56f-4f46-97f4-d18353369161 | target-date:  | "
    "created_at: 2026-08-09T04:38:54.189826+02:00 | "
    "created_by: 7393dec8-e56f-4f46-97f4-d18353369161 | "
    "completed_at: 2026-08-31T20:28:21.426233+02:00 | "
    "completed_by: 7393dec8-e56f-4f46-97f4-d18353369161 | sort-index: 0\n"
    "- [ ] id: 9f1d2c44-1111-4bbb-8ccc-222233334444 | title: Hafermilch | "
    "user:  | target-date:  | created_at: 2026-08-09T04:39:10+02:00 | sort-index: 1\n"
    "\n"
    "## Kommentare\n"
    "- id: c1 | user: 7393dec8-e56f-4f46-97f4-d18353369161 | text: bitte Bio | "
    "created_at: 2026-08-09T05:00:00+02:00\n"
)


class BrainFamilyListTests(BrainApiCase):
    def _list_results(self):
        response = self.client.get("/api/brain/search")
        self.assertEqual(response.status_code, 200)
        return [
            item for item in response.get_json()["results"]
            if item["doc_id"] == "family:projects/einkaufsliste.md"
        ]

    def test_family_list_is_one_readable_card_instead_of_two_metadata_blocks(self):
        self._write("family/projects/einkaufsliste.md", SHOPPING_LIST)

        results = self._list_results()

        self.assertEqual(len(results), 1)
        snippet = results[0]["snippet"]
        self.assertIn("## Einkaufsliste", snippet)
        self.assertIn("- [x] 1 kg Möhren", snippet)
        self.assertIn("- [ ] Hafermilch", snippet)
        self.assertIn("### Kommentare", snippet)
        self.assertIn("- bitte Bio", snippet)
        for machine in ("2a3fc603", "7393dec8", "completed_at", "sort-index", "title:"):
            self.assertNotIn(machine, snippet)

    def test_checking_an_item_does_not_move_the_list_up(self):
        self._write("family/projects/einkaufsliste.md", SHOPPING_LIST)
        before = self._list_results()[0]
        task = next(
            item for item in self.client.get("/api/brain/tasks").get_json()["tasks"]
            if item["doc_id"] == "family:projects/einkaufsliste.md" and item["text"] == "Hafermilch"
        )

        toggled = self.client.post("/api/brain/task/toggle", headers=self.headers, json={
            "doc_id": task["doc_id"],
            "fingerprint": task["fingerprint"],
            "completed": True,
        })
        self.assertEqual(toggled.status_code, 200)

        after = self._list_results()[0]
        self.assertEqual(after["sort_at"], before["sort_at"])
        self.assertEqual(after["fingerprint"], before["fingerprint"])
        self.assertIn("- [x] Hafermilch", after["snippet"])

    def test_sort_position_follows_the_newest_entry(self):
        self._write("family/projects/einkaufsliste.md", SHOPPING_LIST)

        sort_at = self._list_results()[0]["sort_at"]

        # Der juengste Kommentar, nicht die Dateizeit der letzten Aenderung.
        newest = datetime.fromisoformat("2026-08-09T05:00:00+02:00").timestamp()
        self.assertEqual(sort_at, newest)

    def test_target_date_stays_visible_and_ids_do_not(self):
        self._write(
            "family/projects/urlaub.md",
            "---\nid: urlaub\ntitle: Urlaub\nassigned_users: [user-a]\n---\n\n"
            "## Aufgaben\n"
            "- [ ] id: t1 | title: Pässe prüfen | user: u1 | target-date: 2026-09-12 | "
            "created_at: 2026-08-20T10:00:00+02:00 | gruppe: Reise\n",
        )

        results = self.client.get("/api/brain/search?q=Pässe").get_json()["results"]

        self.assertEqual(len(results), 1)
        self.assertIn("- [ ] Pässe prüfen (fällig 2026-09-12 · Reise)", results[0]["snippet"])
        self.assertNotIn("u1", results[0]["snippet"])

    def test_handwritten_family_note_keeps_its_own_wording(self):
        self._write(
            "family/notes/idee.md",
            "---\nassigned_users: [user-a]\n---\n\n# Idee\n\n- [ ] Gartenhaus streichen\n- Termin: Samstag\n",
        )

        results = [
            item for item in self.client.get("/api/brain/search").get_json()["results"]
            if item["doc_id"] == "family:notes/idee.md"
        ]

        snippet = "\n".join(item["snippet"] for item in results)
        self.assertIn("- [ ] Gartenhaus streichen", snippet)
        self.assertIn("- Termin: Samstag", snippet)

    @unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
    def test_planner_detection_needs_frontmatter_id_and_task_heading(self):
        self.assertTrue(brain._is_family_planner_content("family", SHOPPING_LIST))
        self.assertFalse(brain._is_family_planner_content(
            "family", "---\nassigned_users: [user-a]\n---\n\n## Aufgaben\n- [ ] Ohne id\n",
        ))
        self.assertFalse(brain._is_family_planner_content("personal", SHOPPING_LIST))


if __name__ == "__main__":
    unittest.main()
