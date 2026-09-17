"""AI-Worker-Bereich: eine Stelle, an der Jobs, Anbieter und Tickets sichtbar sind.

Bis hierher war der KI-Betrieb von Journl nur indirekt beobachtbar — ein
Jobstatus liess sich einzeln ueber ``/api/write-ai/jobs/<id>`` abfragen, die
Anbieter standen ausschliesslich in ``config.json``, und die Verbindungen der
Agent-Projekte lebten getrennt davon. Dieses Modul liest beides **nur lesend**
zusammen und beantwortet damit die Frage "was laeuft hier eigentlich?".

Seit 4.7.14 ist die Anbieterverwaltung hier zuhause (Entscheidung E-17): die
``ai_providers`` aus der config.json lassen sich anlegen, aendern, loeschen und
einer davon zum Standard-Agenten machen. Validierung, Geheimnisbehandlung und
der Schreibpfad liegen dabei in ``ai_providers.py`` — dieses Modul macht nur
die HTTP-Huelle und das Admin-Gate. Die Verbindungen der Agent-Projekte bleiben
eine zweite, getrennt gepflegte Welt und werden weiterhin nur angezeigt.

Ausdruecklich nicht Aufgabe dieses Moduls: Jobs starten oder abbrechen. Dafuer
ist der Bereich weiterhin nur der Rahmen (Entscheidung E-18).
"""

import json
import os
import re
import sys
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

import agent_projects
import ai_providers


worker_bp = Blueprint("agent_workbench", __name__, url_prefix="/api/worker")

JOB_LIST_LIMIT = 200
OUTPUT_EXCERPT = 4000
# Welche Anbieterfelder den Server verlassen duerfen, entscheidet
# ai_providers.public_view — dort liegt auch die Regel, dass ein Schluessel
# niemals mitfliegt.
JOB_FIELDS = (
    "id", "status", "agent", "model", "workflow_tag", "context_type",
    "created_at", "started_at", "completed_at", "error", "exit_code", "session_id",
)


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _admin_user():
    main = _main()
    user = main._current_user() if main else None
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if user.get("admin") is not True:
        return None, (jsonify({"error": "Forbidden"}), 403)
    return user, None


def _now_iso():
    main = _main()
    now = main.get_tz_aware_now()[0] if main else datetime.now().astimezone()
    return now.isoformat(timespec="seconds")


def _read_json(path):
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _jobs_dir(user_id):
    return _main().DATA_DIR / user_id / "ai_jobs"


def _public_job(job):
    item = {field: job.get(field) for field in JOB_FIELDS}
    item["has_output"] = bool(job.get("stdout") or job.get("stderr"))
    return item


@worker_bp.route("/jobs", methods=["GET"])
def list_jobs():
    """Die tatsaechlich gelaufenen KI-Jobs, neueste zuerst."""
    user, error = _admin_user()
    if error:
        return error
    directory = _jobs_dir(user["id"])
    items = []
    unreadable = 0
    if directory.is_dir():
        for path in directory.glob("*.json"):
            if path.name.endswith(".lock.json"):
                continue
            job = _read_json(path)
            if job is None:
                unreadable += 1
                continue
            if job.get("id"):
                items.append(_public_job(job))
    items.sort(key=lambda entry: entry.get("created_at") or "", reverse=True)

    counts = {}
    for item in items:
        status = item.get("status") or "unbekannt"
        counts[status] = counts.get(status, 0) + 1

    try:
        limit = min(int(request.args.get("limit", 50)), JOB_LIST_LIMIT)
    except (TypeError, ValueError):
        limit = 50
    response = jsonify({
        "items": items[:max(limit, 1)],
        "total": len(items),
        "counts": counts,
        "unreadable": unreadable,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@worker_bp.route("/jobs/<job_id>", methods=["GET"])
def job_detail(job_id):
    """Ausgabe eines einzelnen Laufs — der Grund, warum etwas fehlschlug."""
    user, error = _admin_user()
    if error:
        return error
    directory = _jobs_dir(user["id"])
    path = directory / f"{job_id}.json"
    try:
        path.resolve().relative_to(directory.resolve())
    except (ValueError, OSError):
        return jsonify({"error": "Unbekannter Job"}), 404
    job = _read_json(path)
    if job is None or job.get("id") != job_id:
        return jsonify({"error": "Unbekannter Job"}), 404
    item = _public_job(job)
    item["prompt"] = str(job.get("prompt") or "")[:OUTPUT_EXCERPT]
    item["stdout"] = str(job.get("stdout") or "")[-OUTPUT_EXCERPT:]
    item["stderr"] = str(job.get("stderr") or "")[-OUTPUT_EXCERPT:]
    return jsonify(item)


# Claude Code nutzt die Anmeldung des Host-Benutzers; ein Endpunkt ist dort
# gegenstandslos, die Modellwahl aber sehr wohl wirksam.
# Paare aus Modellkennung und Beschriftung; die Reihenfolge ist die Empfehlung.
# Opus stand hier vorn und wurde dadurch zur Gewohnheit — bei gleicher Aufgabe
# kostet es ein Vielfaches von Sonnet. Eine Voreinstellung gibt es weiterhin
# bewusst nicht (siehe tickets.py), die Reihenfolge ist nur ein Hinweis.
CLAUDE_MODELS = (
    ("claude-sonnet-5", "Sonnet 5 · Standard für umgrenzte Tickets"),
    ("claude-opus-5", "Opus 5 · Entwurf und schwierige Urteile"),
    ("claude-haiku-4-5", "Haiku 4.5 · mechanische Kleinarbeit"),
)
HOST_TARGETS_FILE = "host_targets.json"


def _host_targets():
    """Was der Host-Worker zuletzt gemeldet hat (Werkzeuge und pi-Modelle)."""
    value = _read_json(_main().DATA_DIR / HOST_TARGETS_FILE)
    return value if isinstance(value, dict) else {}


@worker_bp.route("/targets", methods=["GET"])
def list_targets():
    """Ausfuehrungsziele: Ort und Werkzeug gehoeren zusammen, Modelle daran.

    Getrennte Dropdowns fuer Endpunkt und Werkzeug erlaubten Kombinationen, die
    es nicht gibt (etwa Claude Code auf einem LM-Studio-Endpunkt). Ein Ziel
    buendelt beides und bringt seine eigene Modell-Liste mit.
    """
    user, error = _admin_user()
    if error:
        return error
    host = _host_targets()
    tools = host.get("tools") or {}
    targets = []

    if (tools.get("claude") or {}).get("available") is not False:
        targets.append({
            "id": "host-claude",
            "label": "Dev-Server · Claude Code",
            "tool": "claude",
            "models": [list(entry) for entry in CLAUDE_MODELS],
            "supports_effort": False,
            "note": "Nutzt die Anmeldung des Host-Benutzers; kein Endpunkt nötig.",
        })

    pi_models = (tools.get("pi") or {}).get("models") or []
    if pi_models:
        targets.append({
            "id": "host-pi",
            "label": "Dev-Server · pi",
            "tool": "pi",
            "models": [item.get("id") for item in pi_models if isinstance(item, dict) and item.get("id")],
            # pi haengt die Denktiefe als ":<thinking>" an den Modellnamen.
            "supports_effort": any(item.get("thinking") for item in pi_models if isinstance(item, dict)),
            "note": "Modelle aus der models.json des Host-Benutzers.",
        })

    try:
        store = agent_projects.load_store(user["id"])
        connections = store.get("connections", []) or []
    except Exception:
        connections = []
    for connection in connections:
        if not isinstance(connection, dict) or not connection.get("id"):
            continue
        runner = connection.get("runner") or "opencode"
        models = [connection["model"]] if connection.get("model") else []
        targets.append({
            "id": f"connection:{connection['id']}",
            "label": f"{connection.get('label') or connection['id']} · {runner}",
            "tool": runner,
            "models": models,
            "supports_effort": False,
            "free_model": not models,
            "note": connection.get("base_url") or "",
        })

    response = jsonify({"targets": targets, "updated_at": host.get("updated_at") or ""})
    response.headers["Cache-Control"] = "no-store"
    return response


QUESTION_TYPES = ("boolean", "choice", "text")
MAX_ANSWER_LENGTH = 2000
_SESSION_RE = re.compile(r"^[0-9a-f]{32}$")


def _question_dir(user_id):
    return _main().DATA_DIR / str(user_id) / "tickets"


def _write_json(path, payload):
    """Atomar schreiben — der Host-Worker liest dieselbe Datei (E-08)."""
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


def _public_session(item):
    questions = []
    for entry in item.get("questions") or []:
        if not isinstance(entry, dict):
            continue
        questions.append({
            "id": entry.get("id"),
            "type": entry.get("type") if entry.get("type") in QUESTION_TYPES else "text",
            "question": entry.get("question") or "",
            "options": [str(value) for value in (entry.get("options") or [])][:8],
            "answer": entry.get("answer"),
        })
    return {
        "id": item.get("id"),
        "ticket_id": item.get("ticket_id"),
        "ticket_title": item.get("ticket_title") or "",
        "status": item.get("status") or "offen",
        "created_at": item.get("created_at") or "",
        "questions": questions,
        "open_count": sum(1 for entry in questions if entry["answer"] in (None, "")),
    }


def _load_sessions(user_id):
    directory = _question_dir(user_id)
    if not directory.is_dir():
        return []
    sessions = []
    for path in sorted(directory.glob("*.json")):
        item = _read_json(path)
        if item and item.get("questions"):
            sessions.append((path, item))
    return sessions


@worker_bp.route("/questions", methods=["GET"])
def list_questions():
    """Offene und beantwortete Rückfragen eines Nutzers."""
    user, error = _admin_user()
    if error:
        return error
    items = [_public_session(item) for _, item in _load_sessions(user["id"])]
    items.sort(key=lambda entry: (entry["status"] != "offen", entry["created_at"]), reverse=False)
    response = jsonify({
        "items": items,
        "waiting": sum(1 for entry in items if entry["status"] == "offen"),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@worker_bp.route("/assistant/scan", methods=["POST"])
def assistant_scan():
    """Produktfragmente eines Zeitraums zu Vorhaben gruppieren (Etappe 5).

    Der Lauf **schlägt nur vor** — er legt weder Mappen noch Tickets an. Was
    daraus wird, entscheidet der Product Owner.
    """
    user, error = _admin_user()
    if error:
        return error
    csrf = _main().csrf_protect(lambda: None)()
    if csrf is not None:
        return csrf
    data = request.get_json(silent=True) or {}
    start = str(data.get("start_date") or "")
    end = str(data.get("end_date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        return jsonify({"error": "Bitte einen Zeitraum als Datum angeben"}), 400
    if start > end:
        return jsonify({"error": "Der Zeitraum ist verdreht"}), 400

    main = _main()
    config = main.load_config() or {}
    provider = next((item for item in config.get("ai_providers", []) or []
                     if item.get("id") == data.get("provider_id")), None)
    if not provider:
        return jsonify({"error": "Bitte einen konfigurierten Anbieter wählen"}), 400

    import ticket_assistant
    try:
        report = ticket_assistant.run_scan(user["id"], start, end, provider, main._call_ai_api)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:  # noqa: BLE001
        return jsonify({"error": "Der Anbieter war nicht erreichbar"}), 502
    return jsonify(report)


@worker_bp.route("/notifications", methods=["GET"])
def notifications():
    """Zaehler fuer die Hinweislinie im Schreiben-Tab (E-25).

    Gemeldet wird alles, was auf eine Entscheidung wartet: offene Rueckfragen,
    prueffertige Ergebnisse und gescheiterte Laeufe.
    """
    user, error = _admin_user()
    if error:
        return jsonify({"questions": 0, "review": 0, "failed": 0})
    questions = sum(1 for _, item in _load_sessions(user["id"])
                    if item.get("status") == "offen")
    review = failed = 0
    try:
        import tickets as tickets_module
        directory = tickets_module.tickets_dir()
        if directory:
            for path in directory.glob("*.json"):
                item = _read_json(path)
                if not item:
                    continue
                if item.get("state") == "pruefung":
                    review += 1
                elif item.get("state") == "fehlgeschlagen":
                    failed += 1
    except Exception:  # noqa: BLE001
        pass
    response = jsonify({"questions": questions, "review": review, "failed": failed})
    response.headers["Cache-Control"] = "no-store"
    return response


@worker_bp.route("/questions/<session_id>/answer", methods=["POST"])
def answer_question(session_id):
    """Eine Frage beantworten; sind alle beantwortet, läuft das Ticket weiter."""
    user, error = _admin_user()
    if error:
        return error
    csrf = _main().csrf_protect(lambda: None)()
    if csrf is not None:
        return csrf
    if not _SESSION_RE.match(session_id or ""):
        return jsonify({"error": "Unbekannte Rückfrage"}), 404
    path = _question_dir(user["id"]) / f"{session_id}.json"
    item = _read_json(path)
    if item is None:
        return jsonify({"error": "Unbekannte Rückfrage"}), 404
    if item.get("status") != "offen":
        return jsonify({"error": "Diese Rückfrage ist bereits beantwortet"}), 409

    data = request.get_json(silent=True) or {}
    question_id = data.get("question_id")
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return jsonify({"error": "Bitte eine Antwort angeben"}), 400
    answer = answer.strip()[:MAX_ANSWER_LENGTH]

    target = next((entry for entry in item.get("questions") or []
                   if isinstance(entry, dict) and entry.get("id") == question_id), None)
    if target is None:
        return jsonify({"error": "Unbekannte Frage"}), 404
    if target.get("type") == "choice" and answer not in (target.get("options") or []):
        return jsonify({"error": "Diese Auswahl gibt es nicht"}), 400
    if target.get("type") == "boolean" and answer not in ("ja", "nein"):
        return jsonify({"error": "Bitte ja oder nein"}), 400

    target["answer"] = answer
    target["answered_at"] = _now_iso()
    item["updated_at"] = _now_iso()
    remaining = sum(1 for entry in item.get("questions") or []
                    if isinstance(entry, dict) and entry.get("answer") in (None, ""))
    if not remaining:
        # Erst wenn nichts mehr offen ist, greift der Host-Worker das Ticket
        # wieder auf und setzt die Sitzung fort.
        item["status"] = "beantwortet"
    _write_json(path, item)
    return jsonify({"ok": True, "remaining": remaining, "status": item["status"]})


@worker_bp.route("/providers", methods=["GET"])
def list_providers():
    """Beide Anbieter-Welten nebeneinander — nur eine davon ist hier pflegbar.

    Die ``ai_providers`` aus der config.json kommen roh (mit ``{locale_LLM_IP}``,
    ohne Schluessel), weil die Verwaltung genau diese Rohwerte zurueckschreibt.
    Was ``load_config()`` daraus macht, steht daneben in ``resolved`` — sonst
    sieht niemand, worauf ein Platzhalter gerade zeigt.
    """
    user, error = _admin_user()
    if error:
        return error
    main = _main()
    config = main.load_config() or {}

    try:
        view = ai_providers.public_view(main.CONFIG_PATH, os.environ)
    except ai_providers.ProviderError as exc:
        return jsonify({"error": str(exc)}), 500

    resolved = {}
    for provider in config.get("ai_providers", []) or []:
        if isinstance(provider, dict) and provider.get("id"):
            resolved[str(provider["id"])] = str(provider.get("api_url") or "")

    functions = [
        {"id": item.get("id"), "label": item.get("label") or item.get("name")}
        for item in (config.get("ai_functions") or []) if isinstance(item, dict)
    ]

    connections = []
    try:
        store = agent_projects.load_store(user["id"])
        for connection in store.get("connections", []) or []:
            if not isinstance(connection, dict):
                continue
            connections.append({
                "id": connection.get("id"),
                "label": connection.get("label"),
                "kind": connection.get("kind"),
                "model": connection.get("model"),
                "base_url": connection.get("base_url"),
            })
    except Exception:
        # Die Anbieter-Uebersicht darf nicht daran scheitern, dass ein
        # Agent-Projekt-Store fehlt oder beschaedigt ist.
        connections = []

    return jsonify({
        "shared": view["providers"],
        "default_id": view["default_id"],
        "chat_provider_id": ai_providers.CHAT_PROVIDER_ID,
        "resolved": resolved,
        "functions": functions,
        "connections": connections,
        "note": "Die Anbieter unten sind hier pflegbar. Die Verbindungen der "
                "Agent-Projekte bleiben eine getrennte Welt und werden nur angezeigt.",
    })


@worker_bp.route("/providers", methods=["PUT"])
def save_providers():
    """Die ganze Anbieterliste und den Standard-Agenten ersetzen.

    Bewusst die ganze Liste statt einzelner Eintraege: Reihenfolge und
    Eindeutigkeit der IDs sind Eigenschaften der Liste, nicht eines Eintrags,
    und ein Teil-Update muesste sie trotzdem jedes Mal komplett pruefen.
    """
    _user, error = _admin_user()
    if error:
        return error
    csrf = _main().csrf_protect(lambda: None)()
    if csrf is not None:
        return csrf

    data = request.get_json(silent=True) or {}
    main = _main()
    try:
        previous = ai_providers.load_providers(main.CONFIG_PATH)
        providers, default_id = ai_providers.validate(
            data.get("providers"), data.get("default_id"), previous)
        ai_providers.save(main.CONFIG_PATH, providers, default_id)
    except ai_providers.ProviderError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"config.json liess sich nicht schreiben: {exc}"}), 500

    view = ai_providers.public_view(main.CONFIG_PATH, os.environ)
    return jsonify({"ok": True, "providers": view["providers"], "default_id": view["default_id"]})
