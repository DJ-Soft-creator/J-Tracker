#!/usr/bin/env python3
"""Read-only integrity reconciliation for confirmed journal media references."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from media_references import inspect_media_references, verify_persisted_reference  # noqa: E402


def reconcile(root, owner_id):
    from sqlalchemy import create_engine, text

    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT reference.document_revision_id, reference.media_id, reference.media_path,
                       reference.media_type, reference.mime_type, reference.file_hash, reference.byte_size,
                       document.relative_path
                FROM document_media_references reference
                JOIN document_revisions revision ON revision.id = reference.document_revision_id
                JOIN documents document ON document.id = revision.document_id
                WHERE document.owner_id = :owner AND document.scope = 'personal'
                ORDER BY document.relative_path, reference.document_revision_id, reference.media_id
            """), {"owner": owner_id}).mappings().all()
            current = connection.execute(text("""
                SELECT document.current_revision_id, document.relative_path,
                       COALESCE(snapshot.markdown_content,
                           string_agg(block_revision.markdown_content, '' ORDER BY manifest.position)) AS markdown_content
                FROM documents document
                LEFT JOIN document_snapshots snapshot ON snapshot.document_revision_id = document.current_revision_id
                LEFT JOIN document_revision_blocks manifest ON manifest.document_revision_id = document.current_revision_id
                LEFT JOIN block_revisions block_revision ON block_revision.id = manifest.block_revision_id
                WHERE document.owner_id = :owner AND document.scope = 'personal'
                  AND document.document_type = 'journal' AND document.storage_mode = 'db_managed'
                  AND document.state = 'active'
                GROUP BY document.id, document.current_revision_id, document.relative_path, snapshot.markdown_content
            """), {"owner": owner_id}).mappings().all()
    finally:
        engine.dispose()
    persisted = {(str(row["document_revision_id"]), str(row["media_id"])) for row in rows}
    checks = []
    for row in rows:
        check = verify_persisted_reference(root, **{key: row[key] for key in (
            "media_id", "media_path", "media_type", "mime_type", "file_hash", "byte_size")})
        checks.append({"relative_path": row["relative_path"], "revision_id": str(row["document_revision_id"]), **check.report()})
    unindexed = []
    for journal in current:
        if journal["markdown_content"] is None:
            unindexed.append({"relative_path": journal["relative_path"], "status": "unreconstructable"})
            continue
        for reference in inspect_media_references(root, journal["markdown_content"]):
            if reference.status == "verified" and (str(journal["current_revision_id"]), reference.media_id) not in persisted:
                unindexed.append({"relative_path": journal["relative_path"],
                                  "revision_id": str(journal["current_revision_id"]), **reference.report()})
    counts = {}
    for check in checks:
        counts[check["status"]] = counts.get(check["status"], 0) + 1
    return {"mode": "read_only", "owner_id": owner_id,
            "summary": {"stored_references": len(checks), "by_status": counts,
                        "unindexed_current_references": len(unindexed)},
            "references": checks, "unindexed_current_references": unindexed}


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-10C Medienreferenzen prüfen (nur lesend)")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--owner-id", required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        parser.error("--root muss ein Verzeichnis sein")
    print(json.dumps(reconcile(root, args.owner_id), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
