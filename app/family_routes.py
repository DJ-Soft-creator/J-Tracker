"""Family project and planner HTTP routes.

AI maintenance contract:
Own project CRUD, task completion, planner and scheduler endpoints.
Keep parsing, serialization and persistent write primitives in family.py. This
module owns HTTP orchestration only and synchronizes test-replaceable paths and
helpers from the family facade before each request.
"""

import sys

_family = sys.modules.get("family")
if _family is None:  # pragma: no cover - enforced by family.py import order
    raise RuntimeError("family facade must exist before route modules are imported")
globals().update({name: getattr(_family, name) for name in dir(_family) if not name.startswith("__")})
_DYNAMIC_NAMES = ('DATA_DIR', 'FAMILY_DIR', 'PROJECTS_DIR', 'PLANNER_DIR', 'ARCHIVE_DIR', 'PLANNER_FILE', 'FAMILY_TASKS_FILE', '_load_project', '_save_project', '_ensure_dirs', '_current_user', '_get_tz_aware_now')


def _sync_runtime():
    for name in _DYNAMIC_NAMES:
        if hasattr(_family, name):
            globals()[name] = getattr(_family, name)


@family_bp.before_request
def _sync_family_route_runtime():
    _sync_runtime()


@family_bp.route("/projects", methods=["GET"])
def family_projects():
    """Liefert alle fuer den aktuellen User sichtbaren Projekte + Tasks."""
    _ensure_dirs()
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        scheduler_status = _run_due_scheduler()
    except OSError as exc:
        logger.error("Scheduler run failed while loading family projects: %s", exc)
        scheduler_status = {"added": 0, "error": "Scheduler konnte nicht ausgeführt werden"}
    _archive_overdue_completed()
    uid = user["id"]

    result = []
    for path in _all_project_paths():
        proj = parse_project_file(path)
        if not proj:
            continue
        if not _project_visible(proj, uid):
            continue
        result.append(_sanitize_project(proj))

    recurring = parse_recurring_tasks()
    if recurring:
        result.insert(0, _sanitize_project(_recurring_project(recurring)))
    return jsonify({"projects": result, "scheduler": scheduler_status})


@family_bp.route("/project/<project_id>", methods=["GET"])
def family_project_detail(project_id):
    _ensure_dirs()
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    uid = user["id"]
    if project_id == RECURRING_PROJECT_ID:
        try:
            _run_due_scheduler()
        except OSError as exc:
            logger.error("Scheduler run failed while loading recurring tasks: %s", exc)
        return jsonify(_sanitize_project(_recurring_project(parse_recurring_tasks())))
    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_visible(proj, uid):
        return jsonify({"error": "Not found"}), 404
    return jsonify(_sanitize_project(proj))


@family_bp.route("/project", methods=["POST"])
def family_project_create():
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    data = request.get_json(silent=True) or {}
    template_id = (data.get("template_id") or "").strip()
    target_file = data.get("target_file")
    assigned_users = data.get("assigned_users") or []
    try:
        title = family_views.validate_title(data.get("title"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not template_id:
        return jsonify({"error": "title and template_id required"}), 400
    # Nur Administratoren verteilen Sichtbarkeiten (D-011). Wer selbst anlegt,
    # bekommt sonst ein fuer alle sichtbares Dokument.
    if assigned_users and not family_views.can_change_visibility(user):
        return jsonify({"error": "Nur Administratoren dürfen Benutzer zuordnen"}), 403
    exclude_from_personal = data.get("exclude_from_personal", False)
    if not isinstance(exclude_from_personal, bool):
        return jsonify({"error": "exclude_from_personal muss true oder false sein"}), 400
    now, _ = _get_tz_aware_now()
    project = {
        "id": str(uuid.uuid4()),
        "title": title,
        "type": family_views.normalize_type(data.get("type")),
        "exclude_from_personal": exclude_from_personal,
        "template_id": template_id,
        "target_file": target_file or "",
        "assigned_users": assigned_users,
        "created_at": now.isoformat(),
        "created_by": user["id"],
        "tasks": [],
        "comments": [],
    }
    _save_project(project)
    return jsonify(_sanitize_project(project)), 201


@family_bp.route("/project/<project_id>", methods=["PUT"])
def family_project_update(project_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_visible(proj, user["id"]):
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    if "title" in data:
        try:
            proj["title"] = family_views.validate_title(data["title"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "type" in data:
        if family_views.is_personal_bucket(proj):
            return jsonify({"error": "Der persönliche Bucket behält seinen Typ"}), 400
        proj["type"] = family_views.normalize_type(data["type"])
    if "exclude_from_personal" in data:
        if not isinstance(data["exclude_from_personal"], bool):
            return jsonify({"error": "exclude_from_personal muss true oder false sein"}), 400
        proj["exclude_from_personal"] = data["exclude_from_personal"]
    if "template_id" in data:
        proj["template_id"] = data["template_id"]
    if "target_file" in data:
        proj["target_file"] = data["target_file"]
    if "assigned_users" in data:
        if not family_views.can_change_visibility(user):
            return jsonify({"error": "Nur Administratoren dürfen Benutzer zuordnen"}), 403
        if family_views.is_personal_bucket(proj):
            return jsonify({"error": "Der persönliche Bucket bleibt privat"}), 400
        proj["assigned_users"] = data["assigned_users"] or []
    _save_project(proj)
    return jsonify(_sanitize_project(proj))


@family_bp.route("/project/<project_id>", methods=["DELETE"])
def family_project_delete(project_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_visible(proj, user["id"]):
        return jsonify({"error": "Not found"}), 404
    if not family_views.can_delete(proj, user):
        return jsonify({"error": "Löschen darf nur der Ersteller oder ein Administrator"}), 403
    path = _project_path(project_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.error(f"Failed to delete project {project_id}: {e}")
        return jsonify({"error": "Failed"}), 500
    return jsonify({"ok": True})


@family_bp.route("/task/check", methods=["POST"])
def family_task_check():
    """Set a task's completion state. Repeated requests are idempotent."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    task_id = data.get("task_id")
    if not project_id or not task_id:
        return jsonify({"error": "project_id and task_id required"}), 400
    completed = data.get("completed", True)
    if not isinstance(completed, bool):
        return jsonify({"error": "completed (boolean) required"}), 400

    # allow_assignee: eine mir zugewiesene Aufgabe darf ich auch dann abhaken,
    # wenn das Herkunftsdokument nicht fuer mich freigegeben ist. Die Pruefung
    # auf `task.user == ich` liegt in set_task_completion.
    result = set_task_completion(project_id, task_id, user["id"], completed, allow_assignee=True)
    if result.get("forbidden"):
        return jsonify({"error": "Not found"}), 404
    if not result.get("found"):
        return jsonify({"error": "task not found"}), 404
    task = result["task"]
    response = {
        "ok": True,
        "completed": completed,
        "already_completed": result["already_completed"],
        "completed_by": task.get("completed_by"),
        "completed_at": task.get("completed_at"),
    }
    if project_id == RECURRING_PROJECT_ID:
        response["recurring"] = True
    return jsonify(response)


@family_bp.route("/planner", methods=["GET"])
def family_planner_list():
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    _ensure_dirs()
    try:
        scheduler_status = _run_due_scheduler()
    except OSError as exc:
        logger.error("Scheduler run failed while loading planner: %s", exc)
        scheduler_status = {"added": 0, "error": str(exc)}
    now, _ = _get_tz_aware_now()
    items = parse_planner(_read_file(PLANNER_FILE) or "")
    items.sort(key=lambda item: (not item.get("active", True), item.get("title", "").lower()))
    users = [
        {"id": item.get("id"), "display": item.get("username") or item.get("id")}
        for item in _main_module().get_all_users()
    ]
    return jsonify({
        "items": [_planner_api_item(item, now.date()) for item in items],
        "users": users,
        "recurrences": list(RECURRENCE_OPTIONS),
        "today": now.date().isoformat(),
        "scheduler": scheduler_status,
    })


@family_bp.route("/planner", methods=["POST"])
def family_planner_create():
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    data = request.get_json(silent=True) or {}
    try:
        item = _create_recurring_plan(
            title=data.get("title"),
            user_uid=data.get("user"),
            recurrence=data.get("recurrence"),
            start_date=data.get("start_date"),
            active=data.get("active", True),
            created_by=user["id"],
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        logger.error("Failed to create planner item: %s", exc)
        return jsonify({"error": "Plan konnte nicht gespeichert werden"}), 500
    try:
        scheduler_status = _run_due_scheduler()
    except OSError as exc:
        logger.error("Plan saved, but scheduler run failed: %s", exc)
        scheduler_status = {"added": 0, "error": "Plan gespeichert, Scheduler-Prüfung fehlgeschlagen"}
    now, _ = _get_tz_aware_now()
    return jsonify({
        "item": _planner_api_item(item, now.date()),
        "scheduler": scheduler_status,
    }), 201


@family_bp.route("/planner/<plan_id>", methods=["PUT"])
def family_planner_update(plan_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    data = request.get_json(silent=True) or {}
    now, _ = _get_tz_aware_now()

    def update_item(content):
        items = parse_planner(content)
        for item in items:
            if item.get("id") != plan_id:
                continue
            merged = {
                **item,
                **{key: data[key] for key in ("title", "user", "recurrence", "start_date", "active") if key in data},
            }
            values = _validate_planner_payload(merged)
            updated = {
                **item,
                **values,
                "updated_at": now.isoformat(),
                "updated_by": user["id"],
            }
            updated_content, _ = replace_planner_item(content, plan_id, updated)
            return updated_content, updated
        return content, None

    try:
        with scheduler_guard(PLANNER_FILE):
            item = update_text_file(PLANNER_FILE, update_item)
        if not item:
            return jsonify({"error": "Plan nicht gefunden"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        logger.error("Failed to update planner item %s: %s", plan_id, exc)
        return jsonify({"error": "Plan konnte nicht gespeichert werden"}), 500
    try:
        scheduler_status = _run_due_scheduler()
    except OSError as exc:
        logger.error("Plan %s saved, but scheduler run failed: %s", plan_id, exc)
        scheduler_status = {"added": 0, "error": "Plan gespeichert, Scheduler-Prüfung fehlgeschlagen"}
    return jsonify({
        "item": _planner_api_item(item, now.date()),
        "scheduler": scheduler_status,
    })


@family_bp.route("/planner/<plan_id>", methods=["DELETE"])
def family_planner_delete(plan_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err

    def delete_item(content):
        updated_content, deleted = replace_planner_item(content, plan_id, None)
        return updated_content, deleted

    try:
        with scheduler_guard(PLANNER_FILE):
            deleted = update_text_file(PLANNER_FILE, delete_item)
    except OSError as exc:
        logger.error("Failed to delete planner item %s: %s", plan_id, exc)
        return jsonify({"error": "Plan konnte nicht gelöscht werden"}), 500
    if not deleted:
        return jsonify({"error": "Plan nicht gefunden"}), 404
    return jsonify({"ok": True})


@family_bp.route("/planner/run", methods=["POST"])
def family_planner_run():
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    try:
        return jsonify({"ok": True, **_run_due_scheduler()})
    except OSError as exc:
        logger.error("Manual scheduler run failed: %s", exc)
        return jsonify({"error": "Scheduler konnte nicht ausgeführt werden"}), 500


__all__ = ['family_projects', 'family_project_detail', 'family_project_create', 'family_project_update', 'family_project_delete', 'family_task_check', 'family_planner_list', 'family_planner_create', 'family_planner_update', 'family_planner_delete', 'family_planner_run']
