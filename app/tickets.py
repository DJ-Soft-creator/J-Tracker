"""Tickets, die aus dem Schreiben-Tab in das Entwicklungs-Repository wandern.

Ein Ticket besteht aus zwei Dateien nebeneinander im Eingangsordner:

    <id>.md    der eingereichte Inhalt, ausschliesslich vom Product Owner
    <id>.json  Zustand und Verarbeitungsjournal (Sidecar)

Die Trennung ist Absicht und in ``docs/project/Anforderung-Ticket-Assistent.md``
Abschnitt 5a festgehalten: Ein spaeterer Job darf nie in seine eigene
Eingabedatei schreiben, sonst loest ein Lauf den naechsten aus. Aus demselben
Grund macht die blosse Existenz einer Datei ein Ticket **nicht** zum Auftrag —
nur der Zustand ``freigegeben`` tut das, und den setzt der Product Owner hier.

Der Eingangsordner liegt ausserhalb von ``DATA_DIR`` und wird per Bind-Mount
bereitgestellt (``TICKETS_DIR``). Fehlt er, sind alle Endpunkte 404 — die App
laeuft ohne das Feature unveraendert weiter.
"""

import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

import write_sessions


tickets_bp = Blueprint("tickets", __name__, url_prefix="/api/tickets")

SCHEMA_VERSION = 1
MAX_TICKET_BYTES = 256 * 1024
MAX_TITLE_LENGTH = 120
_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")

# Zustaende laut Verarbeitungsvertrag. Nur `freigegeben` ist Auftrag.
STATES = ("neu", "freigegeben", "in_arbeit", "rueckfrage", "pruefung",
          "fehlgeschlagen", "erledigt", "verworfen")
# Was die Oberflaeche setzen darf. `in_arbeit`, `rueckfrage` und `pruefung`
# vergibt die Umsetzungsseite, nicht der Browser. `fehlgeschlagen` bleibt
# liegen, bis der Product Owner es bewusst wieder freigibt (E-26).
SETTABLE_STATES = ("neu", "freigegeben", "erledigt", "verworfen")
# Zustaende, aus denen der Browser nichts umstellen darf, weil ein Lauf sie
# besitzt.
LOCKED_STATES = ("in_arbeit",)

# Agent-Konfiguration, die bei der Freigabe gewaehlt wird (E-16, E-21, E-28).
# Bewusst keine Voreinstellung: es soll nie versehentlich das falsche Modell
# laufen.
RUN_MODES = ("read", "write")
CONFIG_FIELDS = ("endpoint", "tool", "model", "effort", "mode")
MAX_CONFIG_VALUE = 120


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _now():
    main = _main()
    return main.get_tz_aware_now()[0] if main else datetime.now().astimezone()


def _iso(value):
    return value.isoformat(timespec="seconds")


def tickets_dir():
    """Eingangsordner oder None, wenn das Feature nicht bereitsteht."""
    configured = os.environ.get("TICKETS_DIR", "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_dir() or path.is_symlink():
        return None
    return path


def _disabled():
    return jsonify({"error": "Ticket-Ablage ist nicht eingerichtet"}), 404


def _admin_user():
    """Nur angemeldete Admins duerfen Tickets sehen oder einreichen."""
    main = _main()
    user = main._current_user() if main else None
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if user.get("admin") is not True:
        return None, (jsonify({"error": "Forbidden"}), 403)
    return user, None


def _csrf_error():
    return _main().csrf_protect(lambda: None)()


def _atomic_text(path, text):
    """Temporaere Datei, fsync, dann os.replace — wie im uebrigen Bestand.

    Ohne das lesen Oberflaeche und spaetere Jobs halbfertige Dateien; ein
    API-Endpunkt, der das abfangen wuerde, existiert bewusst nicht (E-08).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o660)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_sidecar(path, payload):
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_sidecar(path):
    """Defensiv lesen: eine kaputte Datei darf die Liste nicht kippen."""
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _slug(title):
    cleaned = re.sub(r"[^0-9A-Za-zäöüÄÖÜß -]+", "", title or "").strip()
    cleaned = re.sub(r"[\s-]+", "-", cleaned).strip("-")
    return cleaned[:60].lower()


def _new_id(now):
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _ticket_paths(directory, ticket_id):
    return directory / f"{ticket_id}.md", directory / f"{ticket_id}.json"


def _validated_id(value):
    return value if isinstance(value, str) and _ID_RE.match(value) else None


def _clean_config(raw):
    """Die bei der Freigabe gewaehlte Agent-Konfiguration pruefen.

    Gibt (konfiguration, fehler) zurueck. Endpunkt, Werkzeug, Modell und Modus
    sind Pflicht — ohne sie liefe ein Ticket mit unbestimmtem Agenten (E-28).
    Der Effort ist optional, weil ihn nicht jedes Werkzeug kennt.
    """
    if not isinstance(raw, dict):
        return None, "Bitte Endpunkt, Werkzeug, Modell und Rechte wählen"
    config = {}
    for field in CONFIG_FIELDS:
        value = raw.get(field)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > MAX_CONFIG_VALUE:
            return None, f"Ungültige Angabe für {field}"
        config[field] = value.strip()
    for field in ("endpoint", "tool", "model", "mode"):
        if not config.get(field):
            return None, "Bitte Endpunkt, Werkzeug, Modell und Rechte wählen"
    if config["mode"] not in RUN_MODES:
        return None, "Unbekannte Rechte-Wahl"
    return config, None


def _attachment_name(index, item):
    """Lesbarer, harmloser Dateiname im Ticketordner."""
    original = item.get("original_name") or ""
    suffix = Path(original).suffix.lower()
    if not re.fullmatch(r"\.[0-9a-z]{1,8}", suffix or ""):
        suffix = ""
    base = item.get("original_filename") or item.get("title") or item.get("type") or "anhang"
    base = re.sub(r"[^0-9A-Za-z]+", "-", Path(str(base)).stem).strip("-").lower() or "anhang"
    return f"{index:02d}-{base[:40]}{suffix}"


def _attachment_markdown(entries):
    if not entries:
        return ""
    lines = ["", "## Anhänge", ""]
    for entry in entries:
        link = f"{entry['directory']}/{entry['name']}"
        prefix = "!" if entry["type"] == "image" else ""
        lines.append(f"- {prefix}[{entry['name']}]({link})")
    return "\n".join(lines) + "\n"


def _take_session_media(user, session_id, directory, ticket_id):
    """Medien der Schreibsession in den Ticketordner uebernehmen.

    Gibt (eintraege, fehler) zurueck. Die Session wird nur dann abgeschlossen,
    wenn alle Dateien liegen — sonst bliebe ein Anhang im Nichts.
    """
    user_id = user.get("id")
    session, session_dir = write_sessions._load_session(user_id, session_id)
    if not session or session_dir is None:
        return [], "Unbekannte Schreibsession"
    if session.get("status") != "active" or write_sessions._is_expired(session):
        return [], "Die Schreibsession ist nicht mehr aktiv"

    items = write_sessions._media_items(session_dir)
    pending = [item for item in items if item.get("status") != "ready"]
    if pending:
        return [], "Ein Anhang wird noch verarbeitet"

    target_dir = directory / ticket_id
    entries = []
    try:
        for index, item in enumerate(items, start=1):
            original = item.get("original_name")
            if not original:
                continue
            source = session_dir / "media" / str(item.get("id")) / original
            if not source.is_file() or source.is_symlink():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            name = _attachment_name(index, item)
            shutil.copy2(source, target_dir / name)
            os.chmod(target_dir / name, 0o660)
            entries.append({"name": name, "type": item.get("type") or "file", "directory": ticket_id})
    except OSError:
        shutil.rmtree(target_dir, ignore_errors=True)
        return [], "Anhänge konnten nicht übernommen werden"

    # Erst jetzt ist die Uebernahme vollstaendig: Session schliessen, die
    # Inbox-Reservierung aufloesen und die Session-Medien entfernen — genau die
    # Reihenfolge, die der normale Journal-Weg ebenfalls einhaelt.
    for item in items:
        write_sessions._consume_inbox_attachment(user_id, item)
    session.update(
        status="submitted",
        submitted_at=write_sessions._iso(write_sessions._now()),
        updated_at=write_sessions._iso(write_sessions._now()),
        submission_target=f"ticket:{ticket_id}",
    )
    write_sessions._write_json(session_dir / "session.json", session)
    shutil.rmtree(session_dir / "media", ignore_errors=True)
    return entries, None


def _public(item, content_present):
    return {
        "id": item.get("id"),
        "title": item.get("title") or "(ohne Titel)",
        "state": item.get("state") if item.get("state") in STATES else "neu",
        "created_at": item.get("created_at") or "",
        "created_by_name": item.get("created_by_name") or "",
        "updated_at": item.get("updated_at") or "",
        "slug": item.get("slug") or "",
        "processed": bool(item.get("processing")),
        "attachments": len(item.get("attachments") or []),
        "config": item.get("config") if isinstance(item.get("config"), dict) else None,
        "last_error": str(item.get("last_error") or "")[:300],
        "runs": len(item.get("processing") or []),
        "requested_action": item.get("requested_action") if item.get("requested_action") in REVIEW_ACTIONS else None,
        "files": ((item.get("processing") or [{}])[-1] or {}).get("files") or [],
        "stale": bool(item.get("stale")),
        "content_missing": not content_present,
    }


@tickets_bp.route("/config", methods=["GET"])
def ticket_config():
    """Sagt der Oberflaeche, ob der Eintrag ueberhaupt angeboten werden soll."""
    main = _main()
    user = main._current_user() if main else None
    return jsonify({
        "available": bool(tickets_dir()) and bool(user) and user.get("admin") is True,
    })


@tickets_bp.route("", methods=["GET"])
def list_tickets():
    directory = tickets_dir()
    if not directory:
        return _disabled()
    _, error = _admin_user()
    if error:
        return error
    items = []
    broken = 0
    for sidecar in sorted(directory.glob("*.json")):
        ticket_id = _validated_id(sidecar.stem)
        if not ticket_id:
            continue
        item = _read_sidecar(sidecar)
        if item is None:
            broken += 1
            continue
        markdown, _ = _ticket_paths(directory, ticket_id)
        items.append(_public(item, markdown.is_file()))
    items.sort(key=lambda entry: entry.get("created_at") or "", reverse=True)
    response = jsonify({"items": items, "states": list(SETTABLE_STATES), "broken": broken})
    response.headers["Cache-Control"] = "no-store"
    return response


@tickets_bp.route("", methods=["POST"])
def create_ticket():
    directory = tickets_dir()
    if not directory:
        return _disabled()
    user, error = _admin_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf is not None:
        return csrf
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        return jsonify({"error": "Kein Inhalt"}), 400
    if len(content.encode("utf-8")) > MAX_TICKET_BYTES:
        return jsonify({"error": "Ticket ist zu gross"}), 413
    title = (data.get("title") or "").strip()
    if not title:
        title = content.strip().splitlines()[0][:MAX_TITLE_LENGTH].strip()
    title = title[:MAX_TITLE_LENGTH]

    now = _now()
    ticket_id = _new_id(now)
    markdown_path, sidecar_path = _ticket_paths(directory, ticket_id)

    attachments = []
    session_id = data.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        attachments, error = _take_session_media(user, session_id.strip(), directory, ticket_id)
        if error:
            return jsonify({"error": error}), 409

    body = (
        f"# {title}\n\n"
        f"Eingereicht: {_iso(now)}\n"
        f"Von: {user.get('username') or user.get('id')}\n\n"
        f"---\n\n"
        f"{content.strip()}\n"
        f"{_attachment_markdown(attachments)}"
    )
    _atomic_text(markdown_path, body)
    _write_sidecar(sidecar_path, {
        "attachments": [entry["name"] for entry in attachments],
        "schema": SCHEMA_VERSION,
        "id": ticket_id,
        "title": title,
        "slug": _slug(title),
        "state": "neu",
        "source": "write-tab",
        "created_at": _iso(now),
        "created_by": user.get("id"),
        "created_by_name": user.get("username") or "",
        "updated_at": _iso(now),
        "processing": [],
    })
    return jsonify({"ok": True, "id": ticket_id, "title": title,
                    "attachments": len(attachments)}), 201


# Begleitdateien eines Laufs. Beide sind reiner Text und werden nur gelesen.
SIDE_FILES = {
    "protokoll": ("{ticket}.protokoll.md", 400 * 1024),
    "bericht": ("{ticket}.bericht.md", 80 * 1024),
    "diff": ("{ticket}.diff", 400 * 1024),
}
REVIEW_ACTIONS = ("merge", "discard")


@tickets_bp.route("/<ticket_id>/<kind>", methods=["GET"])
def ticket_side_file(ticket_id, kind):
    """Laufprotokoll oder Bericht eines Tickets im Browser lesbar machen.

    Das Protokoll ist bewusst ausfuehrlich (Prompt, Rohausgabe des Agenten), um
    am Anfang beurteilen zu koennen, ob ein Lauf tut was erwartet wird.
    """
    if kind not in SIDE_FILES:
        return jsonify({"error": "Unbekannte Datei"}), 404
    directory = tickets_dir()
    if not directory:
        return _disabled()
    _, error = _admin_user()
    if error:
        return error
    valid_id = _validated_id(ticket_id)
    if not valid_id:
        return jsonify({"error": "Unbekanntes Ticket"}), 404
    pattern, limit = SIDE_FILES[kind]
    path = directory / pattern.format(ticket=valid_id)
    if not path.is_file() or path.is_symlink():
        return jsonify({"error": "Noch nichts vorhanden"}), 404
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return jsonify({"error": "Datei nicht lesbar"}), 404
    truncated = len(text) > limit
    response = jsonify({
        # Das Ende ist bei einem Protokoll die interessante Seite.
        "text": text[-limit:] if truncated else text,
        "truncated": truncated,
        "bytes": path.stat().st_size,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@tickets_bp.route("/<ticket_id>/review", methods=["POST"])
def request_review(ticket_id):
    """Übernehmen oder Verwerfen anfordern (E-22, E-23).

    Ausgeführt wird die git-Arbeit vom Host-Worker: der Container erreicht weder
    git noch das Repository und bekommt bewusst keine Repo-Rechte. Hier wird
    daher nur der Wunsch im Sidecar hinterlegt.
    """
    directory = tickets_dir()
    if not directory:
        return _disabled()
    _, error = _admin_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf is not None:
        return csrf
    valid_id = _validated_id(ticket_id)
    if not valid_id:
        return jsonify({"error": "Unbekanntes Ticket"}), 404
    _, sidecar_path = _ticket_paths(directory, valid_id)
    item = _read_sidecar(sidecar_path)
    if item is None:
        return jsonify({"error": "Unbekanntes Ticket"}), 404
    if item.get("state") != "pruefung":
        return jsonify({"error": "Das Ticket wartet nicht auf eine Entscheidung"}), 409
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in REVIEW_ACTIONS:
        return jsonify({"error": "Unbekannte Entscheidung"}), 400
    item["requested_action"] = action
    item["updated_at"] = _iso(_now())
    _write_sidecar(sidecar_path, item)
    return jsonify({"ok": True, "id": valid_id, "action": action, "queued": True})


@tickets_bp.route("/<ticket_id>/state", methods=["POST"])
def set_ticket_state(ticket_id):
    directory = tickets_dir()
    if not directory:
        return _disabled()
    _, error = _admin_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf is not None:
        return csrf
    valid_id = _validated_id(ticket_id)
    if not valid_id:
        return jsonify({"error": "Unbekanntes Ticket"}), 404
    _, sidecar_path = _ticket_paths(directory, valid_id)
    item = _read_sidecar(sidecar_path)
    if item is None:
        return jsonify({"error": "Unbekanntes Ticket"}), 404
    data = request.get_json(silent=True) or {}
    state = data.get("state")
    if state not in SETTABLE_STATES:
        return jsonify({"error": "Unbekannter Zustand"}), 400
    if item.get("state") in LOCKED_STATES:
        return jsonify({"error": "Ticket wird gerade bearbeitet"}), 409

    if state == "freigegeben":
        # Die Freigabe ist der Moment, in dem das Ticket zum Auftrag wird —
        # deshalb wird hier die Agent-Konfiguration verbindlich gewaehlt.
        config, error = _clean_config(data.get("config"))
        if error:
            return jsonify({"error": error}), 400
        item["config"] = config
        item["released_at"] = _iso(_now())
    item["state"] = state
    item["updated_at"] = _iso(_now())
    item.setdefault("schema", SCHEMA_VERSION)
    _write_sidecar(sidecar_path, item)
    return jsonify({"ok": True, "id": valid_id, "state": state})
