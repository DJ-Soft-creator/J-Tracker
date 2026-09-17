#!/usr/bin/env python3
"""Read-only plan for a later clean-Markdown journal migration.

This command intentionally has no apply mode.  It lists only document paths,
marker kinds and blocking reasons; journal and agent content never leaves the
process.  A future writer must consume this plan's exact safeguards rather than
relaxing them.
"""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

from legacy_migration import assess_legacy_migration, summary  # noqa: E402
from shadow_index import inventory  # noqa: E402


def plan(root):
    entries = []
    decisions = []
    for item in inventory(root):
        if item.status != "eligible":
            continue
        decision = assess_legacy_migration(item.markdown_content)
        decisions.append(decision)
        entries.append({"relative_path": item.relative_path, **decision.report()})
    return {"mode": "read_only_legacy_migration_plan", "journals": entries,
            "summary": summary(decisions),
            "next_gate": "Nur ready oder ready_for_archive_cleanup darf nach Backup und Review in einen späteren Schreibbatch; blocked bleibt unverändert."}


def main():
    parser = argparse.ArgumentParser(description="Journl Legacy-Journal-Migrationsplan (nur lesend)")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(plan(args.root), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
