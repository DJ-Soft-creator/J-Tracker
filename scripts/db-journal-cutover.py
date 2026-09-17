#!/usr/bin/env python3
"""Plan or explicitly activate a bounded DEV journal cutover.

The default mode is read-only.  ``--apply --confirm <run-id>`` only updates
already shadow-indexed, active, hash-matching personal journals; Markdown is
never changed by this tool.
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
from shadow_index import inventory  # noqa: E402


def _date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise argparse.ArgumentTypeError("Datum muss YYYY-MM-DD sein") from error


def _candidates(root, start, end):
    result = []
    for item in inventory(root):
        if item.status != "eligible":
            continue
        filename = Path(item.relative_path).name
        journal_date = _date(filename.removeprefix("Journal_").removesuffix(".md"))
        if start <= journal_date <= end:
            result.append((journal_date, item))
    return result


def plan(root, owner_id, storage_root_key, start, end):
    from sqlalchemy import create_engine, text

    candidates = _candidates(root, start, end)
    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id, relative_path, storage_mode, state, materialized_file_hash
                FROM documents WHERE scope = 'personal' AND storage_root_key = :root
            """), {"root": storage_root_key}).mappings().all()
    finally:
        engine.dispose()
    indexed = {row["relative_path"]: row for row in rows}
    entries = []
    for journal_date, item in candidates:
        row = indexed.get(item.relative_path)
        reason = None
        if row is None:
            reason = "nicht im Shadow-Index"
        elif row["storage_mode"] == "db_managed":
            reason = "bereits verwaltet"
        elif row["storage_mode"] != "shadow_indexed":
            reason = "kein shadow_indexed-Dokument"
        elif row["state"] != "active":
            reason = "Dokument nicht aktiv"
        elif row["materialized_file_hash"] != item.file_hash:
            reason = "Dateihash weicht vom Shadow-Index ab"
        entries.append({"date": journal_date.isoformat(), "relative_path": item.relative_path,
                        "document_id": str(row["id"]) if row else None,
                        "materialized_file_hash": item.file_hash,
                        "eligible": reason is None, "reason": reason})
    return {"owner_id": owner_id, "storage_root_key": storage_root_key,
            "range": {"start": start.isoformat(), "end": end.isoformat()}, "journals": entries,
            "summary": {"eligible": sum(item["eligible"] for item in entries),
                        "excluded": sum(not item["eligible"] for item in entries)}}


def apply_cutover(payload, confirm):
    run_id = payload["run_id"]
    if confirm != run_id:
        raise ValueError("--confirm muss exakt der angezeigten run_id entsprechen")
    from sqlalchemy import create_engine, text
    eligible = [entry for entry in payload["journals"] if entry["eligible"]]
    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            for entry in eligible:
                updated = connection.execute(text("""
                    UPDATE documents SET storage_mode = 'db_managed', updated_at = :now
                    WHERE id = :id AND storage_mode = 'shadow_indexed' AND state = 'active'
                      AND materialized_file_hash = :hash
                """), {"id": entry["document_id"], "hash": entry["materialized_file_hash"],
                       "now": datetime.now(timezone.utc)})
                if updated.rowcount != 1:
                    raise RuntimeError("Cutover-Vorbedingung hat sich für " + entry["relative_path"] + " geändert")
    finally:
        engine.dispose()
    return len(eligible)


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-8 DEV-Journal-Cutover")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--storage-root-key", required=True)
    parser.add_argument("--from", dest="start", required=True, type=_date)
    parser.add_argument("--to", dest="end", required=True, type=_date)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--from darf nicht nach --to liegen")
    payload = plan(args.root, args.owner_id, args.storage_root_key, args.start, args.end)
    fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["run_id"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    payload["mode"] = "dry_run"
    if args.apply:
        payload["applied"] = apply_cutover(payload, args.confirm)
        payload["mode"] = "applied"
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
