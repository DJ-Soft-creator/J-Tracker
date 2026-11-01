"""Datei-Export der Journal-Analyse-Verwaltung.

Die Datenbank ist die maßgebliche Quelle für Prozessmetadaten (Job-Definition,
Prompt-Vorlage, Lauf-Status, Rolling State, Benachrichtigungen). Dieses Modul
spiegelt genau diese Metadaten unter ``<DATA_DIR>/<user>/journal-analysen/``
als Dateien — damit ein DB-Restore aus dem Dateibestand möglich bleibt
(Entscheidung JA-02 in ``docs/project/Anforderung-Journal-Analyse.md``).

Journale selbst bleiben unberührt: Sie sind Datei-Master, die DB ist dort nur
Beschleuniger. Dieses Modul schreibt ausschließlich unterhalb des eigenen
Nutzerordners und niemals in ein Tagesjournal.

Ablage je Job::

    <user>/journal-analysen/<slug>/
        job.json
        summary.md
        state.json
        runs/YYYY-MM-DD.json
        notifications.json

Geschrieben wird über die atomaren Primitive aus ``scheduling.py``
(``.lock`` + Temp-Datei + ``fsync`` + ``os.replace``) — nicht mit
``open(..., 'w')`` daneben. Gelesen wird defensiv: eine unlesbare Datei wird als
fehlend behandelt und kippt keinen Aufrufer.
"""

import json
import re
import sys
from pathlib import Path

import scheduling


ANALYSIS_DIRNAME = "journal-analysen"
RUNS_DIRNAME = "runs"

# Ein Nutzer- oder Job-Segment muss ein einfacher Ordnername sein: kein Pfad,
# kein „..“, kein führender Punkt. Bewusst ohne Unicode, damit die Ablage auch
# über andere Werkzeuge hinweg vorhersagbar bleibt.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def _data_root(root=None):
    if root is not None:
        return Path(root)
    module = sys.modules.get("main") or sys.modules.get("__main__")
    if module is None or not getattr(module, "DATA_DIR", None):
        raise RuntimeError("DATA_DIR ist nicht verfügbar")
    return Path(module.DATA_DIR)


def _segment(value, field):
    if not isinstance(value, str) or not _SEGMENT_RE.match(value) or value in {".", ".."}:
        raise ValueError(f"Ungültiger {field}")
    return value


def slugify(name):
    """Einen sicheren Ordnernamen aus einem Anzeigenamen ableiten.

    Leere oder nur aus Trennzeichen bestehende Namen ergeben ``""``; der
    Aufrufer vergibt dann einen Ersatznamen. Kollisionen löst der Aufrufer über
    ein Suffix, nicht dieses Modul.
    """
    text = str(name or "").strip().lower()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80]


def user_root(user_id, *, root=None):
    return _data_root(root) / _segment(user_id, "Nutzer")


def job_root(user_id, slug, *, root=None):
    return user_root(user_id, root=root) / ANALYSIS_DIRNAME / _segment(slug, "Job")


def job_json_path(user_id, slug, *, root=None):
    return job_root(user_id, slug, root=root) / "job.json"


def summary_path(user_id, slug, *, root=None):
    return job_root(user_id, slug, root=root) / "summary.md"


def state_path(user_id, slug, *, root=None):
    return job_root(user_id, slug, root=root) / "state.json"


def runs_dir(user_id, slug, *, root=None):
    return job_root(user_id, slug, root=root) / RUNS_DIRNAME


def run_path(user_id, slug, run_date, *, root=None):
    day = _run_date(run_date)
    return runs_dir(user_id, slug, root=root) / f"{day}.json"


def notifications_path(user_id, slug, *, root=None):
    return job_root(user_id, slug, root=root) / "notifications.json"


def _run_date(value):
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    text = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("Ungültiges Laufdatum")
    return text


def _write_json(path, payload):
    scheduling.write_text_file(
        path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_json(path):
    try:
        raw = scheduling.read_text_file(path)
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value


def write_job(user_id, slug, job, *, root=None):
    """Job-Definition exportieren (schlüsselfertiges JSON-Dict)."""
    _write_json(job_json_path(user_id, slug, root=root), dict(job))


def read_job(user_id, slug, *, root=None):
    value = _read_json(job_json_path(user_id, slug, root=root))
    return value if isinstance(value, dict) else None


def write_summary(user_id, slug, summary, *, root=None):
    """Die fortgeschriebene Zusammenfassung als Markdown.

    Ein leerer Text legt eine leere Datei an statt sie zu löschen: Der Export
    soll den DB-Zustand spiegeln, nicht interpretieren.
    """
    scheduling.write_text_file(summary_path(user_id, slug, root=root), str(summary or ""))


def read_summary(user_id, slug, *, root=None):
    try:
        return scheduling.read_text_file(summary_path(user_id, slug, root=root))
    except OSError:
        return ""


def write_state(user_id, slug, state, *, root=None):
    if not isinstance(state, dict):
        raise ValueError("State muss ein Objekt sein")
    _write_json(state_path(user_id, slug, root=root), state)


def read_state(user_id, slug, *, root=None):
    value = _read_json(state_path(user_id, slug, root=root))
    return value if isinstance(value, dict) else {}


def write_run(user_id, slug, run_date, run, *, root=None):
    if not isinstance(run, dict):
        raise ValueError("Lauf muss ein Objekt sein")
    payload = dict(run)
    payload.setdefault("run_date", _run_date(run_date))
    _write_json(run_path(user_id, slug, run_date, root=root), payload)


def read_run(user_id, slug, run_date, *, root=None):
    value = _read_json(run_path(user_id, slug, run_date, root=root))
    return value if isinstance(value, dict) else None


def list_runs(user_id, slug, *, root=None):
    """Alle exportierten Läufe, älteste zuerst. Kaputte Dateien werden übersprungen."""
    directory = runs_dir(user_id, slug, root=root)
    items = []
    if not directory.is_dir():
        return items
    for path in sorted(directory.glob("*.json")):
        value = _read_json(path)
        if isinstance(value, dict):
            items.append(value)
    return items


def write_notifications(user_id, slug, items, *, root=None):
    if not isinstance(items, list):
        raise ValueError("Benachrichtigungen müssen eine Liste sein")
    _write_json(notifications_path(user_id, slug, root=root), list(items))


def read_notifications(user_id, slug, *, root=None):
    value = _read_json(notifications_path(user_id, slug, root=root))
    return value if isinstance(value, list) else []


def export_snapshot(user_id, slug, job, summary, state, runs, notifications, *, root=None):
    """Den vollständigen Datei-Spiegel eines Jobs schreiben.

    Wird nach jeder DB-Aktualisierung aufgerufen. ``runs`` ist eine Liste von
    Lauf-Dicts mit ``run_date``; ``notifications`` eine Liste von Dicts.
    """
    write_job(user_id, slug, job, root=root)
    write_summary(user_id, slug, summary, root=root)
    write_state(user_id, slug, state, root=root)
    for run in runs or []:
        run_date = run.get("run_date")
        if not run_date:
            continue
        write_run(user_id, slug, run_date, run, root=root)
    write_notifications(user_id, slug, notifications or [], root=root)


__all__ = [
    "ANALYSIS_DIRNAME",
    "RUNS_DIRNAME",
    "slugify",
    "user_root",
    "job_root",
    "job_json_path",
    "summary_path",
    "state_path",
    "runs_dir",
    "run_path",
    "notifications_path",
    "write_job",
    "read_job",
    "write_summary",
    "read_summary",
    "write_state",
    "read_state",
    "write_run",
    "read_run",
    "list_runs",
    "write_notifications",
    "read_notifications",
    "export_snapshot",
]