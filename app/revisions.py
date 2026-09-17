"""Phase-4 persistent external working states and checkpoint promotion.

This module is deliberately a database-only service.  It never materializes a
Markdown file; the recoverable file-operation protocol belongs to Phase 5.
Callers own the surrounding transaction, which makes the ``FOR UPDATE`` locks
effective across observation, promotion, and pre-write checkpointing.
"""

import hashlib
import uuid
from datetime import timedelta, timezone


CHECKPOINT_INTERVAL = timedelta(minutes=15)
SNAPSHOT_DELTA_INTERVAL = 20
_NAMESPACE = uuid.UUID("6f56a868-7166-4f10-9d85-396d5629a2b3")


def _hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _utc(now):
    if now.tzinfo is None:
        raise ValueError("now muss eine Zeitzone enthalten")
    return now.astimezone(timezone.utc)


def _revision_id():
    return uuid.uuid4()


def _store_manifest(connection, *, document_id, revision_id, content, origin, now):
    """Store one lossless whole-document block and a revision manifest.

    Phase 2 guarantees that an entire document is a safe fallback block.  Later
    phases may replace this with finer parser blocks without losing the ability
    to reconstruct Phase-4 revisions.
    """
    from sqlalchemy import text

    block_id = uuid.uuid5(_NAMESPACE, str(document_id) + ":whole-document")
    content_hash = _hash(content)
    connection.execute(text("""
        INSERT INTO blocks (id, document_id, anchor_at, block_type, created_revision_id, created_at)
        VALUES (:id, :document, NULL, 'document', :revision, :now)
        ON CONFLICT (id) DO NOTHING
    """), {"id": block_id, "document": document_id, "revision": revision_id, "now": now})
    block_revision_id = connection.execute(text("""
        SELECT id FROM block_revisions WHERE block_id = :block AND content_hash = :hash
    """), {"block": block_id, "hash": content_hash}).scalar()
    if block_revision_id is None:
        block_revision_id = uuid.uuid4()
        connection.execute(text("""
            INSERT INTO block_revisions (id, block_id, document_revision_id, content_hash, markdown_content, origin, created_at)
            VALUES (:id, :block, :revision, :hash, :content, :origin, :now)
        """), {"id": block_revision_id, "block": block_id, "revision": revision_id,
              "hash": content_hash, "content": content, "origin": origin, "now": now})
    connection.execute(text("""
        INSERT INTO document_revision_blocks (document_revision_id, block_id, block_revision_id, position)
        VALUES (:revision, :block, :block_revision, 0)
    """), {"revision": revision_id, "block": block_id, "block_revision": block_revision_id})


def _should_snapshot(connection, *, document_id, now, force):
    from sqlalchemy import text

    if force:
        return True
    latest = connection.execute(text("""
        SELECT revision.created_at
        FROM document_snapshots snapshot
        JOIN document_revisions revision ON revision.id = snapshot.document_revision_id
        WHERE revision.document_id = :document
        ORDER BY revision.created_at DESC, revision.id DESC LIMIT 1
    """), {"document": document_id}).scalar()
    if latest is None or latest.astimezone(timezone.utc).date() != now.date():
        return True
    deltas = connection.execute(text("""
        SELECT count(*) FROM document_revisions revision
        WHERE revision.document_id = :document AND revision.created_at > :snapshot_at
    """), {"document": document_id, "snapshot_at": latest}).scalar_one()
    return deltas >= SNAPSHOT_DELTA_INTERVAL - 1


def _store_revision(connection, *, document_id, parent_revision_id, content, origin, reason, now,
                    summary, force_snapshot=False):
    from sqlalchemy import text

    revision_id = _revision_id()
    content_hash = _hash(content)
    connection.execute(text("""
        INSERT INTO document_revisions (id, document_id, parent_revision_id, origin, reason, file_hash,
            semantic_hash, created_at, summary)
        VALUES (:id, :document, :parent, :origin, :reason, :hash, :hash, :now, :summary)
    """), {"id": revision_id, "document": document_id, "parent": parent_revision_id,
            "origin": origin, "reason": reason, "hash": content_hash, "now": now, "summary": summary})
    _store_manifest(connection, document_id=document_id, revision_id=revision_id, content=content,
                    origin=origin, now=now)
    if _should_snapshot(connection, document_id=document_id, now=now, force=force_snapshot):
        connection.execute(text("""
            INSERT INTO document_snapshots (document_revision_id, markdown_content, file_hash, created_at)
            VALUES (:id, :content, :hash, :now)
        """), {"id": revision_id, "content": content, "hash": content_hash, "now": now})
    return revision_id, content_hash


def observe_external(connection, *, document_id, content, now):
    """Persist the latest external autosave without creating a revision yet."""
    from sqlalchemy import text

    now = _utc(now)
    observed_hash = _hash(content)
    document = connection.execute(text("""
        SELECT current_revision_id, materialized_file_hash FROM documents WHERE id = :id FOR UPDATE
    """), {"id": document_id}).mappings().one()
    if observed_hash == document["materialized_file_hash"]:
        connection.execute(text("DELETE FROM document_working_states WHERE document_id = :id"), {"id": document_id})
        return {"status": "unchanged", "checkpoint_due_at": None}
    state = connection.execute(text("""
        SELECT first_observed_at, checkpoint_due_at FROM document_working_states WHERE document_id = :id
    """), {"id": document_id}).mappings().first()
    due = state["checkpoint_due_at"] if state else now + CHECKPOINT_INTERVAL
    first = state["first_observed_at"] if state else now
    connection.execute(text("""
        INSERT INTO document_working_states (document_id, base_revision_id, observed_file_hash, observed_content,
            first_observed_at, last_observed_at, checkpoint_due_at)
        VALUES (:id, :base, :hash, :content, :first, :now, :due)
        ON CONFLICT (document_id) DO UPDATE SET observed_file_hash = EXCLUDED.observed_file_hash,
            observed_content = EXCLUDED.observed_content, last_observed_at = EXCLUDED.last_observed_at
    """), {"id": document_id, "base": document["current_revision_id"], "hash": observed_hash,
            "content": content, "first": first, "now": now, "due": due})
    return {"status": "working", "checkpoint_due_at": due}


def _sync_verified_checkpoint_media(connection, *, document_id, revision_id, content, media_root, now):
    """Persist only file-verified media for an external checkpoint of a managed journal."""
    if media_root is None:
        return 0
    from sqlalchemy import text
    storage_mode = connection.scalar(text("SELECT storage_mode FROM documents WHERE id = :id"), {"id": document_id})
    if storage_mode != "db_managed":
        return 0
    from media_references import inspect_media_references
    from media_reference_sync import insert_verified_references
    return insert_verified_references(connection, revision_id=revision_id,
                                      references=inspect_media_references(media_root, content), now=now)


def promote_checkpoint(connection, *, document_id, now, reason="interval_checkpoint", media_root=None):
    """Promote the latest working state once; safe for competing schedulers."""
    from sqlalchemy import text

    if reason not in {"interval_checkpoint", "pre_write_checkpoint"}:
        raise ValueError("ungueltiger Checkpoint-Grund")
    now = _utc(now)
    state = connection.execute(text("""
        SELECT * FROM document_working_states WHERE document_id = :id FOR UPDATE
    """), {"id": document_id}).mappings().first()
    if state is None or (reason == "interval_checkpoint" and state["checkpoint_due_at"] > now):
        return None
    revision_id, content_hash = _store_revision(
        connection, document_id=document_id, parent_revision_id=state["base_revision_id"],
        content=state["observed_content"], origin="external", reason=reason, now=now,
        summary="Externer Checkpoint" if reason == "interval_checkpoint" else "Externer Stand vor J-Tracker-Schreiben",
    )
    connection.execute(text("""
        UPDATE documents SET current_revision_id = :revision, materialized_file_hash = :hash, updated_at = :now
        WHERE id = :id
    """), {"revision": revision_id, "hash": content_hash, "now": now, "id": document_id})
    connection.execute(text("DELETE FROM document_working_states WHERE document_id = :id"), {"id": document_id})
    from db_search import index_document
    index_document(connection, document_id=document_id, revision_id=revision_id,
                   content=state["observed_content"], now=now)
    _sync_verified_checkpoint_media(connection, document_id=document_id, revision_id=revision_id,
                                    content=state["observed_content"], media_root=media_root, now=now)
    return revision_id


def pre_write_checkpoint(connection, *, document_id, now, media_root=None):
    """Durably save an observed external state before a future file operation."""
    return promote_checkpoint(connection, document_id=document_id, now=now, reason="pre_write_checkpoint",
                              media_root=media_root)


def prepare_write_revision(connection, *, document_id, content, now, summary="J-Tracker-Speichern"):
    """Create the unpublished target revision for a Phase-5 file operation.

    ``documents.current_revision_id`` deliberately remains unchanged until the
    pending file operation has atomically materialized and finalized it.
    """
    from sqlalchemy import text

    now = _utc(now)
    document = connection.execute(text("""
        SELECT current_revision_id FROM documents WHERE id = :id FOR UPDATE
    """), {"id": document_id}).mappings().one()
    return _store_revision(
        connection, document_id=document_id, parent_revision_id=document["current_revision_id"],
        content=content, origin="jtracker", reason="explicit_save", now=now, summary=summary,
    )


def promote_due_checkpoints(connection, *, now, limit=100, media_roots=None):
    """Promote due documents, skipping locks already held by another scheduler."""
    from sqlalchemy import text

    if limit < 1:
        raise ValueError("limit muss positiv sein")
    now = _utc(now)
    document_ids = connection.execute(text("""
        SELECT working.document_id, document.storage_root_key
        FROM document_working_states working JOIN documents document ON document.id = working.document_id
        WHERE working.checkpoint_due_at <= :now ORDER BY working.checkpoint_due_at, working.document_id
        FOR UPDATE OF working SKIP LOCKED LIMIT :limit
    """), {"now": now, "limit": limit}).mappings().all()
    promoted = []
    roots = media_roots or {}
    for document in document_ids:
        revision_id = promote_checkpoint(connection, document_id=document["document_id"], now=now,
                                         media_root=roots.get(document["storage_root_key"]))
        if revision_id is not None:
            promoted.append(revision_id)
    return promoted


def restore_revision(connection, *, document_id, source_revision_id, now):
    """Create a new, forced-snapshot restore revision without writing Markdown.

    The caller must use the returned revision as the target of Phase 5's
    recoverable materialization protocol.
    """
    from sqlalchemy import text

    now = _utc(now)
    pre_write_checkpoint(connection, document_id=document_id, now=now)
    source = connection.execute(text("""
        SELECT snapshot.markdown_content FROM document_snapshots snapshot
        JOIN document_revisions revision ON revision.id = snapshot.document_revision_id
        WHERE snapshot.document_revision_id = :revision AND revision.document_id = :document
    """), {"revision": source_revision_id, "document": document_id}).scalar()
    if source is None:
        source = connection.execute(text("""
            SELECT block_revision.markdown_content FROM document_revision_blocks manifest
            JOIN block_revisions block_revision ON block_revision.id = manifest.block_revision_id
            WHERE manifest.document_revision_id = :revision
            ORDER BY manifest.position
        """), {"revision": source_revision_id}).scalars().all()
        source = "".join(source) if source else None
    if source is None:
        raise LookupError("Quellrevision existiert nicht oder ist nicht rekonstruierbar")
    current = connection.execute(text("""
        SELECT current_revision_id FROM documents WHERE id = :id FOR UPDATE
    """), {"id": document_id}).scalar_one()
    revision_id, _ = _store_revision(
        connection, document_id=document_id, parent_revision_id=current, content=source,
        origin="restore", reason="explicit_save", now=now, summary="Wiederherstellung einer Revision",
        force_snapshot=True,
    )
    return revision_id
