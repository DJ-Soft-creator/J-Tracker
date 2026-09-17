"""Filter, pagination and isolation guarantees of the confirmed journal search."""

import hashlib
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "app"))
from db_search import index_document, parse_date_range, search_personal  # noqa: E402


DATABASE_URL = os.environ["DATABASE_URL"]

ENTRIES = (
    ("owner-a", "2026/08/03/Journal_2026-08-03.md", "Langer Spaziergang am Fluss entlang"),
    ("owner-a", "2026/08/05/Journal_2026-08-05.md", "Spaziergang mit dem Hund im Regen"),
    ("owner-a", "2026/09/02/Journal_2026-09-02.md", "Ein Spaziergang durch die Stadt"),
    ("owner-b", "2026/08/04/Journal_2026-08-04.md", "Fremder Spaziergang im Wald"),
)


class PhaseNineSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = Config(str(ROOT_DIR / "app" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
        config.set_main_option("sqlalchemy.url", DATABASE_URL)
        command.upgrade(config, "head")

    def setUp(self):
        self.engine = create_engine(DATABASE_URL)
        self.suffix = uuid.uuid4().hex[:8]
        self.owners = {}
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            for owner, relative_path, content in ENTRIES:
                scoped_owner = "%s-%s" % (owner, self.suffix)
                self.owners[owner] = scoped_owner
                document_id = uuid.uuid4()
                revision_id = uuid.uuid4()
                connection.execute(text("""
                    INSERT INTO documents (
                        id, owner_id, scope, document_type, storage_root_key, relative_path,
                        storage_mode, state, created_at, updated_at
                    ) VALUES (
                        :id, :owner, 'personal', 'journal', :root, :path,
                        'db_managed', 'active', :now, :now
                    )
                """), {
                    "id": document_id, "owner": scoped_owner, "root": "root-" + self.suffix,
                    "path": relative_path, "now": now,
                })
                connection.execute(text("""
                    INSERT INTO document_revisions (
                        id, document_id, origin, reason, file_hash, created_at
                    ) VALUES (:id, :document, 'jtracker', 'explicit_save', :hash, :now)
                """), {
                    "id": revision_id, "document": document_id, "now": now,
                    "hash": hashlib.sha256(content.encode()).hexdigest(),
                })
                index_document(
                    connection, document_id=document_id, revision_id=revision_id,
                    content=content, now=now,
                )

    def tearDown(self):
        with self.engine.begin() as connection:
            connection.execute(text("""
                DELETE FROM document_search WHERE owner_id = ANY(:owners)
            """), {"owners": list(self.owners.values())})
            connection.execute(text("""
                DELETE FROM document_revisions WHERE document_id IN (
                    SELECT id FROM documents WHERE owner_id = ANY(:owners)
                )
            """), {"owners": list(self.owners.values())})
            connection.execute(text("""
                DELETE FROM documents WHERE owner_id = ANY(:owners)
            """), {"owners": list(self.owners.values())})
        self.engine.dispose()

    def search(self, owner="owner-a", **kwargs):
        with self.engine.connect() as connection:
            return search_personal(
                connection, owner_id=self.owners[owner], query=kwargs.pop("query", "Spaziergang"), **kwargs
            )

    def paths(self, found):
        return sorted(row["relative_path"] for row in found["results"])

    def test_a_plain_query_finds_only_the_callers_own_journals(self):
        found = self.search()
        self.assertEqual(found["total"], 3)
        self.assertNotIn("2026/08/04/Journal_2026-08-04.md", self.paths(found))

    def test_another_owner_never_appears_in_the_result(self):
        found = self.search("owner-b")
        self.assertEqual(self.paths(found), ["2026/08/04/Journal_2026-08-04.md"])

    def test_a_date_range_filters_on_the_path_prefix(self):
        path_from, path_to, error = parse_date_range("2026-08-01", "2026-08-31")
        self.assertIsNone(error)
        found = self.search(path_from=path_from, path_to=path_to)
        self.assertEqual(self.paths(found), [
            "2026/08/03/Journal_2026-08-03.md", "2026/08/05/Journal_2026-08-05.md",
        ])
        self.assertEqual(found["total"], 2)

    def test_a_single_day_range_is_inclusive_on_both_ends(self):
        path_from, path_to, _ = parse_date_range("2026-08-05", "2026-08-05")
        self.assertEqual(self.paths(self.search(path_from=path_from, path_to=path_to)), [
            "2026/08/05/Journal_2026-08-05.md",
        ])

    def test_an_explicit_path_allowlist_intersects_the_full_text_hits(self):
        found = self.search(paths=[
            "2026/08/05/Journal_2026-08-05.md", "2026/09/02/Journal_2026-09-02.md",
        ])
        self.assertEqual(found["total"], 2)
        self.assertEqual(self.paths(found), [
            "2026/08/05/Journal_2026-08-05.md", "2026/09/02/Journal_2026-09-02.md",
        ])

    def test_an_empty_allowlist_short_circuits_without_touching_the_database(self):
        # A tag filter that matched nothing must not fall back to an unfiltered search.
        self.assertEqual(search_personal(
            connection=None, owner_id="irrelevant", query="Spaziergang", paths=[],
        ), {"results": [], "total": 0})

    def test_pagination_reports_the_full_total_on_every_page(self):
        first = self.search(limit=2, offset=0, order="oldest")
        second = self.search(limit=2, offset=2, order="oldest")
        self.assertEqual((first["total"], second["total"]), (3, 3))
        self.assertEqual(len(first["results"]), 2)
        self.assertEqual(len(second["results"]), 1)
        self.assertEqual(
            self.paths(first) + self.paths(second),
            sorted(path for owner, path, _ in ENTRIES if owner == "owner-a"),
        )

    def test_ordering_by_date_is_stable_in_both_directions(self):
        newest = [row["relative_path"] for row in self.search(order="newest")["results"]]
        oldest = [row["relative_path"] for row in self.search(order="oldest")["results"]]
        self.assertEqual(newest, list(reversed(oldest)))
        self.assertEqual(newest[0], "2026/09/02/Journal_2026-09-02.md")

    def test_the_excerpt_marks_the_match_and_the_rank_is_numeric(self):
        row = self.search(limit=1)["results"][0]
        self.assertIn("<mark>", row["excerpt"])
        self.assertGreater(float(row["rank"]), 0)

    def test_an_empty_query_returns_nothing_rather_than_everything(self):
        self.assertEqual(self.search(query="   "), {"results": [], "total": 0})

    def test_out_of_range_paging_values_are_refused(self):
        for kwargs in ({"limit": 0}, {"limit": 500}, {"offset": -1}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                self.search(**kwargs)

    def test_a_broken_date_is_reported_instead_of_being_ignored(self):
        _, _, error = parse_date_range("2026-13-01", None)
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
