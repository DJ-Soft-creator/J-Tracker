import hashlib
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))

from media_references import inspect_media_references, report_media_references, verify_persisted_reference  # noqa: E402


class MediaReferenceTests(unittest.TestCase):
    def _marker(self, media_id, relative, digest):
        return '<!-- jt:media {"id":"%s","type":"image","mime_type":"image/jpeg","media_path":"%s","sha256":"%s"} -->' % (media_id, relative, digest)

    def test_verified_marker_reports_only_attachment_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "2026/08/12/media/photo.jpg"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"photo bytes")
            digest = hashlib.sha256(b"photo bytes").hexdigest()
            references = inspect_media_references(root, self._marker(uuid.uuid4(), relative, digest))
        self.assertEqual(references[0].status, "verified")
        self.assertEqual(references[0].byte_size, 11)
        self.assertNotIn("photo bytes", str(report_media_references(references)))

    def test_missing_mismatched_and_unsafe_markers_are_not_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media_id = uuid.uuid4()
            missing = inspect_media_references(root, self._marker(media_id, "2026/08/12/media/missing.jpg", "a" * 64))
            unsafe = inspect_media_references(root, self._marker(uuid.uuid4(), "../secret.jpg", "a" * 64))
        self.assertEqual(missing[0].status, "missing")
        self.assertEqual(unsafe[0].status, "invalid")

    def test_duplicate_ids_are_explicitly_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media_id = uuid.uuid4()
            marker = self._marker(media_id, "2026/08/12/media/missing.jpg", "a" * 64)
            references = inspect_media_references(root, marker + "\n" + marker)
        self.assertEqual([item.status for item in references], ["missing", "invalid"])

    def test_legacy_marker_without_final_attachment_data_is_visible_but_not_migratable(self):
        marker = '<!-- jt:media {"id":"%s","type":"image","mime_type":"image/jpeg"} -->' % uuid.uuid4()
        with tempfile.TemporaryDirectory() as directory:
            references = inspect_media_references(Path(directory), marker)
        self.assertEqual(references[0].status, "legacy_unresolved")
        self.assertIn("Legacy-Marker", references[0].reason)

    def test_persisted_reference_detects_later_file_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "2026/08/12/media/photo.jpg"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"original")
            digest = hashlib.sha256(b"original").hexdigest()
            path.write_bytes(b"changed")
            reference = verify_persisted_reference(root, media_id=uuid.uuid4(), media_path=relative,
                                                   media_type="image", mime_type="image/jpeg",
                                                   file_hash=digest, byte_size=len(b"original"))
        self.assertEqual(reference.status, "size_mismatch")


if __name__ == "__main__":
    unittest.main()
