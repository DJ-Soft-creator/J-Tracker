"""Explicitly persist only file-verified media markers for current revisions."""

from datetime import datetime, timezone


def insert_verified_references(connection, *, revision_id, references, now=None):
    """Insert immutable reference rows; callers must have verified every file first."""
    from sqlalchemy import text

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    inserted = 0
    for reference in references:
        if reference.status != "verified":
            continue
        result = connection.execute(text("""
            INSERT INTO document_media_references (
                document_revision_id, media_id, media_path, media_type, mime_type, file_hash, byte_size, verified_at
            ) VALUES (:revision, :media_id, :path, :media_type, :mime_type, :file_hash, :byte_size, :verified_at)
            ON CONFLICT (document_revision_id, media_id) DO NOTHING
        """), {"revision": revision_id, "media_id": reference.media_id, "path": reference.media_path,
              "media_type": reference.media_type, "mime_type": reference.mime_type,
              "file_hash": reference.sha256, "byte_size": reference.byte_size, "verified_at": timestamp})
        inserted += result.rowcount
    return inserted
