"""Journal health counters: silently ignored files, coverage, whole-day fallback."""

import json

try:
    from .brain_api_case import BrainApiCase, FLASK_AVAILABLE
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase, FLASK_AVAILABLE

if FLASK_AVAILABLE:
    import brain


class IgnoredFileTests(BrainApiCase):
    def test_journal_file_with_a_foreign_name_is_reported_as_ignored(self):
        """A file the index skips must be named, not silently dropped.

        ``_scan_personal_documents`` filters through ``_personal_kind``; a path
        that matches no known layout never reaches the index. Real journals
        named ``original_*.md`` stayed invisible that way for months.
        """
        self._write("user-a/2025/10/16/original_2025-10-16.md", "# Alt\n\nEin Eintrag.\n")
        report = brain.ignored_markdown_files("user-a")
        self.assertIn("2025/10/16/original_2025-10-16.md", report["files"])
        self.assertEqual(report["count"], 1)

    def test_files_the_index_does_read_are_not_reported(self):
        report = brain.ignored_markdown_files("user-a")
        self.assertEqual(report["files"], [])
        self.assertEqual(report["count"], 0)

    def test_technical_files_are_not_reported_as_ignored(self):
        """README and index directories are excluded on purpose, not by accident."""
        self._write("user-a/notes/README.md", "Technisch.\n")
        self.assertEqual(brain.ignored_markdown_files("user-a")["count"], 0)

    def test_reported_file_is_really_absent_from_the_index(self):
        self._write("user-a/2025/10/16/original_2025-10-16.md", "# Alt\n\nEinzigartigerbegriff.\n")
        brain.rebuild_user_index("user-a")
        results = self.client.get("/api/brain/search?q=Einzigartigerbegriff").get_json()["results"]
        self.assertEqual(results, [])
        self.assertEqual(brain.ignored_markdown_files("user-a")["count"], 1)


class CoverageTests(BrainApiCase):
    def test_counts_blocks_and_how_many_carry_hashtags(self):
        report = brain.tag_coverage("user-a")
        self.assertEqual(report["days"], 1)
        # Nur ein Block traegt einen vollstaendigen Anker. Der zweite ist mit
        # "Time:10:00:00" gestempelt und damit fuer journal_blocks() unsichtbar -
        # genau die Luecke, die diese Zahl sichtbar machen soll.
        self.assertEqual(report["blocks"], 1)
        self.assertEqual(report["tagged_blocks"], 0)
        self.assertEqual(report["ai_blocks"], 0)
        self.assertEqual(report["stamped_days"], 0)

    def test_machine_tags_are_counted_separately_from_own_tags(self):
        self._write(
            "user-a/2026/08/04/Journal_2026-08-04.md",
            "# Journal 2026-08-04\n\n___\n\n"
            "## Eintrag | Datum & Uhrzeit: 2026-08-04 09:00:00\n"
            "Text #focus\n\n___\n\n"
            '<!-- jt:hashtag-index:start schema="1" -->\n<!--\n'
            + json.dumps({
                "blocks": {"2026-08-04 09:00:00": ["focus", "work"]},
                "ai": {"2026-08-04 09:00:00": ["work"]},
                "ai_run": "2026-08-30T10:00:00+00:00",
            })
            + "\n-->\n<!-- jt:hashtag-index:end -->\n",
        )
        report = brain.tag_coverage("user-a", "2026-08-04", "2026-08-04")
        self.assertEqual(report["blocks"], 1)
        self.assertEqual(report["tagged_blocks"], 1)
        self.assertEqual(report["ai_blocks"], 1)
        self.assertEqual(report["stamped_days"], 1)

    def test_whole_day_fallback_is_surfaced_instead_of_looking_normal(self):
        """A day that only resolves as one whole-day block is a data-quality signal."""
        self._write(
            "user-a/2026/08/05/Journal_2026-08-05.md",
            "# Journal 2026-08-05\n\nEin Text ganz ohne brauchbare Uhrzeit.\n",
        )
        report = brain.tag_coverage("user-a", "2026-08-05", "2026-08-05")
        self.assertEqual(report["whole_day_days"], 1)
        self.assertEqual(report["blocks"], 1)

    def test_date_range_limits_the_counted_days(self):
        self._write("user-a/2026/08/09/Journal_2026-08-09.md", "# Journal 2026-08-09\n\nText.\n")
        self.assertEqual(brain.tag_coverage("user-a", "2026-08-09", "2026-08-09")["days"], 1)
        self.assertEqual(brain.tag_coverage("user-a", "2026-08-01", "2026-08-02")["days"], 0)


class DiagnosticsRouteTests(BrainApiCase):
    def test_report_combines_coverage_and_ignored_files(self):
        self._write("user-a/2025/10/16/original_2025-10-16.md", "# Alt\n\nEin Eintrag.\n")
        payload = self.client.get("/api/brain/diagnostics").get_json()
        self.assertEqual(payload["blocks"], 1)
        self.assertEqual(payload["ignored"]["count"], 1)
        self.assertEqual(payload["range"], {"start": "", "end": ""})

    def test_malformed_dates_are_rejected(self):
        response = self.client.get("/api/brain/diagnostics?start=03.08.2026")
        self.assertEqual(response.status_code, 400)

    def test_reversed_range_is_rejected(self):
        response = self.client.get("/api/brain/diagnostics?start=2026-08-09&end=2026-08-01")
        self.assertEqual(response.status_code, 400)

    def test_requires_a_session(self):
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/api/brain/diagnostics").status_code, 401)
