#!/usr/bin/env python3
"""Inspect media markers for confirmed DB-managed journals without changing state."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from media_references import inspect_media_references, report_media_references  # noqa: E402


def _current_journals(owner_id):
    from sqlalchemy import create_engine, text

    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return connection.execute(text("""
                SELECT document.relative_path,
                       COALESCE(snapshot.markdown_content,
                           string_agg(block_revision.markdown_content, '' ORDER BY manifest.position)) AS markdown_content
                FROM documents document
                LEFT JOIN document_snapshots snapshot ON snapshot.document_revision_id = document.current_revision_id
                LEFT JOIN document_revision_blocks manifest ON manifest.document_revision_id = document.current_revision_id
                LEFT JOIN block_revisions block_revision ON block_revision.id = manifest.block_revision_id
                WHERE document.owner_id = :owner AND document.scope = 'personal'
                  AND document.document_type = 'journal' AND document.storage_mode = 'db_managed'
                  AND document.state = 'active'
                GROUP BY document.id, document.relative_path, snapshot.markdown_content
                ORDER BY document.relative_path
            """), {"owner": owner_id}).mappings().all()
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-10 Medieninventar (nur lesend)")
    parser.add_argument("--root", required=True, type=Path, help="Persönlicher Journl-Datenroot")
    parser.add_argument("--owner-id", required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        parser.error("--root muss ein Verzeichnis sein")
    journals, all_references = [], []
    for row in _current_journals(args.owner_id):
        if row["markdown_content"] is None:
            journals.append({"relative_path": row["relative_path"], "status": "unreconstructable"})
            continue
        report = report_media_references(inspect_media_references(root, row["markdown_content"]))
        journals.append({"relative_path": row["relative_path"], **report})
        all_references.extend(report["references"])
    statuses = {}
    for reference in all_references:
        statuses[reference["status"]] = statuses.get(reference["status"], 0) + 1
    print(json.dumps({"owner_id": args.owner_id, "mode": "read_only", "journals": journals,
                      "summary": {"references": len(all_references), "by_status": statuses}},
                     ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
