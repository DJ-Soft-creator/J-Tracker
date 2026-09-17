#!/usr/bin/env python3
"""Promote due Phase-4 external working states; never writes Markdown files."""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from revisions import promote_due_checkpoints  # noqa: E402


def _media_roots(data_dir):
    root = Path(data_dir)
    if root.is_symlink() or not root.is_dir():
        return {}
    return {"dev-user-" + path.name: path for path in root.iterdir()
            if path.is_dir() and not path.is_symlink()}


def run_once(*, limit=100, now=None, data_dir=None):
    from sqlalchemy import create_engine

    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            return promote_due_checkpoints(connection, now=now or datetime.now(timezone.utc), limit=limit,
                                           media_roots=_media_roots(data_dir or os.environ.get("DATA_DIR", "/app/data")))
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-4 Checkpoint-Scheduler")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.interval_seconds < 1:
        raise SystemExit("--interval-seconds muss positiv sein")
    while True:
        try:
            print(len(run_once(limit=args.limit)), flush=True)
        except Exception as error:
            # The next interval retries transient PostgreSQL errors.  Markdown is
            # never touched here, and the error deliberately contains no content.
            print("checkpoint scheduler retry: " + type(error).__name__, file=sys.stderr, flush=True)
        if not args.loop:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
