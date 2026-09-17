"""Abgeschlossene Schreibsessions nach einer Frist entfernen.

Jede Schreibsession ist ein Ordner unter ``<user>/write_sessions/`` mit dem
Entwurfstext in ``session.json`` — das ist der Grund, warum ein Entwurf einen
Gerätewechsel oder einen Absturz übersteht.  Gesendet oder verworfen wird die
Session abgeschlossen, aber ihr Ordner blieb bisher für immer liegen: in PROD
waren das nach fünf Wochen über 500 Ordner.

Aufgeräumt wird deshalb, was nachweislich erledigt ist:

* ``submitted`` — der Eintrag steht im Journal, die Anhänge liegen im
  Journalbaum, die Session ist nur noch Protokoll.
* ``discarded`` — bewusst verworfen; Inbox-Anhänge hat der Verwerfen-Weg
  bereits freigegeben.

Nicht aufgeräumt wird, was noch gebraucht werden könnte: aktive und archivierte
Sessions, und jede gesendete Session, in der noch Mediendateien liegen — dann
ist der Submit nicht sauber durchgelaufen und die Aufnahme existiert vielleicht
nur hier.  Solche Fälle werden gemeldet statt gelöscht.
"""

import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scheduling import path_lock, write_text_file


FINISHED_STATUS = {"submitted", "discarded"}
DEFAULT_RETENTION_DAYS = 30
_ID_RE = re.compile(r"^[0-9a-f-]{36}$")


def retention_days():
    try:
        value = int(os.environ.get("WRITE_SESSION_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return value if value >= 1 else DEFAULT_RETENTION_DAYS


def _parse(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _finished_at(data):
    """When the session was closed; the oldest reliable timestamp wins."""
    for key in ("submitted_at", "discarded_at", "updated_at", "created_at"):
        parsed = _parse(data.get(key))
        if parsed:
            return parsed
    return None


def _has_media_files(directory):
    media = directory / "media"
    if not media.is_dir():
        return False
    return any(path.is_file() and path.suffix != ".lock" for path in media.rglob("*"))


def purge_finished_sessions(root, now=None, days=None, dry_run=False):
    """Remove finished session folders under one user's ``write_sessions``.

    Returns ``{"removed": [ids], "kept": [ids], "failed": [ids]}`` — ``kept``
    names the sessions that still hold media although they were submitted.
    """
    result = {"removed": [], "kept": [], "failed": []}
    root = Path(root)
    if not root.is_dir():
        return result
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days or retention_days())
    for path in sorted(root.glob("*/session.json")):
        directory = path.parent
        if directory.is_symlink() or not _ID_RE.fullmatch(directory.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("id") != directory.name:
            continue
        if data.get("status") not in FINISHED_STATUS:
            continue
        finished = _finished_at(data)
        if not finished or finished > cutoff:
            continue
        if data.get("status") == "submitted" and _has_media_files(directory):
            result["kept"].append(directory.name)
            continue
        if dry_run:
            result["removed"].append(directory.name)
            continue
        try:
            shutil.rmtree(directory)
        except OSError:
            result["failed"].append(directory.name)
            continue
        result["removed"].append(directory.name)
    return result


def purge_daily(root, now=None):
    """Einmal am Tag aufräumen, angestossen vom Abruf der Sessionliste.

    Der Schreiben-Tab fragt seine Sessions ohnehin regelmaessig ab; das ist der
    billigste verlaessliche Anlass und spart einen weiteren Sidecar. Ein Fehler
    beim Aufraeumen darf die Liste nie kosten - sie ist die Funktion, das
    Aufraeumen nur Hygiene.
    """
    root = Path(root)
    marker = root / ".cleanup.json"
    today = (now or datetime.now(timezone.utc)).astimezone().date().isoformat()

    def ran_today():
        try:
            return json.loads(marker.read_text(encoding="utf-8")).get("last_run") == today
        except (OSError, json.JSONDecodeError, AttributeError):
            return False

    try:
        if not root.is_dir() or ran_today():
            return None
        with path_lock(root / ".collection", exclusive=True):
            if ran_today():
                return None
            result = purge_finished_sessions(root, now)
            write_text_file(marker, json.dumps(
                {"last_run": today, **{key: len(value) for key, value in result.items()}},
                ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            return result
    except OSError:
        return None
