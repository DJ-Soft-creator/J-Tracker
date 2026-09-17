"""Lange Laeufe, die einen geschlossenen Browser ueberleben.

AI maintenance contract: dieses Modul kennt keine Fachlogik. Es haelt Zustand,
Fortschritt und Abbruchwunsch eines Laufs und startet den Arbeits-Thread. Was
ein Lauf tut, steht im Fachmodul und wird ueber ``register`` eingehaengt.

Warum es das gibt: das nachtraegliche Verschlagworten und die Chatantwort liefen
als Schleife im Browser. Sperrt sich das Handy, bricht die Schleife ab, obwohl
der Server weiterrechnet. Hier laeuft die Schleife im Server, der Browser fragt
nur noch den Stand ab und darf jederzeit weggehen und spaeter wiederkommen.

Ein Lauf liegt als JSON unter ``DATA_DIR/<user>/.runs/<id>.json`` und bewusst
nicht nur im Speicher: nur so sieht eine zweite Registerkarte denselben Lauf,
und nur so ist nach einem Neustart erkennbar, dass ein Lauf abgebrochen wurde,
statt still zu verschwinden.
"""

import json
import logging
import re
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

from scheduling import read_text_file, update_text_file, write_text_file

logger = logging.getLogger(__name__)

RUNS_DIRNAME = ".runs"
SCHEMA = 1

ACTIVE_STATUSES = ("queued", "running")
FINAL_STATUSES = ("done", "failed", "cancelled", "aborted")

# Ein Lauf haelt nur die juengsten Logzeilen. Ein Jahreslauf ueber 365 Journale
# soll den Fortschritt zeigen, nicht ein Protokoll aufbewahren, das niemand
# liest und das jede Antwort aufblaeht.
MAX_LOG_ENTRIES = 500
# Fertige Laeufe verschwinden von selbst; sie sind Statusanzeige, kein Archiv.
RETENTION_DAYS = 7

_ID_RE = re.compile(r"^[0-9a-f-]{36}$")

# Jeder Lauf bekommt seinen eigenen Thread, gedeckelt durch dieses Semaphor.
# Ein einzelner Arbeiter waere bequemer, wuerde aber alles hintereinander legen:
# eine Chatantwort, die Minuten braucht, haette jede Verschlagwortung blockiert —
# auch die eines anderen Kontos.
MAX_CONCURRENT_RUNS = 4

_handlers = {}
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)


class RunError(RuntimeError):
    """Fehler, den die Oberflaeche dem Nutzer wortwoertlich zeigen darf."""


class RunConflict(RunError):
    """Fuer diesen Nutzer laeuft bereits ein Lauf derselben Art."""


def register(kind, handler):
    """Haenge die Arbeit einer Laufart ein: ``handler(handle, params) -> dict``."""
    _handlers[kind] = handler


# ─── Ablage ───────────────────────────────────────────────────────────
def _core():
    """Die Fassade erst beim Zugriff aufloesen.

    Dieses Modul wird ueber brain -> historical_tagging schon importiert, bevor
    ``main`` selbst in ``sys.modules`` steht; ein zur Importzeit gemerkter Verweis
    zeigte deshalb auf das falsche Modul. Ausserdem tauschen die Tests
    ``main.DATA_DIR`` aus, was nur ein spaeter Zugriff mitbekommt.
    """
    return sys.modules.get("main") or sys.modules.get("__main__")


def _runs_dir(user_id):
    return _core().DATA_DIR / user_id / RUNS_DIRNAME


def _run_path(user_id, run_id):
    return _runs_dir(user_id) / "{}.json".format(run_id)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(user_id, run_id):
    if not isinstance(run_id, str) or not _ID_RE.match(run_id):
        return None
    path = _run_path(user_id, run_id)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        record = json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _write(user_id, run_id, record):
    record["updated_at"] = _now()
    write_text_file(_run_path(user_id, run_id), json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return record


def _modify(user_id, run_id, change):
    """Lies, aendere und schreibe einen Lauf unter Sperre.

    Fortschritt schreibt der Arbeits-Thread, den Abbruchwunsch der Request-Thread.
    Ohne diese Klammer ueberschreibt der eine still den anderen.
    """
    path = _run_path(user_id, run_id)

    def updater(current):
        try:
            record = json.loads(current) if current else None
        except json.JSONDecodeError:
            record = None
        if not isinstance(record, dict):
            return current, None
        change(record)
        record["updated_at"] = _now()
        return json.dumps(record, ensure_ascii=False, indent=2) + "\n", record

    return update_text_file(path, updater)


# ─── Sicht fuer den Browser ───────────────────────────────────────────
def public_run(record, log_from=0):
    """Der Lauf, wie ihn die Oberflaeche sehen darf — ohne inneren Ballast."""
    log = record.get("log") or []
    try:
        log_from = max(0, int(log_from))
    except (TypeError, ValueError):
        log_from = 0
    offset = int(record.get("log_offset") or 0)
    # Der Browser zaehlt in absoluten Positionen. Sind ihm Zeilen durch die
    # Deckelung entgangen, bekommt er ehrlich den vorhandenen Rest.
    start = max(0, log_from - offset)
    return {
        "id": record.get("id", ""),
        "kind": record.get("kind", ""),
        "label": record.get("label", ""),
        "status": record.get("status", ""),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
        "finished_at": record.get("finished_at", ""),
        "progress": record.get("progress") or {},
        "result": record.get("result") or {},
        "error": record.get("error", ""),
        "cancel_requested": bool(record.get("cancel_requested")),
        "active": record.get("status") in ACTIVE_STATUSES,
        "log": log[start:],
        "log_from": offset + start,
        "log_total": offset + len(log),
    }


# ─── Griff fuer die Fachmodule ────────────────────────────────────────
class RunHandle:
    """Was ein laufender Auftrag von seinem eigenen Lauf wissen muss."""

    def __init__(self, user_id, run_id):
        self.user_id = user_id
        self.run_id = run_id

    @property
    def cancelled(self):
        record = _read(self.user_id, self.run_id)
        return bool(record and record.get("cancel_requested"))

    def progress(self, **values):
        def change(record):
            record.setdefault("progress", {}).update(values)

        _modify(self.user_id, self.run_id, change)

    def log(self, entry):
        def change(record):
            record.setdefault("log", []).append(entry)
            overflow = len(record["log"]) - MAX_LOG_ENTRIES
            if overflow > 0:
                del record["log"][:overflow]
                record["log_offset"] = int(record.get("log_offset") or 0) + overflow

        _modify(self.user_id, self.run_id, change)


# ─── Lebenszyklus ─────────────────────────────────────────────────────
def start(user_id, kind, params, *, label=""):
    """Reihe einen Lauf ein. Pro Nutzer und Art laeuft immer nur einer.

    Der Deckel ist keine Bequemlichkeit: zwei Verschlagwortungsläufe wuerden
    dieselben Journale gleichzeitig umschreiben.
    """
    if kind not in _handlers:
        raise RunError("Unbekannte Laufart: {}".format(kind))
    existing = active(user_id, kind)
    if existing:
        raise RunConflict("Es läuft bereits ein Auftrag dieser Art.")
    run_id = str(uuid.uuid4())
    record = {
        "schema": SCHEMA, "id": run_id, "kind": kind, "user_id": user_id,
        "label": label or kind, "status": "queued", "params": params or {},
        "created_at": _now(), "updated_at": _now(), "finished_at": "",
        "progress": {}, "log": [], "log_offset": 0, "result": {},
        "error": "", "cancel_requested": False,
    }
    _write(user_id, run_id, record)
    _spawn(user_id, run_id)
    return record


def read(user_id, run_id):
    return _read(user_id, run_id)


def cancel(user_id, run_id):
    """Bitte um Abbruch. Der Lauf beendet sich an der naechsten sauberen Stelle."""
    def change(record):
        if record.get("status") in ACTIVE_STATUSES:
            record["cancel_requested"] = True

    record = _modify(user_id, run_id, change)
    if record is None:
        return None
    if record.get("status") == "queued":
        # Noch nicht gestartet: sofort beenden statt den Arbeiter erst anwerfen.
        record = _finish(user_id, run_id, "cancelled", error="Vor dem Start abgebrochen.")
    return record


def discard(user_id, run_id):
    """Entferne einen beendeten Lauf. Ein laufender wird nicht weggeraeumt."""
    record = _read(user_id, run_id)
    if record is None:
        return False
    if record.get("status") in ACTIVE_STATUSES:
        raise RunConflict("Der Auftrag läuft noch.")
    path = _run_path(user_id, run_id)
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _records(user_id):
    directory = _runs_dir(user_id)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        record = _read(user_id, path.stem)
        if record:
            records.append(record)
    return records


def active(user_id, kind=None):
    """Der laufende oder wartende Auftrag dieser Art, falls es einen gibt."""
    for record in _records(user_id):
        if record.get("status") in ACTIVE_STATUSES and (kind is None or record.get("kind") == kind):
            return record
    return None


def listing(user_id, kind=None):
    """Laufende zuerst, danach die juengsten fertigen."""
    records = [record for record in _records(user_id) if kind is None or record.get("kind") == kind]
    records.sort(key=lambda record: record.get("created_at", ""), reverse=True)
    records.sort(key=lambda record: record.get("status") not in ACTIVE_STATUSES)
    return records


def cleanup(user_id, now=None):
    """Beendete Laeufe nach RETENTION_DAYS entfernen."""
    limit = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    removed = 0
    for record in _records(user_id):
        if record.get("status") in ACTIVE_STATUSES:
            continue
        stamp = record.get("finished_at") or record.get("updated_at") or ""
        try:
            finished = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        if finished < limit:
            try:
                _run_path(user_id, record["id"]).unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _finish(user_id, run_id, status, *, result=None, error=""):
    def change(record):
        record["status"] = status
        record["finished_at"] = _now()
        if result is not None:
            record["result"] = result
        if error:
            record["error"] = error

    return _modify(user_id, run_id, change)


def abort_stale_runs():
    """Nach einem Neustart gibt es keine laufenden Auftraege mehr.

    Ein Lauf, der als „laeuft" im Dateisystem stehen bleibt, blockiert sonst
    fuer immer den naechsten Start derselben Art — und die Oberflaeche wartet
    auf einen Fortschritt, den niemand mehr meldet.
    """
    root = _core().DATA_DIR
    if not root.is_dir():
        return 0
    aborted = 0
    for directory in sorted(root.glob("*/{}".format(RUNS_DIRNAME))):
        user_id = directory.parent.name
        for record in _records(user_id):
            if record.get("status") not in ACTIVE_STATUSES:
                continue
            _finish(user_id, record["id"], "aborted",
                    error="Die App wurde neu gestartet, der Auftrag wurde beendet.")
            aborted += 1
        cleanup(user_id)
    if aborted:
        logger.info("Background runs marked as aborted after restart: %d", aborted)
    return aborted


# ─── Arbeiter ─────────────────────────────────────────────────────────
def _run_one(user_id, run_id):
    record = _read(user_id, run_id)
    if not record or record.get("status") != "queued":
        return
    if record.get("cancel_requested"):
        _finish(user_id, run_id, "cancelled", error="Vor dem Start abgebrochen.")
        return
    handler = _handlers.get(record.get("kind"))
    if handler is None:
        _finish(user_id, run_id, "failed", error="Für diesen Auftrag gibt es keine Verarbeitung mehr.")
        return
    _modify(user_id, run_id, lambda item: item.update({"status": "running"}))
    handle = RunHandle(user_id, run_id)
    try:
        result = handler(handle, record.get("params") or {})
    except RunError as exc:
        _finish(user_id, run_id, "failed", error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - ein Auftrag darf den Arbeiter nie beenden
        logger.exception("Background run %s (%s) failed", run_id, record.get("kind"))
        _finish(user_id, run_id, "failed", error="{}: {}".format(type(exc).__name__, exc))
        return
    status = "cancelled" if handle.cancelled else "done"
    _finish(user_id, run_id, status, result=result if isinstance(result, dict) else {})


def _worker(user_id, run_id):
    """Ein Lauf, ein Thread — der Platz im Semaphor wird immer zurueckgegeben."""
    with _slots:
        try:
            _run_one(user_id, run_id)
        except Exception:  # noqa: BLE001 - ein Auftrag darf nie stillschweigend verschwinden
            logger.exception("Background run %s crashed outside its handler", run_id)
            _finish(user_id, run_id, "failed", error="Der Auftrag ist unerwartet abgebrochen.")


def _spawn(user_id, run_id):
    thread = threading.Thread(
        target=_worker, args=(user_id, run_id), name="run-{}".format(run_id[:8]), daemon=True,
    )
    thread.start()
    return thread


__all__ = [
    "ACTIVE_STATUSES", "FINAL_STATUSES", "RunConflict", "RunError", "RunHandle",
    "abort_stale_runs", "active", "cancel", "cleanup", "discard", "listing",
    "MAX_CONCURRENT_RUNS", "public_run", "read", "register", "start",
]
