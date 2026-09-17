#!/usr/bin/env python3
"""Bounded Phase-3 shadow scan; it never writes Markdown source files."""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from shadow_index import inventory, reconcile, report, shadow_import  # noqa: E402


REPORT_RETENTION_DAYS = 30


def _write_report(directory, payload):
    directory.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=REPORT_RETENTION_DAYS)
    for path in directory.glob("phase3-*.json"):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
            path.unlink()
    target = directory / ("phase3-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, prefix=".phase3-", delete=False) as temp:
        json.dump(payload, temp, ensure_ascii=False, indent=2, sort_keys=True)
        temp.write("\n")
        temp_path = Path(temp.name)
    os.replace(temp_path, target)
    return target


def scan_once(data_dir):
    from sqlalchemy import create_engine

    data_root = Path(data_dir)
    roots = [path for path in data_root.iterdir() if path.is_dir() and any(path.rglob("Journal_*.md"))]
    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        per_root = []
        for root in roots:
            key = "dev-user-" + root.name
            items = inventory(root)
            with engine.begin() as connection:
                before = reconcile(connection, storage_root_key=key, items=items)
                imported = shadow_import(connection, owner_id=root.name, storage_root_key=key, items=items)
            per_root.append({"owner_id": root.name, "storage_root_key": key, "inventory": report(items)["summary"],
                             "reconciliation_before_import": before, "shadow_import": imported})
    finally:
        engine.dispose()
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "roots": per_root}
    report_path = _write_report(data_root / "indexes" / "db-reports", payload)
    return {"report": str(report_path), "roots": len(per_root)}


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-3 Shadow-Scan")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.interval_seconds < 1:
        raise SystemExit("--interval-seconds muss positiv sein")
    data_dir = os.environ.get("DATA_DIR", "/app/data")
    while True:
        print(json.dumps(scan_once(data_dir), ensure_ascii=False), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
