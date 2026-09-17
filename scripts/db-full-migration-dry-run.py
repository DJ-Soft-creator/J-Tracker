#!/usr/bin/env python3
"""Read-only Phase-13 readiness report for one authorised DEV journal root.

This is deliberately an audit, not a migration command.  It never writes a
Markdown file, database row, report file, or backup.  The JSON result contains
paths, IDs, hashes and aggregate state, but never journal, prompt or response
content.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
from shadow_index import inventory, reconcile, report  # noqa: E402


def _database_state(owner_id, storage_root_key):
    """Read only the state needed for migration readiness, never content."""
    from sqlalchemy import create_engine, text

    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            # PostgreSQL rejects accidental future writes on this connection.
            connection.execute(text("SET TRANSACTION READ ONLY"))
            documents = connection.execute(text("""
                SELECT id, relative_path, document_type, storage_mode, state,
                       materialized_file_hash, materialized_size
                FROM documents
                WHERE owner_id = :owner AND scope = 'personal'
                  AND storage_root_key = :root
                ORDER BY relative_path
            """), {"owner": owner_id, "root": storage_root_key}).mappings().all()
            jobs = connection.execute(text("""
                SELECT job.id, job.status AS job_status, job.error_code,
                       response.status AS response_status
                FROM background_jobs job
                LEFT JOIN agent_requests request ON request.id = job.agent_request_id
                LEFT JOIN agent_responses response ON response.request_id = request.id
                WHERE job.owner_id = :owner
                ORDER BY job.created_at
            """), {"owner": owner_id}).mappings().all()
    finally:
        engine.dispose()
    return documents, jobs


def _free_space(root):
    usage = os.statvfs(root)
    return {
        "available_bytes": usage.f_bavail * usage.f_frsize,
        "filesystem_total_bytes": usage.f_blocks * usage.f_frsize,
        "journal_root_bytes": sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and not path.is_symlink()),
    }


def plan(root, owner_id, storage_root_key, *, now=None):
    """Return a content-free readiness report for exactly one personal root."""
    root = Path(root)
    items = inventory(root)
    documents, jobs = _database_state(owner_id, storage_root_key)
    item_report = report(items)

    # ``reconcile`` compares the exact bytes observed above to the indexed DB
    # state and has no side effects.
    from sqlalchemy import create_engine, text
    engine = create_engine(db.database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            reconciliation = reconcile(connection, storage_root_key=storage_root_key, items=items)
    finally:
        engine.dispose()

    active_jobs = [job for job in jobs if job["job_status"] in {"queued", "leased"}]
    visible_answers = [job for job in jobs if job["response_status"] == "proposed"]
    blocked_items = [item.relative_path for item in items if item.status in {"blocked", "migration_blocked"}]
    special_items = [item.relative_path for item in items if item.status == "special"]
    reconciliation_findings = {
        key: value for key, value in reconciliation.items() if value
    }
    db_documents = [{
        "id": str(row["id"]), "relative_path": row["relative_path"],
        "document_type": row["document_type"], "storage_mode": row["storage_mode"],
        "state": row["state"], "materialized_file_hash": row["materialized_file_hash"],
        "materialized_size": row["materialized_size"],
    } for row in documents]

    journal_documents = [row for row in db_documents if row["document_type"] == "journal"]
    not_managed = [row["relative_path"] for row in journal_documents if row["storage_mode"] != "db_managed"]
    blockers = {
        "inventory_blocked": blocked_items,
        "reconciliation": reconciliation_findings,
        "active_background_jobs": [str(job["id"]) for job in active_jobs],
    }
    blocking = bool(blocked_items or reconciliation_findings or active_jobs)
    return {
        "mode": "read_only_phase_13_dry_run",
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "owner_id": owner_id,
        "storage_root_key": storage_root_key,
        "scope": {
            "implemented_document_types": ["journal"],
            "excluded_document_types": ["brain", "family", "notes", "projects"],
            "note": "Kein Voll-Cutover: Dieser Bericht bewertet nur den bereits implementierten Journal-Pfad.",
        },
        "inventory": item_report["summary"],
        "reconciliation": reconciliation,
        "database_documents": db_documents,
        "special_files": special_items,
        "background_jobs": {
            "active_job_ids": [str(job["id"]) for job in active_jobs],
            "proposed_answer_job_ids": [str(job["id"]) for job in visible_answers],
            "note": "Antworttexte werden nicht ausgegeben.",
        },
        "storage": _free_space(root),
        "backup": {"verified": False, "required_before_apply": True},
        "expected_changes": {
            "journal_paths_not_yet_db_managed": not_managed,
            "markdown_writes": 0,
            "database_writes": 0,
        },
        "blockers": blockers,
        "ready_for_any_apply": not blocking and not not_managed,
        "next_gate": "Review des Berichts; Backup- und Batch-Freigabe sind getrennte, explizite Schritte.",
    }


def main():
    parser = argparse.ArgumentParser(description="Journl Phase-13 DEV-Migrations-Dry-Run (nur lesend)")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--storage-root-key", required=True)
    args = parser.parse_args()
    payload = plan(args.root, args.owner_id, args.storage_root_key)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
