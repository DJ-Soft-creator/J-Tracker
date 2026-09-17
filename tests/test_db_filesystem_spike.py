"""Phase-0C tests for the legacy atomic text-write primitive.

The tests use only temporary directories and injected OS errors.  They describe the
behaviour that a later Document Store must retain or deliberately strengthen; they do
not turn any existing path into a database-managed writer.
"""

import errno
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "app"))

import scheduling  # noqa: E402


class FilesystemSpikeTests(unittest.TestCase):
    def test_replace_is_preceded_by_fsync_and_keeps_existing_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Journal.md"
            path.write_text("vorher", encoding="utf-8")
            path.chmod(0o640)
            events = []
            real_fsync = os.fsync
            real_replace = os.replace

            def record_fsync(file_descriptor):
                events.append(("fsync", stat.S_IFMT(os.fstat(file_descriptor).st_mode)))
                return real_fsync(file_descriptor)

            def record_replace(source, destination):
                events.append(("replace", Path(source).parent, Path(destination)))
                return real_replace(source, destination)

            with mock.patch.object(scheduling.os, "fsync", side_effect=record_fsync), \
                 mock.patch.object(scheduling.os, "replace", side_effect=record_replace):
                scheduling.write_text_file(path, "nachher")

            self.assertEqual(path.read_text(encoding="utf-8"), "nachher")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(events[0][0], "fsync")
            self.assertEqual(events[0][1], stat.S_IFREG)
            self.assertEqual(events[1], ("replace", path.parent, path))
            self.assertEqual(list(path.parent.glob(".Journal.md.*")), [])

    def test_enospc_while_syncing_temp_file_keeps_old_file_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Journal.md"
            path.write_text("sicher", encoding="utf-8")

            with mock.patch.object(
                scheduling.os, "fsync", side_effect=OSError(errno.ENOSPC, "No space left")
            ):
                with self.assertRaises(OSError) as raised:
                    scheduling.write_text_file(path, "darf nicht erscheinen")

            self.assertEqual(raised.exception.errno, errno.ENOSPC)
            self.assertEqual(path.read_text(encoding="utf-8"), "sicher")
            self.assertEqual(list(path.parent.glob(".Journal.md.*")), [])

    def test_read_only_target_directory_fails_before_replacing_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Journal.md"
            path.write_text("sicher", encoding="utf-8")

            with mock.patch.object(
                scheduling.Path, "mkdir", side_effect=PermissionError(errno.EROFS, "Read-only file system")
            ):
                with self.assertRaises(PermissionError) as raised:
                    scheduling.write_text_file(path, "darf nicht erscheinen")

            self.assertEqual(raised.exception.errno, errno.EROFS)
            self.assertEqual(path.read_text(encoding="utf-8"), "sicher")

    def test_unavailable_filesystem_before_temp_creation_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Journal.md"
            path.write_text("sicher", encoding="utf-8")

            with mock.patch.object(
                scheduling.tempfile, "NamedTemporaryFile", side_effect=OSError(errno.EIO, "I/O error")
            ):
                with self.assertRaises(OSError) as raised:
                    scheduling.write_text_file(path, "darf nicht erscheinen")

            self.assertEqual(raised.exception.errno, errno.EIO)
            self.assertEqual(path.read_text(encoding="utf-8"), "sicher")

    def test_interrupt_after_replace_leaves_a_complete_target_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Journal.md"
            path.write_text("vorher", encoding="utf-8")
            real_replace = os.replace

            def replace_then_interrupt(source, destination):
                real_replace(source, destination)
                raise KeyboardInterrupt

            with mock.patch.object(scheduling.os, "replace", side_effect=replace_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    scheduling.write_text_file(path, "vollstaendig-neu")

            self.assertEqual(path.read_text(encoding="utf-8"), "vollstaendig-neu")
            self.assertEqual(list(path.parent.glob(".Journal.md.*")), [])

    def test_legacy_lock_is_created_next_to_the_document(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "Journal.md"

            scheduling.write_text_file(path, "Inhalt")

            self.assertTrue((path.parent / "Journal.md.lock").exists())

    def test_legacy_primitive_syncs_only_the_temp_file_not_the_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Journal.md"
            synced_kinds = []
            real_fsync = os.fsync

            def record_fsync(file_descriptor):
                synced_kinds.append(stat.S_IFMT(os.fstat(file_descriptor).st_mode))
                return real_fsync(file_descriptor)

            with mock.patch.object(scheduling.os, "fsync", side_effect=record_fsync):
                scheduling.write_text_file(path, "Inhalt")

            self.assertEqual(synced_kinds, [stat.S_IFREG])


if __name__ == "__main__":
    unittest.main()
