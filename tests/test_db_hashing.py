"""Phase-2 contract tests for exact-byte and semantic Markdown hashes."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))

from markdown import FileValidationError, file_hash, hash_file, semantic_hash  # noqa: E402


class MarkdownHashingTests(unittest.TestCase):
    def write_bytes(self, directory, name, content):
        path = Path(directory) / name
        path.write_bytes(content)
        return path

    def test_file_hash_is_exact_bytes_while_semantic_hash_normalizes_line_endings(self):
        lf = "Zeile eins\nZeile zwei\n"
        crlf = lf.replace("\n", "\r\n")
        self.assertNotEqual(file_hash(lf.encode()), file_hash(crlf.encode()))
        self.assertEqual(semantic_hash(lf), semantic_hash(crlf))
        self.assertEqual(file_hash(lf.encode()), hashlib.sha256(lf.encode()).hexdigest())

    def test_streamed_hashes_match_in_memory_contract_even_at_crlf_chunk_boundary(self):
        raw = "A\r\nUmlaute: ÄÖÜ äöü ß\rB\n".encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_bytes(directory, "journal.md", raw)
            result = hash_file(path, max_bytes=1024, chunk_size=2)
        self.assertEqual(result.size, len(raw))
        self.assertEqual(result.file_hash, file_hash(raw))
        self.assertEqual(result.semantic_hash, semantic_hash(raw.decode("utf-8")))

    def test_invalid_utf8_and_size_overrun_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = self.write_bytes(directory, "invalid.md", b"ok\xff")
            oversized = self.write_bytes(directory, "large.md", b"12345")
            with self.assertRaisesRegex(FileValidationError, "UTF-8"):
                hash_file(invalid, max_bytes=1024)
            with self.assertRaisesRegex(FileValidationError, "Groesse"):
                hash_file(oversized, max_bytes=4)

    def test_hash_arguments_are_explicit_and_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_bytes(directory, "journal.md", b"ok")
            with self.assertRaises(TypeError):
                file_hash("ok")
            with self.assertRaises(TypeError):
                semantic_hash(b"ok")
            with self.assertRaises(ValueError):
                hash_file(path, max_bytes=-1)


if __name__ == "__main__":
    unittest.main()
