"""Unit tests for the Phase-3 read-only filesystem inventory."""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))

from shadow_index import MAX_DOCUMENT_BYTES, inventory, reconcile, report  # noqa: E402


class ShadowInventoryTests(unittest.TestCase):
    def make_journal(self, root, name="Journal_2026-08-11.md", content="# Journal\n\nText #tag\n"):
        path = Path(root) / "2026" / "08" / "11" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_inventory_reports_only_metadata_and_separates_special_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_journal(root)
            (root / "2026" / "08" / "11" / "Journal_2026-08-11.md.bak").write_text("backup", encoding="utf-8")
            items = inventory(root)
            payload = report(items)
        self.assertEqual([item.status for item in items], ["eligible", "special"])
        self.assertNotIn("markdown_content", payload["items"][0])
        self.assertNotIn("Text #tag", str(payload))

    def test_invalid_utf8_and_oversized_documents_are_blocked_without_writing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = self.make_journal(root)
            invalid.write_bytes(b"not utf8: \xff")
            oversized = self.make_journal(root, "Journal_2026-08-12.md", "x" * (MAX_DOCUMENT_BYTES + 1))
            items = {item.relative_path: item for item in inventory(root)}
        self.assertEqual(items[invalid.relative_to(root).as_posix()].status, "migration_blocked")
        self.assertEqual(items[oversized.relative_to(root).as_posix()].status, "migration_blocked")

    def test_symlink_is_reported_as_blocked_and_never_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = self.make_journal(root)
            link = root / "2026" / "08" / "11" / "Journal_2026-08-12.md"
            os.symlink(target, link)
            items = inventory(root)
        linked = next(item for item in items if item.relative_path.endswith("2026-08-12.md"))
        self.assertEqual(linked.status, "blocked")
        self.assertIn("symlink", linked.warnings)

    def test_reconciliation_detects_new_changed_missing_and_blocked_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = self.make_journal(root)
            invalid = self.make_journal(root, "Journal_2026-08-12.md")
            invalid.write_bytes(b"\xff")
            items = inventory(root)
            class Connection:
                def execute(self, *_args, **_kwargs):
                    class Rows:
                        def mappings(self): return self
                        def all(self): return [{"relative_path": existing.relative_to(root).as_posix(), "materialized_file_hash": "old", "storage_mode": "shadow_indexed", "state": "active"}, {"relative_path": "missing/Journal_2020-01-01.md", "materialized_file_hash": "old", "storage_mode": "shadow_indexed", "state": "active"}]
                    return Rows()

            with mock.patch.dict(sys.modules, {"sqlalchemy": types.SimpleNamespace(text=lambda value: value)}):
                result = reconcile(Connection(), storage_root_key="test-root", items=items)
        self.assertEqual(result["hash_changed"], [existing.relative_to(root).as_posix()])
        self.assertEqual(result["parser_blocked"], [invalid.relative_to(root).as_posix()])
        self.assertEqual(result["database_without_file"], ["missing/Journal_2020-01-01.md"])

    def test_remote_markdown_link_is_not_a_missing_local_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_journal(root, content="[Dokumentation](https://example.test/guide)\n")
            item = next(item for item in inventory(root) if item.status == "eligible")
        self.assertEqual(item.missing_media, ())


if __name__ == "__main__":
    unittest.main()
