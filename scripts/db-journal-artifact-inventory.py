#!/usr/bin/env python3
"""Read-only Phase-11 inventory of current managed-journal side artifacts."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from journal_artifacts import inspect_journal_artifacts, inspect_media_sidecars, report_journal_artifacts  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-11 Nebenartefakt-Inventar (nur lesend)")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--owner-id", required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        parser.error("--root muss ein Verzeichnis sein")
    from sqlalchemy import create_engine, text
    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT document.relative_path, COALESCE(snapshot.markdown_content,
                    string_agg(block_revision.markdown_content, '' ORDER BY manifest.position)) AS markdown_content
                FROM documents document
                LEFT JOIN document_snapshots snapshot ON snapshot.document_revision_id = document.current_revision_id
                LEFT JOIN document_revision_blocks manifest ON manifest.document_revision_id = document.current_revision_id
                LEFT JOIN block_revisions block_revision ON block_revision.id = manifest.block_revision_id
                WHERE document.owner_id = :owner AND document.scope = 'personal' AND document.document_type = 'journal'
                  AND document.storage_mode = 'db_managed' AND document.state = 'active'
                GROUP BY document.id, document.relative_path, snapshot.markdown_content ORDER BY document.relative_path
            """), {"owner": args.owner_id}).mappings().all()
    finally:
        engine.dispose()
    items = [inspect_journal_artifacts(root, row["relative_path"], row["markdown_content"])
             for row in rows if row["markdown_content"] is not None]
    sidecars = [inspect_media_sidecars(root, row["relative_path"], row["markdown_content"])
                for row in rows if row["markdown_content"] is not None]
    print(json.dumps({"mode": "read_only", "owner_id": args.owner_id, **report_journal_artifacts(items),
                      "media_sidecar_consistency": sidecars},
                     ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
