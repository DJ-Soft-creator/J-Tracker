"""Phase-6 immutable conflict storage; conflict resolution is a later operation."""

import hashlib
import uuid
from datetime import timezone


def _hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def record_conflict(connection, *, document_id, base_revision_id, base_content, external_content,
                    jtracker_content, now):
    """Persist all conflict sides and block normal writes for the document."""
    from sqlalchemy import text

    if now.tzinfo is None:
        raise ValueError("now muss eine Zeitzone enthalten")
    now = now.astimezone(timezone.utc)
    existing = connection.scalar(text("""
        SELECT id FROM sync_conflicts
        WHERE document_id = :document AND status = 'open'
        FOR UPDATE
    """), {"document": document_id})
    if existing is not None:
        return existing
    conflict_id = uuid.uuid4()
    connection.execute(text("""
        INSERT INTO sync_conflicts (id, document_id, status, base_revision_id, created_at)
        VALUES (:id, :document, 'open', :base, :now)
    """), {"id": conflict_id, "document": document_id, "base": base_revision_id, "now": now})
    for kind, content in (("base", base_content), ("external", external_content), ("jtracker", jtracker_content)):
        connection.execute(text("""
            INSERT INTO sync_conflict_versions (id, conflict_id, kind, file_hash, markdown_content, created_at)
            VALUES (:id, :conflict, :kind, :hash, :content, :now)
        """), {"id": uuid.uuid4(), "conflict": conflict_id, "kind": kind, "hash": _hash(content),
                "content": content, "now": now})
    connection.execute(text("UPDATE documents SET state = 'conflict', updated_at = :now WHERE id = :id"),
                       {"id": document_id, "now": now})
    return conflict_id


def resolve_conflict(connection, *, conflict_id, choice, now, merged_content=None):
    """Persist one idempotent resolution candidate without materializing a file.

    Keeping the document in ``conflict`` is intentional: Phase 5 must still
    materialize this content through a prepared operation before normal writes
    may resume.
    """
    from sqlalchemy import text

    if choice not in {"external", "jtracker", "both", "manual"}:
        raise ValueError("Unbekannte Konfliktentscheidung")
    now = now.astimezone(timezone.utc)
    conflict = connection.execute(text("SELECT * FROM sync_conflicts WHERE id = :id FOR UPDATE"),
                                  {"id": conflict_id}).mappings().one()
    versions = dict(connection.execute(text("""
        SELECT kind, markdown_content FROM sync_conflict_versions WHERE conflict_id = :id
    """), {"id": conflict_id}).all())
    if conflict["status"] == "resolved":
        if conflict["resolution"] != choice:
            raise ValueError("Konflikt wurde bereits anders entschieden")
        return versions["merged"]
    if choice == "external":
        content = versions["external"]
    elif choice == "jtracker":
        content = versions["jtracker"]
    else:
        if not isinstance(merged_content, str) or not merged_content.strip():
            raise ValueError("Zusammengefuehrter sichtbarer Text ist erforderlich")
        content = merged_content
    connection.execute(text("""
        INSERT INTO sync_conflict_versions (id, conflict_id, kind, file_hash, markdown_content, created_at)
        VALUES (:id, :conflict, 'merged', :hash, :content, :now)
    """), {"id": uuid.uuid4(), "conflict": conflict_id, "hash": _hash(content),
            "content": content, "now": now})
    connection.execute(text("""
        UPDATE sync_conflicts SET status = 'resolved', resolved_at = :now, resolution = :resolution
        WHERE id = :id
    """), {"id": conflict_id, "now": now, "resolution": choice})
    return content


def materialize_resolution(store, *, conflict_id, storage_root_key, root, choice, idempotency_key,
                           now, merged_content=None):
    """Resolve a conflict through the only allowed managed write path."""
    from sqlalchemy import text

    with store.engine.begin() as connection:
        conflict = connection.execute(text("SELECT * FROM sync_conflicts WHERE id = :id FOR UPDATE"),
                                      {"id": conflict_id}).mappings().one()
        content = resolve_conflict(connection, conflict_id=conflict_id, choice=choice, now=now,
                                   merged_content=merged_content)
        document_id = conflict["document_id"]
    revision_id = store.write(document_id=document_id, storage_root_key=storage_root_key, root=root,
                              content=content, idempotency_key=idempotency_key, now=now,
                              allow_conflict=True)
    with store.engine.begin() as connection:
        connection.execute(text("UPDATE documents SET state = 'active', updated_at = :now WHERE id = :id"),
                           {"id": document_id, "now": now.astimezone(timezone.utc)})
    return revision_id
