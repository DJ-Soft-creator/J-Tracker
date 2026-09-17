"""Nail down what the Brain word search actually matches.

AI maintenance contract: these tests exist because the matching semantics were
never written down anywhere. The search normalises text with NFKD, drops the
combining marks and casefolds it, then looks for a plain substring - so
``muller`` finds ``Müller`` and ``projek`` finds ``Projekte``. That behaviour
survived the move to a precomputed haystack (``prepare_search_text``) and has
to survive whatever replaces it; a switch to PostgreSQL stemming would change
every assertion below and must be a deliberate product decision, not a
side effect.
"""

import json

try:
    from .brain_api_case import BrainApiCase, FLASK_AVAILABLE
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase, FLASK_AVAILABLE

if FLASK_AVAILABLE:
    import brain


class BrainSearchSemanticsTests(BrainApiCase):
    def setUp(self):
        super().setUp()
        self._write(
            "user-a/notes/umlaute.md",
            "# Notizen\n\nTermin bei Frau Müller wegen der Straße und der Projekte.\n",
        )
        brain.rebuild_user_index("user-a")

    def _paths(self, query):
        response = self.client.get(f"/api/brain/search?q={query}")
        self.assertEqual(response.status_code, 200)
        return [item["path"] for item in response.get_json()["results"]]

    def test_diacritics_are_ignored_in_both_directions(self):
        # Written with an umlaut, found without one - and the other way round.
        self.assertIn("notes/umlaute.md", self._paths("muller"))
        self.assertIn("notes/umlaute.md", self._paths("müller"))
        self.assertIn("notes/umlaute.md", self._paths("MÜLLER"))
        self.assertIn("notes/umlaute.md", self._paths("strasse".replace("ss", "ß")))

    def test_a_query_matches_inside_a_word_not_only_whole_words(self):
        # This is the property PostgreSQL stemming would drop: no tokenisation,
        # a plain substring over the normalised text.
        self.assertIn("notes/umlaute.md", self._paths("projek"))
        self.assertIn("notes/umlaute.md", self._paths("rojekt"))
        self.assertIn("notes/umlaute.md", self._paths("Projekte"))

    def test_the_path_is_part_of_the_haystack(self):
        self.assertIn("notes/umlaute.md", self._paths("umlaute"))

    def test_quoted_terms_keep_their_spaces_and_minus_excludes(self):
        self.assertIn("notes/umlaute.md", self._paths('"Frau Müller"'))
        self.assertEqual(self._paths('"Herr Müller"'), [])
        self.assertNotIn("notes/umlaute.md", self._paths("termin -muller"))

    def test_every_term_has_to_match_the_same_block(self):
        self.assertIn("notes/umlaute.md", self._paths("termin strasse"))
        self.assertNotIn("notes/umlaute.md", self._paths("termin zahnarzt"))

    def test_the_search_answers_hits_and_their_facets_in_one_request(self):
        # The hashtags beside the hits describe the same result list. Asking for
        # them separately meant walking the whole index twice per keystroke, in
        # a single-process server where both requests fight over the GIL.
        for query in ("", "journal", "ki:", "notiz"):
            with self.subTest(query=query):
                separate = self.client.get(f"/api/brain/tags?facet=block&q={query}").get_json()
                combined = self.client.get(f"/api/brain/search?q={query}&facets=block").get_json()
                self.assertEqual(combined["tags"], separate["tags"])
                self.assertEqual(combined["index_pending"], separate["index_pending"])

    def test_the_merged_facets_honour_the_selected_hashtags(self):
        separate = self.client.get("/api/brain/tags?facet=block&tags=work").get_json()
        combined = self.client.get("/api/brain/search?facets=block&tags=work").get_json()
        self.assertEqual(combined["tags"], separate["tags"])

    def test_the_search_answer_stays_unchanged_without_the_parameter(self):
        # The file-list views and the DB journal search still ask separately and
        # must not pay for facets they do not show.
        self.assertNotIn("tags", self.client.get("/api/brain/search?q=journal").get_json())

    def _search(self, query):
        response = self.client.get(f"/api/brain/search?q={query}")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_a_mistyped_word_falls_back_to_the_closest_spelling_in_the_notes(self):
        data = self._search("projkte")

        self.assertEqual(data["corrections"], [{"term": "projkte", "replacement": "projekte"}])
        self.assertIn("notes/umlaute.md", [item["path"] for item in data["results"]])

    def test_a_query_that_finds_something_is_never_diverted(self):
        # The repair only runs when nothing matched at all, so a working search
        # can never be silently answered with a different word.
        data = self._search("projekte")

        self.assertEqual(data["corrections"], [])
        self.assertIn("notes/umlaute.md", [item["path"] for item in data["results"]])

    def test_a_word_that_resembles_nothing_stays_without_results(self):
        data = self._search("qqqzzzxyw")

        self.assertEqual(data["results"], [])
        self.assertEqual(data["corrections"], [])

    def test_short_fragments_are_not_treated_as_typos(self):
        # Under four characters a trigram comparison is guessing, and the search
        # is a substring match anyway - "mul" already finds "Müller".
        data = self._search("mul")

        self.assertEqual(data["corrections"], [])
        self.assertIn("notes/umlaute.md", [item["path"] for item in data["results"]])

    def test_a_correction_is_only_reported_when_it_actually_helped(self):
        # If the repaired term finds nothing either, the client must not be told
        # a word was substituted - nothing was gained by it.
        data = self._search("projkte zzzqqqyyy")

        self.assertEqual(data["results"], [])
        self.assertEqual(data["corrections"], [])

    def test_the_precomputed_haystack_never_reaches_the_index_on_disk(self):
        # ``search_base`` is derived from text and hashtags that are already in
        # the file. Persisting it would double the index and let a derived value
        # drift away from what it was derived from.
        directory = brain._index_dir("personal", "user-a")
        written = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
            if path.name != "status.json"
        ]
        self.assertTrue(written)
        for document in written:
            for block in document["blocks"]:
                self.assertNotIn("search_base", block)
                self.assertNotIn("tags_base", block)
                for task in block.get("tasks", []):
                    self.assertNotIn("search_base", task)

    def test_the_prepared_haystack_agrees_with_building_it_in_place(self):
        # A block that never passed through ``prepare_search_text`` - what the
        # test fixtures with hand-built documents produce - must still match.
        documents, _ = brain._visible_documents("user-a")
        note = next(item for item in documents if item["path"] == "notes/umlaute.md")
        block = note["blocks"][0]
        self.assertIsNotNone(block.get("search_base"))

        bare = {key: value for key, value in block.items() if key != "search_base"}
        for term in ("muller", "projek", "umlaute", "zahnarzt"):
            self.assertEqual(
                brain._matches_query(block, note, [term], []),
                brain._matches_query(bare, note, [term], []),
                f"prepared and in-place matching disagree on {term!r}",
            )
