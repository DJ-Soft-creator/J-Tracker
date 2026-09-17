#!/usr/bin/env python3
"""Rebuild the confirmed search index without changing Markdown files."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from db_search import index_document  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-9 Suchindex neu aufbauen")
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from sqlalchemy import create_engine, text

    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            rows = connection.execute(text("""
                SELECT document.id, document.current_revision_id,
                       COALESCE(snapshot.markdown_content,
                           string_agg(block_revision.markdown_content, '' ORDER BY manifest.position)) AS markdown_content
                FROM documents document
                LEFT JOIN document_snapshots snapshot ON snapshot.document_revision_id = document.current_revision_id
                LEFT JOIN document_revision_blocks manifest ON manifest.document_revision_id = document.current_revision_id
                LEFT JOIN block_revisions block_revision ON block_revision.id = manifest.block_revision_id
                WHERE document.owner_id = :owner AND document.scope = 'personal'
                  AND document.document_type = 'journal' AND document.storage_mode = 'db_managed'
                  AND document.state = 'active'
                GROUP BY document.id, document.current_revision_id, snapshot.markdown_content
            """), {"owner": args.owner_id}).mappings().all()
            if not args.dry_run:
                now = datetime.now(timezone.utc)
                for row in rows:
                    if row["markdown_content"] is None:
                        raise RuntimeError("Aktuelle Revision ist nicht rekonstruierbar: " + str(row["id"]))
                    index_document(connection, document_id=row["id"], revision_id=row["current_revision_id"],
                                   content=row["markdown_content"], now=now)
        print("indexed=" + str(len(rows)) + " dry_run=" + str(args.dry_run).lower())
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
