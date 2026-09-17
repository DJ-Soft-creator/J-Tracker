"""Family content, shopping and archive HTTP routes.

AI maintenance contract:
Own AI suggestions, raw/editor access, shopping, search, archive and notification endpoints.
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


def _project_access_visible(proj, uid):
    """Access check only, deliberately weaker than ``_project_visible``.

    The raw markdown editor is also the tool that repairs a structurally
    broken project file (see ``family_project_raw_put``); gating it on
    ``_project_integrity_valid`` too would lock a broken file away from the
    one place that can fix it.
    """
    return _user_can_see(proj.get("assigned_users", []), uid)


@family_bp.route("/project/<project_id>/raw", methods=["GET"])
def family_project_raw_get(project_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_access_visible(proj, user["id"]):
        return jsonify({"error": "Not found"}), 404

    path = _project_path(project_id)
    content = _read_file(path) or ""
    return jsonify({"content": content})


@family_bp.route("/project/<project_id>/raw", methods=["PUT"])
def family_project_raw_put(project_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_access_visible(proj, user["id"]):
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if content is None or not isinstance(content, str):
        return jsonify({"error": "content (string) required"}), 400

    now, _ = _get_tz_aware_now()
    # Repair mode: hand-typed checklist/comment lines without machine fields
    # become real tasks/comments instead of invalidating the whole project -
    # see parse_project_content's repair_context contract.
    parsed = parse_project_content(content, repair_context={"user_id": user["id"], "now": now.isoformat()})
    if not parsed.get("_frontmatter_valid") or parsed.get("id") != project_id:
        return jsonify({"error": "Kopfbereich beschädigt oder verändert - bitte nicht anfassen."}), 400
    parsed["file"] = proj.get("file")
    _save_project(parsed)
    return jsonify({"ok": True})


# ─── Smart Shopping-Editor (nur Titel + Completed) ────────────────────
@family_bp.route("/project/<project_id>/shopping-items", methods=["GET"])
def family_shopping_items_get(project_id):
    """Liefert nur die Items (id, title, completed) eines Einkaufslisten-Projekts."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_visible(proj, user["id"]):
        return jsonify({"error": "Not found"}), 404
    items = []
    for t in proj.get("tasks", []):
        if t.get("source") == "recurring":
            continue
        items.append({
            "id": t.get("id"),
            "title": t.get("title", ""),
            "completed": bool(t.get("completed")),
        })
    return jsonify({"items": items})


@family_bp.route("/project/<project_id>/shopping-items", methods=["PUT"])
def family_shopping_items_put(project_id):
    """Ersetzt alle Items eines Einkaufslisten-Projekts.

    Body: {items: [{id?: str, title: str, completed?: bool}, ...]}

    - Items mit vorhandener id → Titel/Completed aktualisieren, Metadaten bleiben.
    - Items ohne id → neuer Task wird angelegt.
    - Tasks deren id nicht in items vorkommen → werden geloescht.
    """
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
    items = data.get("items")
    if not isinstance(items, list):
        return jsonify({"error": "items (list) required"}), 400

    now, _ = _get_tz_aware_now()
    existing = {t.get("id"): t for t in proj.get("tasks", []) if t.get("source") != "recurring"}
    new_tasks = []
    current_group = ""
    for item in items:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        heading = _shopping_heading_title(title)
        if heading:
            current_group = heading
            continue
        item_id = item.get("id")
        completed = bool(item.get("completed", False))
        if item_id and item_id in existing:
            t = existing[item_id]
            t["title"] = title
            if current_group:
                t["gruppe"] = current_group
            if t.get("completed") != completed:
                if completed:
                    t["completed"] = True
                    t["completed_at"] = now.isoformat()
                    t["completed_by"] = user["id"]
                else:
                    t["completed"] = False
                    t["completed_at"] = None
                    t["completed_by"] = None
            new_tasks.append(t)
        else:
            new_task = {
                "id": str(uuid.uuid4()),
                "title": title,
                "user": "",
                "target_date": "",
                "completed": completed,
                "created_at": now.isoformat(),
                "created_by": user["id"],
                "completed_at": now.isoformat() if completed else None,
                "completed_by": user["id"] if completed else None,
            }
            if current_group:
                new_task["gruppe"] = current_group
            new_tasks.append(new_task)

    recurring_tasks = [t for t in proj.get("tasks", []) if t.get("source") == "recurring"]
    proj["tasks"] = new_tasks + recurring_tasks
    _save_project(proj)
    return jsonify({"ok": True, "count": len(new_tasks)})


@family_bp.route("/project/<project_id>/shopping-clean", methods=["POST"])
def family_shopping_clean(project_id):
    """Remove completed items, or optionally all items, from a shopping list."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    path = _project_path(project_id)
    if path is None:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    clear_all = data.get("clear_all") is True

    def clean(content):
        project = parse_project_content(content, path.name)
        if not _project_visible(project, user["id"]):
            return content, {"forbidden": True}
        if not _is_shopping_project(project):
            return content, {"not_shopping": True}

        tasks = project.get("tasks", [])
        remaining = (
            [task for task in tasks if task.get("source") == "recurring"]
            if clear_all
            else [
                task for task in tasks
                if task.get("source") == "recurring" or not task.get("completed")
            ]
        )
        removed = len(tasks) - len(remaining)
        if not removed:
            return content, {"removed": 0}
        project["tasks"] = remaining
        return serialize_project(project), {"removed": removed}

    result = update_text_file(path, clean) or {}
    if result.get("forbidden"):
        return jsonify({"error": "Not found"}), 404
    if result.get("not_shopping"):
        return jsonify({"error": "Not a shopping list"}), 400
    return jsonify({"ok": True, "removed": result.get("removed", 0), "cleared_all": clear_all})


# ─── Einheitlicher Editor (Tasks + Kommentare) ───────────────────────
@family_bp.route("/project/<project_id>/editor", methods=["GET"])
def family_project_editor_get(project_id):
    """Liefert Tasks + Kommentare + User-Liste fuer den schlaugen Editor.

    Response: {
        mode: 'compact' | 'full',        # compact bei Einkaufsliste
        title: str,
        tasks: [{id, title, completed, user, target_date, source, recurrence}],
        comments: [{id, user, text, created_at, user_display}],
        users: [{id, display}],
    }
    """
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_visible(proj, user["id"]):
        return jsonify({"error": "Not found"}), 404

    is_shopping = _is_shopping_project(proj)
    mode = "compact" if is_shopping else "full"

    tasks = []
    for t in proj.get("tasks", []):
        tasks.append({
            "id": t.get("id"),
            "title": t.get("title", ""),
            "completed": bool(t.get("completed")),
            "user": t.get("user", ""),
            "target_date": t.get("target_date", ""),
            "source": t.get("source", "manual"),
            "recurrence": t.get("recurrence"),
        })
    comments = []
    for c in proj.get("comments", []):
        comments.append({
            "id": c.get("id"),
            "user": c.get("user", ""),
            "text": c.get("text", ""),
            "created_at": c.get("created_at", ""),
            "user_display": _user_display(c.get("user")),
        })
    users = []
    main = _main_module()
    all_users = main.get_all_users() if hasattr(main, "get_all_users") else []
    for u in all_users:
        users.append({"id": u.get("id"), "display": u.get("username") or u.get("id")})

    return jsonify({
        "mode": mode,
        "title": proj.get("title") or "",
        "tasks": tasks,
        "comments": comments,
        "users": users,
    })


@family_bp.route("/project/<project_id>/editor", methods=["PUT"])
def family_project_editor_put(project_id):
    """Ersetzt Tasks und Kommentare eines Projekts (einheitlicher Editor).

    Body: {
        tasks: [{id?: str, title: str, completed?: bool,
                 user?: str, target_date?: str}],
        comments: [{id?: str, user: str, text: str, created_at?: str}],
        deleted_comment_ids: [str, ...]
    }

    Tasks:
      - mit vorhandener id → title/completed/user/target_date aktualisieren,
        restliche Metadaten bleiben erhalten.
      - ohne id → neuer Task (defaults created_at/created_by).
      - Tasks deren id nicht in tasks vorkommen → geloescht
        (ausser recurring-Tasks, diese bleiben unveraendert erhalten).
    Kommentare:
      - mit vorhandener id → unveraendert (nur anzeigen, nicht editieren).
      - ohne id → neu anlegen.
      - in deleted_comment_ids → entfernen.

    Der Aufgaben-Editor im Familien-Tab schickt seit 4.7.14 **keine**
    Kommentarfelder mehr: die Sektion war der einzige Weg, einen Kommentar
    anzulegen, und zeigte ihn danach nirgends wieder. Weil beide Listen hier
    leer ankommen, bleiben bereits vorhandene Kommentare im Markdown
    unangetastet. Die Felder bleiben im Vertrag, damit ein Kommentarbereich
    spaeter an anderer Stelle wieder andocken kann.
    """
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

    is_shopping = _is_shopping_project(proj)

    data = request.get_json(silent=True) or {}
    tasks_in = data.get("tasks")
    if not isinstance(tasks_in, list):
        return jsonify({"error": "tasks (list) required"}), 400
    comments_in = data.get("comments")
    if not isinstance(comments_in, list):
        comments_in = []
    deleted_comment_ids = data.get("deleted_comment_ids")
    if not isinstance(deleted_comment_ids, list):
        deleted_comment_ids = []

    now, _ = _get_tz_aware_now()

    # --- Tasks zusammenfuehren ---
    existing = {t.get("id"): t for t in proj.get("tasks", []) if t.get("source") != "recurring"}
    new_tasks = []
    current_group = ""
    for item in tasks_in:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        if is_shopping:
            heading = _shopping_heading_title(title)
            if heading:
                current_group = heading
                continue
        item_id = item.get("id")
        completed = bool(item.get("completed", False))
        user_val = str(item.get("user", "")).strip()
        target_date = str(item.get("target_date", "")).strip()
        if item_id and item_id in existing:
            t = existing[item_id]
            t["title"] = title
            t["user"] = user_val
            t["target_date"] = target_date
            if current_group:
                t["gruppe"] = current_group
            if t.get("completed") != completed:
                if completed:
                    t["completed"] = True
                    t["completed_at"] = now.isoformat()
                    t["completed_by"] = user["id"]
                else:
                    t["completed"] = False
                    t["completed_at"] = None
                    t["completed_by"] = None
            new_tasks.append(t)
        else:
            new_task = {
                "id": str(uuid.uuid4()),
                "title": title,
                "user": user_val,
                "target_date": target_date,
                "completed": completed,
                "created_at": now.isoformat(),
                "created_by": user["id"],
                "completed_at": now.isoformat() if completed else None,
                "completed_by": user["id"] if completed else None,
            }
            if is_shopping and current_group:
                new_task["gruppe"] = current_group
            new_tasks.append(new_task)

    recurring_tasks = [t for t in proj.get("tasks", []) if t.get("source") == "recurring"]
    proj["tasks"] = new_tasks + recurring_tasks

    # --- Kommentare zusammenfuehren ---
    existing_comments = {c.get("id"): c for c in proj.get("comments", [])}
    deleted_set = set(deleted_comment_ids)
    new_comments = []
    for c in existing_comments.values():
        if c.get("id") in deleted_set:
            continue
        new_comments.append(c)
    for c in comments_in:
        cid = c.get("id")
        if cid:
            continue  # bestehende Kommentare nicht editieren
        text = str(c.get("text", "")).strip()
        if not text:
            continue
        new_comments.append({
            "id": str(uuid.uuid4()),
            "user": str(c.get("user", "")).strip(),
            "text": text,
            "created_at": now.isoformat(),
        })

    proj["comments"] = new_comments

    _save_project(proj)
    return jsonify({"ok": True, "task_count": len(new_tasks), "comment_count": len(new_comments)})


@family_bp.route("/archive", methods=["GET"])
def family_archive_list():
    _ensure_dirs()
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    items = []
    if ARCHIVE_DIR.is_dir():
        for p in sorted(ARCHIVE_DIR.glob("*.md")):
            item = _parse_archive_file(p)
            if item and _archive_item_visible(item, user["id"]):
                # Die Archivdatei speichert nur IDs. Die Aufloesung gehoert hier
                # hin, wie bei Tasks und Kommentaren auch - der Client fuehrt
                # bewusst kein eigenes Benutzerverzeichnis.
                item["user_display"] = _user_display(item.get("user"))
                item["completed_by_display"] = _user_display(item.get("completed_by"))
                items.append(item)
    return jsonify({"items": items})


@family_bp.route("/archive/<task_id>", methods=["POST"])
def family_archive_item(task_id):
    """Archiviert einen einzelnen erledigten Task sofort."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    for path in _all_project_paths():
        proj = parse_project_file(path)
        if not _project_visible(proj, user["id"]):
            continue
        for t in proj.get("tasks", []):
            if t.get("id") == task_id:
                if not t.get("completed"):
                    # automatisch als erledigt markieren
                    now, _ = _get_tz_aware_now()
                    t["completed"] = True
                    t["completed_at"] = now.isoformat()
                    t["completed_by"] = user["id"]
                if not _archive_task(proj["id"], t):
                    return jsonify({"error": "invalid task id"}), 409
                proj["tasks"] = [x for x in proj.get("tasks", []) if x.get("id") != task_id]
                _atomic_write(path, serialize_project(proj))
                return jsonify({"ok": True, "archived": task_id})
    return jsonify({"error": "task not found"}), 404


@family_bp.route("/notifications", methods=["GET"])
def family_notifications():
    """Badge-Status {blue, red}, je None/"solid"/"dashed".

    Rot:  offene sichtbare Task ist heute oder früher fällig.
          "solid"  = mindestens eine davon ist der eigenen Person zugewiesen
                     (oder niemandem zugewiesen, also allgemein relevant).
          "dashed" = nur Tasks, die ausschließlich einer anderen Person
                     zugewiesen sind.
    Blau: seit last_family_visit haben andere User sichtbare Items erstellt.
          "solid"  = mindestens eines davon betrifft die eigene Person
                     (zugewiesen an mich oder niemandem zugewiesen).
          "dashed" = nur Items, die ausschließlich einer anderen Person
                     zugewiesen sind.
    """
    _ensure_dirs()
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        _run_due_scheduler()
    except OSError as exc:
        logger.error("Scheduler run failed while checking notifications: %s", exc)
    _archive_overdue_completed()
    uid = user["id"]
    now, _ = _get_tz_aware_now()
    today_str = now.strftime("%Y-%m-%d")

    last_visit_str = user.get("last_family_visit")
    last_visit = None
    if last_visit_str:
        try:
            lv = datetime.fromisoformat(last_visit_str)
            if lv.tzinfo is None:
                lv = lv.replace(tzinfo=timezone.utc)
            last_visit = lv
        except (ValueError, TypeError):
            last_visit = None

    blue_own = False
    blue_other = False
    red_own = False
    red_other = False

    def _is_own_or_unassigned(assigned_uid):
        return not assigned_uid or assigned_uid == uid

    recurring = parse_recurring_tasks()

    for path in _all_project_paths():
        proj = parse_project_file(path)
        if not proj:
            continue
        if not _project_visible(proj, uid):
            continue
        # Blue: Projekt selbst (kein einzelner Verantwortlicher -> allgemein)
        if proj.get("created_by") and proj["created_by"] != uid:
            ca = _parse_iso(proj.get("created_at"))
            if ca and (last_visit is None or ca > last_visit):
                blue_own = True
        for t in proj.get("tasks", []):
            assigned = t.get("user")
            # Blue
            if t.get("created_by") and t["created_by"] != uid:
                ca = _parse_iso(t.get("created_at"))
                if ca and (last_visit is None or ca > last_visit):
                    if _is_own_or_unassigned(assigned):
                        blue_own = True
                    else:
                        blue_other = True
            # Red: offene Task heute faellig
            if not t.get("completed") and t.get("target_date"):
                td = t["target_date"].strip()
                if td <= today_str:
                    if _is_own_or_unassigned(assigned):
                        red_own = True
                    else:
                        red_other = True
    for task in recurring:
        assigned = task.get("user")
        if task.get("created_by") and task["created_by"] != uid:
            created_at = _parse_iso(task.get("created_at"))
            if created_at and (last_visit is None or created_at > last_visit):
                if _is_own_or_unassigned(assigned):
                    blue_own = True
                else:
                    blue_other = True
        if not task.get("completed") and task.get("target_date"):
            if task["target_date"].strip() <= today_str:
                if _is_own_or_unassigned(assigned):
                    red_own = True
                else:
                    red_other = True

    red = "solid" if red_own else ("dashed" if red_other else None)
    blue = "solid" if blue_own else ("dashed" if blue_other else None)

    return jsonify({"blue": blue, "red": red})


@family_bp.route("/visit", methods=["POST"])
def family_visit():
    """Setzt last_family_visit = now auf dem User-Objekt."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err
    now, _ = _get_tz_aware_now()
    user["last_family_visit"] = now.isoformat()
    _main_module().update_user(user)
    return jsonify({"ok": True})


# ─── Helfer: ISO-Parser ───────────────────────────────────────────────


__all__ = ['family_project_raw_get', 'family_project_raw_put', 'family_shopping_items_get', 'family_shopping_items_put', 'family_shopping_clean', 'family_project_editor_get', 'family_project_editor_put', 'family_archive_list', 'family_archive_item', 'family_notifications', 'family_visit']
