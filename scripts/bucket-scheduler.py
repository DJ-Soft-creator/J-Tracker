#!/usr/bin/env python3
"""Sammelt unbenutzte Medien vergangener Tage in die Tagesablage.

Vierter Compose-Scheduler nach dem Muster von ``scheduler.py``,
``db-checkpoint-scheduler.py`` und ``whisper-scheduler.py``: einmal taeglich,
Stunde konfigurierbar, idempotent.

Eingesammelt wird ausschliesslich, was **nicht verwendet** wurde — offene
Screenshots der Inbox (``status: pending``). Ein Screenshot, der an eine
Schreibsession gebunden oder in ein Journal uebernommen wurde, traegt einen
anderen Status und bleibt unberuehrt.

Damit gilt die Invariante aus ``docs/db/phase-10-media-inventory.md``:
referenzierte Medien werden nie verschoben, denn ein gueltiger
``jt:media``-Marker verlangt ein hashgleiches Attachment unter seinem datierten
Pfad. Unbenutzte Medien tragen per Definition keinen solchen Marker.
"""

import argparse
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path


DAY_BUCKET = "Tagesablage"
INBOX = "Screenshot-Inbox"
DEFAULT_HOUR = 0


def _now():
    return datetime.now().astimezone()


def _iso(value):
    return value.isoformat(timespec="seconds")


def _read_json(path):
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o660)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _companion_files(metadata_path, item):
    """Bild und Vorschau zu einem Inbox-Eintrag, streng im selben Ordner."""
    found = []
    for key in ("source_path", "preview_path"):
        relative = item.get(key)
        if not isinstance(relative, str) or not relative:
            continue
        candidate = metadata_path.parent / Path(relative).name
        if candidate.is_file() and not candidate.is_symlink():
            found.append(candidate)
    return found


def collect_day(user_root, day, *, now=None):
    """Offene Screenshots eines Tages in die Tagesablage verschieben."""
    year, month, date = day.split("-")
    inbox = Path(user_root) / year / month / date / "media" / INBOX
    if not inbox.is_dir():
        return 0
    target = Path(user_root) / year / month / date / "media" / DAY_BUCKET
    moved = 0
    for metadata_path in sorted(inbox.glob("*.json")):
        item = _read_json(metadata_path)
        if not item or item.get("status") != "pending":
            continue
        files = _companion_files(metadata_path, item)
        if not files:
            # Ohne Datei gibt es nichts einzusammeln; der verwaiste Eintrag
            # bleibt liegen, statt still zu verschwinden.
            continue
        target.mkdir(parents=True, exist_ok=True)
        try:
            for path in files:
                shutil.move(str(path), str(target / path.name))
        except OSError:
            continue
        item.update(status="collected", origin=INBOX,
                    collected_at=_iso(now or _now()))
        try:
            _write_json(target / metadata_path.name, item)
            metadata_path.unlink(missing_ok=True)
        except OSError:
            continue
        moved += 1
    return moved


def run_once(data_root, *, now=None, keep_days=1):
    """Alle Nutzer, alle Tage bis einschliesslich gestern."""
    reference = (now or _now()).date()
    limit = reference - timedelta(days=max(0, keep_days))
    total = 0
    root = Path(data_root)
    if not root.is_dir():
        return 0
    for user_root in sorted(root.iterdir()):
        if not user_root.is_dir() or user_root.name.startswith("."):
            continue
        for inbox in user_root.glob(f"[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/media/{INBOX}"):
            parts = inbox.parts
            day = f"{parts[-5]}-{parts[-4]}-{parts[-3]}"
            try:
                if datetime.strptime(day, "%Y-%m-%d").date() > limit:
                    continue  # Der laufende Tag bleibt unangetastet.
            except ValueError:
                continue
            total += collect_day(user_root, day, now=now)
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=os.environ.get("DATA_DIR", "/app/data"))
    parser.add_argument("--hour", type=int,
                        default=int(os.environ.get("BUCKET_HOUR", DEFAULT_HOUR)))
    parser.add_argument("--loop", action="store_true")
    arguments = parser.parse_args()

    moved = run_once(arguments.data_root)
    print(f"Tagesablage: {moved} Medien eingesammelt", flush=True)
    if not arguments.loop:
        return

    while True:
        now = _now()
        target = now.replace(hour=arguments.hour % 24, minute=5, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        time.sleep(max(60, (target - now).total_seconds()))
        try:
            moved = run_once(arguments.data_root)
            print(f"Tagesablage: {moved} Medien eingesammelt", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"Tagesablage: Lauf fehlgeschlagen: {error}", flush=True)


if __name__ == "__main__":
    main()
