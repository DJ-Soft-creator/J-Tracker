#!/usr/bin/env python3
"""Plan and explicitly apply a hash-guarded legacy-journal cleanup batch.

The apply path archives valid technical marker JSON against the pre-cleanup
revision, then delegates the Markdown replacement to the recoverable Document
Store.  It never guesses malformed markers and never deletes a backup.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from document_store import DocumentConflict, configured_document_store  # noqa: E402
from legacy_migration import (archive_legacy_metadata, assess_legacy_migration,
                              extract_legacy_metadata, render_without_legacy_metadata)  # noqa: E402
from shadow_index import inventory, reconcile  # noqa: E402


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def plan(root, owner_id, storage_root_key):
    """Make a stable, content-free plan from current files and DB state."""
    from sqlalchemy import create_engine, text

    root = Path(root).resolve(strict=True)
    items = inventory(root)
    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id, relative_path, current_revision_id, materialized_file_hash, storage_mode, state
                FROM documents WHERE owner_id = :owner AND scope = 'personal'
                  AND document_type = 'journal' AND storage_root_key = :root
            """), {"owner": owner_id, "root": storage_root_key}).mappings().all()
            reconciliation = reconcile(connection, storage_root_key=storage_root_key, items=items)
    finally:
        engine.dispose()
    indexed = {row["relative_path"]: row for row in rows}
    journals = []
    for item in items:
        if item.status != "eligible":
            continue
        decision = assess_legacy_migration(item.markdown_content)
        row = indexed.get(item.relative_path)
        reason = None
        if row is None:
            reason = "Dokument fehlt im Datenbankindex"
        elif row["storage_mode"] != "db_managed" or row["state"] != "active":
            reason = "Dokument ist nicht aktiv db_managed"
        elif row["materialized_file_hash"] != item.file_hash:
            reason = "Dateihash weicht vom Datenbankstand ab"
        elif decision.status not in {"ready", "ready_for_archive_cleanup"}:
            reason = "Legacy-Metadaten sind nicht sicher bereinigbar"
        journals.append({"relative_path": item.relative_path, "document_id": str(row["id"]) if row else None,
                         "source_revision_id": str(row["current_revision_id"]) if row else None,
                         "source_file_hash": item.file_hash, "status": decision.status,
                         "marker_kinds": list(decision.marker_kinds), "eligible": reason is None, "reason": reason})
    return {"owner_id": owner_id, "storage_root_key": storage_root_key, "journals": journals,
            "reconciliation": reconciliation,
            "summary": {"eligible": sum(item["eligible"] for item in journals),
                        "excluded": sum(not item["eligible"] for item in journals)}}


def _run_id(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def apply(payload, root, *, confirm, backup_confirm):
    """Archive then materialize each exact planned item; safe to repeat."""
    if confirm != payload["run_id"] or backup_confirm != payload["run_id"]:
        raise ValueError("--confirm und --backup-confirm müssen exakt der Dry-Run-ID entsprechen")
    if any(payload["reconciliation"].values()):
        raise RuntimeError("Reconciliation ist nicht leer; Apply wird nicht gestartet")
    from sqlalchemy import create_engine, text

    root = Path(root).resolve(strict=True)
    engine = create_engine(db.database_url(), pool_pre_ping=True)
    store = configured_document_store(engine)
    applied, skipped = [], []
    try:
        for entry in payload["journals"]:
            if not entry["eligible"]:
                skipped.append({"relative_path": entry["relative_path"], "reason": entry["reason"]})
                continue
            path = root / entry["relative_path"]
            content = path.read_text(encoding="utf-8", errors="strict")
            if _hash(content) != entry["source_file_hash"]:
                raise DocumentConflict("Datei hat sich seit dem Dry-Run geändert: " + entry["relative_path"])
            records = extract_legacy_metadata(content)
            clean = render_without_legacy_metadata(content)
            if clean == content:
                skipped.append({"relative_path": entry["relative_path"], "reason": "bereits bereinigt"})
                continue
            with engine.begin() as connection:
                current = connection.execute(text("""
                    SELECT current_revision_id, materialized_file_hash, storage_mode, state
                    FROM documents WHERE id = :id FOR UPDATE
                """), {"id": entry["document_id"]}).mappings().one_or_none()
                if (current is None or str(current["current_revision_id"]) != entry["source_revision_id"]
                        or current["materialized_file_hash"] != entry["source_file_hash"]
                        or current["storage_mode"] != "db_managed" or current["state"] != "active"):
                    raise RuntimeError("Datenbankbasis hat sich seit dem Dry-Run geändert: " + entry["relative_path"])
                archive_legacy_metadata(connection, source_revision_id=current["current_revision_id"],
                                        source_file_hash=entry["source_file_hash"], records=records,
                                        now=datetime.now(timezone.utc))
            revision = store.write(document_id=entry["document_id"], storage_root_key=payload["storage_root_key"],
                                   root=root, content=clean, expected_file_hash=entry["source_file_hash"],
                                   idempotency_key="legacy-cleanup:" + entry["source_revision_id"],
                                   summary="Bereinigung archivierter Legacy-Metadaten")
            applied.append({"relative_path": entry["relative_path"], "revision_id": str(revision),
                            "archived_markers": len(records)})
    finally:
        engine.dispose()
    return {"applied": applied, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description="Journl Legacy-Journal-Bereinigung")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--storage-root-key", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--backup-confirm", help="Bestätigt das separat geprüfte Backup mit der Dry-Run-ID")
    args = parser.parse_args()
    payload = plan(args.root, args.owner_id, args.storage_root_key)
    payload["run_id"] = _run_id(payload)
    payload["mode"] = "dry_run"
    if args.apply:
        payload["result"] = apply(payload, args.root, confirm=args.confirm, backup_confirm=args.backup_confirm)
        payload["mode"] = "applied"
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
