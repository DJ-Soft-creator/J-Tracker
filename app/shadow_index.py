"""Read-only inventory and PostgreSQL shadow index for Phase 3.

The module never opens a Markdown file for writing.  It deliberately accepts an
explicit, already-authorised user root rather than discovering arbitrary paths.
"""

import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from markdown import FileValidationError, file_hash, hash_file, parse_document


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_BLOCK_BYTES = 1 * 1024 * 1024
_JOURNAL_NAME = re.compile(r"Journal_\d{4}-\d{2}-\d{2}\.md$")
_SPECIAL_SUFFIXES = (".bak", ".lock")
_TEMP_PREFIXES = (".", "~")
_DOCUMENT_NAMESPACE = uuid.UUID("e6682e52-6dd0-4aeb-9d80-a07f18cab15c")


@dataclass(frozen=True)
class InventoryItem:
    relative_path: str
    status: str
    file_hash: str | None = None
    semantic_hash: str | None = None
    size: int | None = None
    mtime_ns: int | None = None
    parser_status: str | None = None
    marker_kinds: tuple = ()
    block_count: int = 0
    duplicate_anchors: tuple = ()
    missing_media: tuple = ()
    warnings: tuple = ()
    markdown_content: str | None = None

    def report(self):
        value = asdict(self)
        value.pop("markdown_content")
        return value


def _relative(root, path):
    try:
        lexical = path.relative_to(root)
        path.resolve(strict=False).relative_to(root)
        return lexical.as_posix()
    except ValueError as error:
        raise FileValidationError("Pfad verlaesst den erlaubten Root") from error


def _is_special(relative_path):
    name = Path(relative_path).name
    return ".sync-conflict-" in name or name.endswith(_SPECIAL_SUFFIXES) or name.startswith(_TEMP_PREFIXES)


def _iter_files(root):
    """Yield regular files without following symlinks, including symlink findings."""
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            candidate = current_path / name
            if candidate.is_symlink():
                dirs.remove(name)
                yield candidate, "symlink"
        for name in names:
            candidate = current_path / name
            if candidate.is_symlink():
                yield candidate, "symlink"
            elif candidate.is_file():
                yield candidate, "file"


def _missing_media(root, source_path, links):
    missing = []
    for link in links:
        # Remote Markdown links remain portable visible content.  Only local
        # attachment targets are subject to filesystem verification.
        if "://" in link:
            continue
        if link.startswith("/"):
            missing.append(link)
            continue
        candidate = source_path.parent / link
        try:
            candidate.resolve(strict=False).relative_to(root)
        except ValueError:
            missing.append(link)
            continue
        if not candidate.is_file() or candidate.is_symlink():
            missing.append(link)
    return tuple(sorted(set(missing)))


def inventory(root, *, max_document_bytes=MAX_DOCUMENT_BYTES, max_block_bytes=MAX_BLOCK_BYTES):
    """Classify journal files and side files under one authorised root."""
    supplied_root = Path(root)
    if supplied_root.is_symlink():
        raise FileValidationError("Inventurroot darf kein Symlink sein")
    root_path = supplied_root.resolve(strict=True)
    if not root_path.is_dir():
        raise FileValidationError("Inventurroot muss ein echtes Verzeichnis sein")
    items = []
    for path, kind in _iter_files(root_path):
        if kind == "symlink":
            # Preserve the lexical location in the report; resolving it would hide it.
            relative = path.relative_to(root_path).as_posix()
            items.append(InventoryItem(relative, "blocked", warnings=("symlink",)))
            continue
        relative = _relative(root_path, path)
        if _is_special(relative):
            items.append(InventoryItem(relative, "special", warnings=("separate Spezialdatei",)))
            continue
        if not _JOURNAL_NAME.fullmatch(path.name):
            continue
        try:
            hashes = hash_file(path, max_bytes=max_document_bytes)
            content = path.read_text(encoding="utf-8", errors="strict")
            if file_hash(content.encode("utf-8")) != hashes.file_hash:
                raise FileValidationError("Datei hat sich waehrend der Inventur geaendert")
        except FileValidationError as error:
            items.append(InventoryItem(relative, "migration_blocked", warnings=(str(error),)))
            continue
        except (OSError, UnicodeDecodeError):
            items.append(InventoryItem(relative, "migration_blocked", warnings=("Datei nicht lesbar",)))
            continue
        document = parse_document(content)
        oversized_blocks = tuple(
            block.anchor for block in document.blocks if len(block.markdown.encode("utf-8")) > max_block_bytes
        )
        duplicates = tuple(warning.removeprefix("doppelte Zeitanker: ") for warning in document.warnings
                           if warning.startswith("doppelte Zeitanker: "))
        status = "migration_blocked" if document.status.value == "blocked" or oversized_blocks else "eligible"
        warnings = list(document.warnings)
        if oversized_blocks:
            warnings.append("Block ueberschreitet die erlaubte Groesse")
        stat = path.stat()
        items.append(InventoryItem(
            relative, status, hashes.file_hash, hashes.semantic_hash, hashes.size, stat.st_mtime_ns,
            document.status.value, tuple(marker.kind for marker in document.legacy_markers), len(document.blocks),
            duplicates, _missing_media(root_path, path, document.media_links), tuple(warnings),
            content,
        ))
    return tuple(sorted(items, key=lambda item: item.relative_path))


def report(items):
    """Produce aggregate-only data; document text is never included."""
    counts = {}
    marker_counts = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
        for kind in item.marker_kinds:
            marker_counts[kind] = marker_counts.get(kind, 0) + 1
    return {"summary": {"items": len(items), "by_status": counts, "marker_kinds": marker_counts},
            "items": [item.report() for item in items]}


def _document_id(storage_root_key, relative_path):
    return uuid.uuid5(_DOCUMENT_NAMESPACE, storage_root_key + ":" + relative_path)


def shadow_import(connection, *, owner_id, storage_root_key, items):
    """Import new files and retain changed files as Phase-4 working states.

    The first observed file needs an immutable base revision.  Later external
    saves must not create a revision flood, so they are delegated to the
    checkpoint service until its scheduler promotes the latest state.
    """
    from sqlalchemy import text
    from revisions import observe_external

    imported = unchanged = blocked = working = 0
    now = datetime.now(timezone.utc)
    for item in items:
        if item.status != "eligible":
            blocked += item.status == "migration_blocked"
            continue
        document_id = _document_id(storage_root_key, item.relative_path)
        row = connection.execute(text("""
            SELECT id, current_revision_id, materialized_file_hash
            FROM documents WHERE scope = 'personal' AND storage_root_key = :root AND relative_path = :path
              AND state <> 'deleted'
        """), {"root": storage_root_key, "path": item.relative_path}).mappings().first()
        if row and row["materialized_file_hash"] == item.file_hash:
            unchanged += 1
            continue
        if row:
            observe_external(connection, document_id=row["id"], content=item.markdown_content, now=now)
            working += 1
            continue
        parent_id = row["current_revision_id"] if row else None
        revision_id = uuid.uuid5(_DOCUMENT_NAMESPACE, str(document_id) + ":" + item.file_hash)
        if not row:
            connection.execute(text("""
                INSERT INTO documents (id, owner_id, scope, document_type, storage_root_key, relative_path,
                    storage_mode, state, materialized_file_hash, materialized_size, materialized_mtime_ns, created_at, updated_at)
                VALUES (:id, :owner, 'personal', 'journal', :root, :path, 'shadow_indexed', 'active',
                    :hash, :size, :mtime, :now, :now)
            """), {"id": document_id, "owner": owner_id, "root": storage_root_key, "path": item.relative_path,
                  "hash": item.file_hash, "size": item.size, "mtime": item.mtime_ns, "now": now})
        connection.execute(text("""
            INSERT INTO document_revisions (id, document_id, parent_revision_id, origin, reason, file_hash,
                semantic_hash, created_at, summary)
            VALUES (:id, :document, :parent, 'external', 'external_import', :hash, :semantic, :now, 'Phase-3 Shadow-Import')
            ON CONFLICT (id) DO NOTHING
        """), {"id": revision_id, "document": document_id, "parent": parent_id, "hash": item.file_hash,
              "semantic": item.semantic_hash, "now": now})
        connection.execute(text("""
            INSERT INTO document_snapshots (document_revision_id, markdown_content, file_hash, created_at)
            VALUES (:revision, :content, :hash, :now) ON CONFLICT (document_revision_id) DO NOTHING
        """), {"revision": revision_id, "content": item.markdown_content, "hash": item.file_hash, "now": now})
        connection.execute(text("""
            UPDATE documents SET current_revision_id = :revision, storage_mode = 'shadow_indexed', state = 'active',
                materialized_file_hash = :hash, materialized_size = :size, materialized_mtime_ns = :mtime, updated_at = :now
            WHERE id = :id
        """), {"revision": revision_id, "hash": item.file_hash, "size": item.size, "mtime": item.mtime_ns,
              "now": now, "id": document_id})
        imported += 1
    return {"imported": imported, "unchanged": unchanged, "blocked": blocked, "working": working}


def reconcile(connection, *, storage_root_key, items):
    """Compare current inventory to its shadow index without changing either side."""
    from sqlalchemy import text

    rows = connection.execute(text("""
        SELECT relative_path, materialized_file_hash, storage_mode, state
        FROM documents WHERE storage_root_key = :root AND state <> 'deleted'
    """), {"root": storage_root_key}).mappings().all()
    indexed = {row["relative_path"]: row for row in rows}
    current = {item.relative_path: item for item in items if item.status in {"eligible", "migration_blocked"}}
    result = {"file_new": [], "hash_changed": [], "parser_blocked": [], "missing_media": [],
              "path_not_allowed": [], "database_without_file": []}
    for path, item in current.items():
        if item.status == "migration_blocked":
            result["parser_blocked"].append(path)
        if item.missing_media:
            result["missing_media"].append(path)
        row = indexed.get(path)
        if item.status == "eligible" and row is None:
            result["file_new"].append(path)
        elif row and item.file_hash and row["materialized_file_hash"] != item.file_hash:
            result["hash_changed"].append(path)
    for item in items:
        if item.status == "blocked":
            result["path_not_allowed"].append(item.relative_path)
    result["database_without_file"] = sorted(set(indexed) - set(current))
    return {key: sorted(value) for key, value in result.items()}
