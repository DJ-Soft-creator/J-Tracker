"""Phase-5 crash-recoverable materialization for explicitly db-managed documents.

Routes do not call this service yet.  Its public entry points require the
server-side root key and an external runtime lock root so a caller cannot turn
an arbitrary path into a managed write target.
"""

import fcntl
import hashlib
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from revisions import observe_external, pre_write_checkpoint, prepare_write_revision


class DocumentStoreError(RuntimeError):
    """Safe, content-free error for a rejected document operation."""


class DocumentConflict(DocumentStoreError):
    """The file changed after its verified base hash."""


def runtime_lock_root():
    """Return the explicitly configured lock root for a future managed writer."""
    value = os.environ.get("JOURNL_DB_LOCK_ROOT", "").strip()
    if not value:
        raise DocumentStoreError("JOURNL_DB_LOCK_ROOT fehlt")
    return Path(value)


def _hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _utc(now):
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now muss eine Zeitzone enthalten")
    return now.astimezone(timezone.utc)


def _advisory_key(document_id):
    raw = hashlib.sha256(uuid.UUID(str(document_id)).bytes).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


def _safe_target(root, relative_path):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise DocumentStoreError("Dokumentroot ist nicht zulaessig")
    resolved_root = root.resolve(strict=True)
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise DocumentStoreError("Dokumentpfad ist nicht zulaessig")
    target = resolved_root.joinpath(relative)
    try:
        target.parent.resolve(strict=True).relative_to(resolved_root)
    except (FileNotFoundError, ValueError) as error:
        raise DocumentStoreError("Dokumentpfad verlaesst den erlaubten Root") from error
    if target.is_symlink():
        raise DocumentStoreError("Dokumentziel darf kein Symlink sein")
    return target


@contextmanager
def _runtime_lock(lock_root, document_id):
    root = Path(lock_root)
    if root.is_symlink():
        raise DocumentStoreError("Runtime-Lockroot darf kein Symlink sein")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    lock_path = root / (str(document_id) + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_bytes(path):
    try:
        if path.is_symlink() or not path.is_file():
            raise DocumentStoreError("Dokumentdatei fehlt oder ist nicht zulaessig")
        return path.read_bytes()
    except OSError as error:
        raise DocumentStoreError("Dokumentdatei kann nicht gelesen werden") from error


def _fsync_directory(directory):
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace(path, content, expected_hash):
    """Write a complete temp file, verify the base again, then replace it."""
    path = Path(path)
    current = _read_bytes(path)
    if _hash_bytes(current) != expected_hash:
        raise DocumentConflict("Dokument wurde parallel geaendert")
    previous = path.stat()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix="." + path.name + ".", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(previous.st_mode))
        if hasattr(os, "chown") and os.geteuid() == 0:
            os.chown(temporary, previous.st_uid, previous.st_gid)
        # A third-party editor can still race after this check; that documented
        # filesystem limit is handled by later reconciliation, never hidden.
        if _hash_bytes(_read_bytes(path)) != expected_hash:
            raise DocumentConflict("Dokument wurde parallel geaendert")
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    observed = _read_bytes(path)
    if _hash_bytes(observed) != _hash_bytes(content):
        raise DocumentStoreError("Materialisierte Datei konnte nicht verifiziert werden")
    return observed


class DocumentStore:
    def __init__(self, engine, *, lock_root):
        self.engine = engine
        self.lock_root = Path(lock_root)

    def _load_document(self, connection, document_id, storage_root_key, *, allow_conflict=False):
        from sqlalchemy import text

        document = connection.execute(text("""
            SELECT id, storage_root_key, relative_path, storage_mode, state, current_revision_id,
                   materialized_file_hash
            FROM documents WHERE id = :id FOR UPDATE
        """), {"id": document_id}).mappings().one_or_none()
        if document is None or document["storage_root_key"] != storage_root_key:
            raise DocumentStoreError("Dokument ist nicht zulaessig")
        allowed_states = {"active", "conflict"} if allow_conflict else {"active"}
        if document["storage_mode"] != "db_managed" or document["state"] not in allowed_states:
            raise DocumentStoreError("Dokument ist nicht fuer den neuen Schreibzugang freigegeben")
        return document

    def _set_operation_status(self, operation_id, status, *, error_code=None):
        from sqlalchemy import text

        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE pending_file_operations SET status = :status, error_code = :error, updated_at = :now
                WHERE id = :id AND status IN ('prepared', 'file_written')
            """), {"id": operation_id, "status": status, "error": error_code, "now": now})

    def _finalize(self, operation_id, root):
        from sqlalchemy import text

        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            operation = connection.execute(text("""
                SELECT * FROM pending_file_operations WHERE id = :id FOR UPDATE
            """), {"id": operation_id}).mappings().one()
            if operation["status"] == "finalized":
                return operation["target_revision_id"]
            if operation["status"] != "file_written":
                raise DocumentStoreError("Dateioperation kann nicht finalisiert werden")
            connection.execute(text("""
                UPDATE documents SET current_revision_id = :revision, materialized_file_hash = :hash,
                    materialized_size = :size, materialized_mtime_ns = :mtime, updated_at = :now
                WHERE id = :document
            """), {"revision": operation["target_revision_id"], "hash": operation["target_file_hash"],
                    "size": len(operation["target_markdown_content"].encode("utf-8")), "mtime": None,
                    "now": now, "document": operation["document_id"]})
            connection.execute(text("""
                UPDATE pending_file_operations SET status = 'finalized', updated_at = :now, finalized_at = :now
                WHERE id = :id
            """), {"id": operation_id, "now": now})
            from db_search import index_document
            index_document(connection, document_id=operation["document_id"], revision_id=operation["target_revision_id"],
                           content=operation["target_markdown_content"], now=now)
            from media_references import inspect_media_references
            from media_reference_sync import insert_verified_references
            references = inspect_media_references(root, operation["target_markdown_content"])
            insert_verified_references(connection, revision_id=operation["target_revision_id"],
                                       references=references, now=now)
            return operation["target_revision_id"]

    def _materialize_or_recover(self, operation, target, root):
        expected = operation["base_file_hash"]
        target_hash = operation["target_file_hash"]
        actual_hash = _hash_bytes(_read_bytes(target))
        if actual_hash == target_hash:
            self._set_operation_status(operation["id"], "file_written")
            return self._finalize(operation["id"], root)
        if actual_hash != expected:
            self._set_operation_status(operation["id"], "conflict", error_code="file_hash_changed")
            raise DocumentConflict("Dokument wurde parallel geaendert")
        try:
            _atomic_replace(target, operation["target_markdown_content"].encode("utf-8"), expected)
        except DocumentConflict:
            self._set_operation_status(operation["id"], "conflict", error_code="file_hash_changed")
            raise
        except OSError as error:
            self._set_operation_status(operation["id"], "failed", error_code="filesystem_error")
            raise DocumentStoreError("Dateioperation ist fehlgeschlagen") from error
        self._set_operation_status(operation["id"], "file_written")
        return self._finalize(operation["id"], root)

    def write(self, *, document_id, storage_root_key, root, content, idempotency_key, now=None,
              allow_conflict=False, expected_file_hash=None, summary="J-Tracker-Speichern"):
        """Prepare, materialize, and finalize one idempotent managed write."""
        from sqlalchemy import text

        if not isinstance(content, str) or not idempotency_key:
            raise ValueError("Inhalt und Idempotency-Key sind erforderlich")
        if expected_file_hash is not None and not isinstance(expected_file_hash, str):
            raise ValueError("Erwarteter Dateihash muss ein String sein")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Schreibzusammenfassung ist erforderlich")
        now = _utc(now)
        document_uuid = uuid.UUID(str(document_id))
        with self.engine.connect() as lock_connection:
            lock_connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _advisory_key(document_uuid)})
            try:
                with _runtime_lock(self.lock_root, document_uuid):
                    with self.engine.begin() as connection:
                        document = self._load_document(connection, document_uuid, storage_root_key,
                                                       allow_conflict=allow_conflict)
                        target = _safe_target(root, document["relative_path"])
                        existing = connection.execute(text("""
                            SELECT * FROM pending_file_operations
                            WHERE document_id = :document AND idempotency_key = :key FOR UPDATE
                        """), {"document": document_uuid, "key": idempotency_key}).mappings().one_or_none()
                        if existing is None:
                            actual = _read_bytes(target)
                            actual_hash = _hash_bytes(actual)
                            if expected_file_hash is not None and actual_hash != expected_file_hash:
                                raise DocumentConflict("Dokument wurde vor dem Schreiben parallel geaendert")
                            if actual_hash != document["materialized_file_hash"]:
                                try:
                                    external_content = actual.decode("utf-8", errors="strict")
                                except UnicodeDecodeError as error:
                                    raise DocumentStoreError("Externe Dokumentdatei ist nicht gueltig UTF-8") from error
                                observe_external(connection, document_id=document_uuid,
                                                 content=external_content, now=now)
                                pre_write_checkpoint(connection, document_id=document_uuid, now=now, media_root=root)
                                document = self._load_document(connection, document_uuid, storage_root_key,
                                                               allow_conflict=allow_conflict)
                                actual = _read_bytes(target)
                                actual_hash = _hash_bytes(actual)
                                if actual_hash != document["materialized_file_hash"]:
                                    raise DocumentConflict("Dokument wurde waehrend des Checkpoints erneut geaendert")
                            revision_id, target_hash = prepare_write_revision(
                                connection, document_id=document_uuid, content=content, now=now, summary=summary)
                            operation_id = uuid.uuid4()
                            connection.execute(text("""
                                INSERT INTO pending_file_operations (id, document_id, idempotency_key, status,
                                    base_file_hash, target_file_hash, target_markdown_content, target_revision_id,
                                    created_at, updated_at)
                                VALUES (:id, :document, :key, 'prepared', :base, :target, :content, :revision, :now, :now)
                            """), {"id": operation_id, "document": document_uuid, "key": idempotency_key,
                                    "base": actual_hash, "target": target_hash, "content": content,
                                    "revision": revision_id, "now": now})
                            existing = {"id": operation_id, "base_file_hash": actual_hash,
                                        "target_file_hash": target_hash, "target_markdown_content": content,
                                        "target_revision_id": revision_id, "status": "prepared"}
                    if existing["status"] == "finalized":
                        return existing["target_revision_id"]
                    return self._materialize_or_recover(existing, target, root)
            finally:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _advisory_key(document_uuid)})

    def recover_open_operations(self, *, storage_roots, limit=100):
        """Recover prepared/file-written operations; a third hash becomes conflict."""
        from sqlalchemy import text

        if limit < 1:
            raise ValueError("limit muss positiv sein")
        with self.engine.connect() as connection:
            operations = connection.execute(text("""
                SELECT operation.*, document.storage_root_key, document.relative_path
                FROM pending_file_operations operation
                JOIN documents document ON document.id = operation.document_id
                WHERE operation.status IN ('prepared', 'file_written')
                ORDER BY operation.updated_at, operation.id LIMIT :limit
            """), {"limit": limit}).mappings().all()
        recovered = []
        for operation in operations:
            root = storage_roots.get(operation["storage_root_key"])
            if root is None:
                self._set_operation_status(operation["id"], "failed", error_code="storage_root_unavailable")
                continue
            try:
                self.write(document_id=operation["document_id"], storage_root_key=operation["storage_root_key"],
                           root=root, content=operation["target_markdown_content"],
                           idempotency_key=operation["idempotency_key"])
                recovered.append(operation["id"])
            except DocumentConflict:
                continue
        return recovered


def configured_document_store(engine):
    """Construct the service only from the server-side runtime configuration."""
    return DocumentStore(engine, lock_root=runtime_lock_root())
