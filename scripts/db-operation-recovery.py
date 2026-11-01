#!/usr/bin/env python3
"""Recover only open Phase-5 operations; never discovers arbitrary write roots."""

import argparse
import os
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from document_store import configured_document_store  # noqa: E402


def _storage_prefix():
    """Root-Key-Prefix: PROD-Spiegel nutzt 'prod-user', DEV bleibt bei 'dev-user'."""
    return os.environ.get("JOURNL_DB_STORAGE_PREFIX", "dev-user").strip()


def dev_user_roots(data_dir):
    """Return only canonical UUID user roots used by the shadow importer."""
    data_root = Path(data_dir)
    if data_root.is_symlink() or not data_root.is_dir():
        raise RuntimeError("DATA_DIR ist kein zulaessiger Datenroot")
    result = {}
    for candidate in data_root.iterdir():
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            uuid.UUID(candidate.name)
        except ValueError:
            continue
        result[_storage_prefix() + "-" + candidate.name] = candidate.resolve(strict=True)
    return result


def run_once(data_dir, *, limit=100):
    from sqlalchemy import create_engine

    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        return configured_document_store(engine).recover_open_operations(
            storage_roots=dev_user_roots(data_dir), limit=limit
        )
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-5 Recovery-Worker")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.interval_seconds < 1 or args.limit < 1:
        raise SystemExit("Intervall und Limit muessen positiv sein")
    data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
    while True:
        try:
            print(len(run_once(data_dir, limit=args.limit)), flush=True)
        except Exception as error:
            print("operation recovery retry: " + type(error).__name__, file=sys.stderr, flush=True)
        if not args.loop:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
