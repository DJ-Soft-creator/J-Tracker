#!/usr/bin/env python3
"""Phase-3 journal inventory and opt-in shadow importer; never writes Markdown."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# The repository has ``app/``; the production Docker image copies its contents
# directly to /app.  Support both layouts without depending on the cwd.
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from shadow_index import inventory, reconcile, report, shadow_import  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-3-Inventur (nur lesend)")
    parser.add_argument("--root", required=True, help="autorisierter Benutzerroot mit Tagesjournalen")
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--storage-root-key", required=True)
    parser.add_argument("--dry-run", action="store_true", help="nur Bericht, keine DB-Schreibvorgaenge")
    parser.add_argument("--reconcile", action="store_true", help="DB-Shadow-Index nur lesend vergleichen")
    parser.add_argument("--summary-only", action="store_true", help="keine Einzeldateien im Bericht ausgeben")
    parser.add_argument("--report", type=Path, help="JSON-Bericht; '-' fuer stdout")
    args = parser.parse_args()
    items = inventory(args.root)
    result = report(items)
    if not args.dry_run or args.reconcile:
        from sqlalchemy import create_engine
        engine = create_engine(db.database_url(), pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                if not args.dry_run:
                    result["shadow_import"] = shadow_import(connection, owner_id=args.owner_id,
                        storage_root_key=args.storage_root_key, items=items)
                if args.reconcile:
                    result["reconciliation"] = reconcile(connection, storage_root_key=args.storage_root_key, items=items)
        finally:
            engine.dispose()
    if args.summary_only:
        result.pop("items")
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report and str(args.report) != "-":
        args.report.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)


if __name__ == "__main__":
    main()
