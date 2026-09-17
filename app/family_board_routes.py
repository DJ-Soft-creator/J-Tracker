"""Familie-Tab board, pin and completion HTTP routes.

AI maintenance contract:
Own the endpoints the reworked Familie tab needs: the aggregated board, the
per-user pin order, project completion and the shopping-list picker. Keep
project CRUD in family_routes.py, content/editor endpoints in
family_content_routes.py, and persistence in family_documents.py.

Endpoint paths keep the historic ``/api/family/project*`` vocabulary on
purpose. The UI calls these documents Buckets; renaming the routes would break
both app shells, the journal templates, the planner and the Brain index at
once (see docs/project/Anforderung-Familie-Tab-Neuaufbau.md, section 2).
"""

import sys

_family = sys.modules.get("family")
if _family is None:  # pragma: no cover - enforced by family.py import order
    raise RuntimeError("family facade must exist before route modules are imported")
globals().update({name: getattr(_family, name) for name in dir(_family) if not name.startswith("__")})
_DYNAMIC_NAMES = (
    '_ensure_dirs', '_current_user',
)


def _sync_runtime():
    for name in _DYNAMIC_NAMES:
        if hasattr(_family, name):
            globals()[name] = getattr(_family, name)


@family_bp.before_request
def _sync_family_board_runtime():
    _sync_runtime()


@family_bp.route("/board", methods=["GET"])
def family_board():
    """Aufgaben, Projekte und Notizen inklusive der Sammel-Buckets."""
    _ensure_dirs()
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        scheduler_status = _run_due_scheduler()
    except OSError as exc:
        logger.error("Scheduler run failed while loading the family board: %s", exc)
        scheduler_status = {"added": 0, "error": "Scheduler konnte nicht ausgeführt werden"}
    _archive_overdue_completed()
    try:
        board = build_board(user)
    except ValueError as exc:
        logger.error("Family board unavailable for %s: %s", user["id"], exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({**board, "scheduler": scheduler_status})


@family_bp.route("/pins", methods=["PUT"])
def family_pins_put():
    """Reihenfolge der angehefteten Einträge eines Subtabs speichern."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    data = request.get_json(silent=True) or {}
    try:
        pins = save_pins(user, str(data.get("scope") or ""), data.get("ids"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "ids": pins})


@family_bp.route("/project/<project_id>/complete", methods=["POST"])
def family_project_complete(project_id):
    """Ein Projekt abschließen: raus aus der Liste, ab ins Archiv."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    try:
        project = complete_project(project_id, user)
    except LookupError:
        return jsonify({"error": "Not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        logger.error("Failed to archive project %s: %s", project_id, exc)
        return jsonify({"error": "Projekt konnte nicht archiviert werden"}), 500
    return jsonify({"ok": True, "archived_at": project.get("archived_at")})


@family_bp.route("/project/<project_id>/task", methods=["POST"])
def family_project_task_create(project_id):
    """Eine einzelne Aufgabe in ein gewähltes Dokument einhängen.

    Gegenstück zum Editor, der immer den ganzen Aufgabenblock ersetzt: hier
    wird genau ein Eintrag angehängt, damit „+ Neu → Aufgabe“ nichts
    überschreiben kann.
    """
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    data = request.get_json(silent=True) or {}

    assignee = str(data.get("user") or "").strip()
    if assignee and assignee not in {item.get("id") for item in _main_module().get_all_users()}:
        return jsonify({"error": "Verantwortliche Person ist unbekannt"}), 400

    target_date = str(data.get("target_date") or "").strip()
    if target_date:
        try:
            if date.fromisoformat(target_date).isoformat() != target_date:
                raise ValueError
        except ValueError:
            return jsonify({"error": "Fälligkeit muss das Format JJJJ-MM-TT haben"}), 400

    try:
        task = append_task_to_project(
            project_id=project_id,
            title=data.get("title"),
            user_uid=assignee,
            created_by=user["id"],
            target_date=target_date,
        )
    except LookupError:
        return jsonify({"error": "Not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        logger.error("Failed to append task to %s: %s", project_id, exc)
        return jsonify({"error": "Aufgabe konnte nicht gespeichert werden"}), 500
    return jsonify({"ok": True, "task_id": task.get("id")}), 201


@family_bp.route("/shopping-lists", methods=["GET"])
def family_shopping_lists():
    """Sichtbare Einkaufslisten für die Zielauswahl im Schreiben-Tab."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    items = [
        {"id": project.get("id"), "title": project.get("title") or project.get("id")}
        for project in shopping_lists(user["id"])
    ]
    return jsonify({"items": items})


__all__ = [
    'family_board', 'family_pins_put', 'family_project_complete',
    'family_project_task_create', 'family_shopping_lists',
]
