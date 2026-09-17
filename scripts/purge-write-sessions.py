#!/usr/bin/env python3
"""Abgeschlossene Schreibsessions aller Konten aufräumen.

Die App räumt beim Abruf der Sessionliste täglich selbst auf; dieses Skript ist
der Weg, einen aufgelaufenen Altbestand in einem Rutsch loszuwerden oder das
Ergebnis vorher anzusehen (`--dry-run`). Es fasst nur an, was
`write_session_cleanup` freigibt: gesendete und verworfene Sessions jenseits der
Frist, niemals aktive, archivierte oder gesendete mit übrig gebliebenen Medien.

    python3 scripts/purge-write-sessions.py --data-dir /pfad/zu/data --dry-run
"""

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP if APP.is_dir() else ROOT))
import write_session_cleanup  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "/app/data"))
    parser.add_argument("--days", type=int, default=None, help="Frist in Tagen (Standard: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Nur zeigen, was entfernt würde")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"Datenverzeichnis fehlt: {data_dir}", file=sys.stderr)
        return 1
    total = {"removed": 0, "kept": 0, "failed": 0}
    for root in sorted(data_dir.glob("*/write_sessions")):
        result = write_session_cleanup.purge_finished_sessions(root, days=args.days, dry_run=args.dry_run)
        for key in total:
            total[key] += len(result[key])
        if any(result.values()):
            print(f"{root.parent.name}: {len(result['removed'])} entfernt, "
                  f"{len(result['kept'])} behalten (Medien), {len(result['failed'])} fehlgeschlagen")
            for session_id in result["kept"]:
                print(f"    behalten, weil Medien vorhanden: {session_id}")
    verb = "würden entfernt" if args.dry_run else "entfernt"
    print(f"Summe: {total['removed']} {verb}, {total['kept']} behalten, {total['failed']} fehlgeschlagen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
