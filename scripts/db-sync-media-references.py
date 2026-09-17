#!/usr/bin/env python3
"""Plan or explicitly persist verified media references for current DEV journals."""

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
from media_references import MediaReference, inspect_media_references  # noqa: E402
from media_reference_sync import insert_verified_references  # noqa: E402


def _journals(owner_id):
    from sqlalchemy import create_engine, text

    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return connection.execute(text("""
                SELECT document.id AS document_id, document.relative_path, document.current_revision_id,
                       COALESCE(snapshot.markdown_content,
                           string_agg(block_revision.markdown_content, '' ORDER BY manifest.position)) AS markdown_content
                FROM documents document
                LEFT JOIN document_snapshots snapshot ON snapshot.document_revision_id = document.current_revision_id
                LEFT JOIN document_revision_blocks manifest ON manifest.document_revision_id = document.current_revision_id
                LEFT JOIN block_revisions block_revision ON block_revision.id = manifest.block_revision_id
                WHERE document.owner_id = :owner AND document.scope = 'personal'
                  AND document.document_type = 'journal' AND document.storage_mode = 'db_managed'
                  AND document.state = 'active'
                GROUP BY document.id, document.relative_path, document.current_revision_id, snapshot.markdown_content
                ORDER BY document.relative_path
            """), {"owner": owner_id}).mappings().all()
    finally:
        engine.dispose()


def plan(root, owner_id):
    entries = []
    for journal in _journals(owner_id):
        references = () if journal["markdown_content"] is None else inspect_media_references(root, journal["markdown_content"])
        verified = [reference.report() for reference in references if reference.status == "verified"]
        entries.append({"document_id": str(journal["document_id"]), "relative_path": journal["relative_path"],
                        "revision_id": str(journal["current_revision_id"]), "verified": verified,
                        "excluded": sum(reference.status != "verified" for reference in references),
                        "reconstructable": journal["markdown_content"] is not None})
    return {"owner_id": owner_id, "journals": entries,
            "summary": {"verified": sum(len(item["verified"]) for item in entries),
                        "excluded": sum(item["excluded"] for item in entries)}}


def apply(payload, confirm):
    if confirm != payload["run_id"]:
        raise ValueError("--confirm muss exakt der angezeigten run_id entsprechen")
    if not any(journal["verified"] for journal in payload["journals"]):
        return 0
    from sqlalchemy import create_engine, text

    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            inserted = 0
            for journal in payload["journals"]:
                current = connection.scalar(text("""
                    SELECT current_revision_id FROM documents
                    WHERE id = :document AND owner_id = :owner AND storage_mode = 'db_managed' AND state = 'active'
                """), {"document": journal["document_id"], "owner": payload["owner_id"]})
                if str(current) != journal["revision_id"]:
                    raise RuntimeError("Aktuelle Revision hat sich geändert: " + journal["relative_path"])
                references = tuple(MediaReference(**{
                    "media_id": item["media_id"], "media_path": item["media_path"], "media_type": item["media_type"],
                    "mime_type": item["mime_type"], "sha256": item["sha256"], "byte_size": item["byte_size"],
                    "status": "verified", "reason": None,
                }) for item in journal["verified"])
                inserted += insert_verified_references(connection, revision_id=journal["revision_id"], references=references,
                                                      now=datetime.now(timezone.utc))
    finally:
        engine.dispose()
    return inserted


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-10B Medienreferenzen synchronisieren")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        parser.error("--root muss ein Verzeichnis sein")
    payload = plan(root, args.owner_id)
    fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload.update(run_id=hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24], mode="dry_run")
    if args.apply:
        payload.update(mode="applied", inserted=apply(payload, args.confirm))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
