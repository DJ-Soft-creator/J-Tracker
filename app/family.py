"""Family domain facade: paths, parsing, serialization and write primitives.

AI maintenance contract: keep HTTP orchestration in family_routes.py or
family_content_routes.py. Keep shared Markdown invariants and persistent write
helpers here so both route groups and external scripts use one implementation.

Stellt das Blueprint `family_bp` mit allen /api/family/* Endpunkten sowie
Parser/Serializer fuer Projekt-Dateien und zugehoerige Helper bereit.

Konventionen wie in main.py: pathlib, fcntl-Locks, atomare Writes via
tmp+os.replace, logging via logger, keine externen Libs.
"""

import os
import re
import uuid
import logging
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, request, jsonify, session

from scheduling import (
    RECURRENCE_LABELS,
    RECURRENCE_OPTIONS,
    VALID_RECURRENCES,
    append_planner_item,
    ensure_text_file,
    materialize_due_tasks,
    next_due_date,
    parse_planner,
    parse_recurring_tasks as parse_recurring_task_content,
    read_text_file,
    remove_recurring_tasks,
    replace_planner_item,
    scheduler_guard,
    set_recurring_task_completion,
    update_text_file,
    write_text_file,
)

import family_views
from family_markdown import (
    _valid_id,
    _KV_RE,
    _is_shopping_project,
    _shopping_heading_title,
    parse_project_content,
    serialize_project,
)

logger = logging.getLogger(__name__)

# ─── Pfade ────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
FAMILY_DIR = DATA_DIR / "family"
PROJECTS_DIR = FAMILY_DIR / "projects"
PLANNER_DIR = FAMILY_DIR / "planner"
ARCHIVE_DIR = FAMILY_DIR / "archive"
# Abgeschlossene Projekte liegen in einem Unterordner, damit sie nicht mit den
# archivierten Einzelaufgaben (`archive/<task-id>.md`) kollidieren. Brain
# indiziert FAMILY_DIR rekursiv, sie bleiben also durchsuchbar.
ARCHIVED_PROJECTS_DIR = ARCHIVE_DIR / "projects"
PLANNER_FILE = PLANNER_DIR / "recurring.md"
FAMILY_TASKS_FILE = FAMILY_DIR / "Familien-Aufgaben.md"
RECURRING_PROJECT_ID = "wiederkehrende-aufgaben"

# ─── Blueprint ─────────────────────────────────────────────────────────
family_bp = Blueprint("family", __name__, url_prefix="/api/family")


# ─── Helfer: Zugriff auf main.py ──────────────────────────────────────
def _main_module():
    """Liefert das main.py-Modul (lazy, vermeidet Circular Import).

    Beim Aufruf `python main.py` heisst das Modul '__main__'; beim Import
    als Bibliothek 'main'. Beide Faelle abdecken.
    """
    import sys
    imported = sys.modules.get("main")
    if imported and hasattr(imported, "_current_user"):
        return imported
    return sys.modules.get("__main__")


def _current_user():
    return _main_module()._current_user()


def _get_tz_aware_now():
    return _main_module().get_tz_aware_now()


def _user_display(uid):
    """Loest eine User-ID via get_user_by_id auf → username (fallback uid)."""
    if not uid:
        return ""
    main = _main_module()
    u = main.get_user_by_id(uid)
    if u and u.get("username"):
        return u["username"]
    return str(uid)[:8]


def _ensure_dirs():
    """Legt alle Family-Ordner lazily an."""
    for d in (FAMILY_DIR, PROJECTS_DIR, PLANNER_DIR, ARCHIVE_DIR, ARCHIVED_PROJECTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    ensure_text_file(PLANNER_FILE)
    ensure_text_file(FAMILY_TASKS_FILE)


# ─── Atomare File-IO (wie main.py) ────────────────────────────────────
def _atomic_write(path: Path, content: str):
    write_text_file(path, content)


def _read_file(path: Path):
    if not path.exists():
        return None
    return read_text_file(path)


# ─── Sichtbarkeit ─────────────────────────────────────────────────────
def _user_can_see(assigned_users, uid):
    """True wenn assigned_users leer ist ODER uid enthalten ist."""
    if not assigned_users:
        return True
    return uid in assigned_users


def _project_integrity_valid(project):
    if not (
        project
        and project.get("_frontmatter_valid", True)
        and project.get("_task_ids_valid", True)
        and _valid_id(project.get("id"))
    ):
        return False
    file_name = project.get("file")
    return not file_name or Path(file_name).stem == project["id"]


def _project_visible(project, uid):
    return _project_integrity_valid(project) and _user_can_see(project.get("assigned_users", []), uid)


# ─── Projekt-Parser/Serializer (siehe family_markdown.py) ─────────────
def parse_project_file(path: Path):
    """Liest eine Projekt-Datei und liefert ein dict (oder None)."""
    content = _read_file(path)
    if content is None:
        return None
    return parse_project_content(content, path.name)


# ─── Recurring-Tasks aus Familien-Aufgaben.md ─────────────────────────
def parse_recurring_tasks():
    """Liefert erzeugte Aufgaben, inklusive bestehender Legacy-Zeilen."""
    content = _read_file(FAMILY_TASKS_FILE)
    if not content:
        return []
    return parse_recurring_task_content(content)


# ─── Projekt laden/schreiben ──────────────────────────────────────────
def _project_path(project_id):
    if not _valid_id(project_id):
        return None
    return PROJECTS_DIR / f"{project_id}.md"


def _load_project(project_id):
    path = _project_path(project_id)
    if path is None:
        return None
    return parse_project_file(path)


def _touch(project):
    """Record the last edit. Every mutation counts, including a task toggle."""
    now, _ = _get_tz_aware_now()
    project["updated_at"] = now.isoformat()
    return project


def _save_project(project, touch=True):
    _ensure_dirs()
    pid = project.get("id")
    if not pid:
        pid = str(uuid.uuid4())
        project["id"] = pid
    if not _valid_id(pid):
        raise ValueError("Invalid Family project id")
    project["id"] = pid
    if touch:
        _touch(project)
    path = _project_path(pid)
    _atomic_write(path, serialize_project(project))
    return project


def set_task_completion(project_id, task_id, user_id, completed, can_access=None, allow_assignee=False):
    """Transactionally update a Family task and its completion audit fields."""
    _ensure_dirs()
    now, _ = _get_tz_aware_now()
    if project_id == RECURRING_PROJECT_ID:
        return set_recurring_task_completion(
            FAMILY_TASKS_FILE,
            task_id,
            user_id,
            now.isoformat(),
            completed,
            can_access=can_access,
        )

    if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", project_id):
        return {"forbidden": True, "found": False, "task": None}

    path = _project_path(project_id)

    def update(content):
        if can_access and not can_access(content):
            return content, {"forbidden": True, "found": False, "task": None}
        project = parse_project_content(content, path.name)
        visible = _project_visible(project, user_id)
        if not visible and not (allow_assignee and _project_integrity_valid(project)):
            return content, {"forbidden": True, "found": False, "task": None}
        for task in project.get("tasks", []):
            if task.get("id") != task_id:
                continue
            # Eine Aufgabe aus einem nicht freigegebenen Dokument darf nur die
            # Person abhaken, der sie zugewiesen ist (Entscheidung 2 der
            # Anforderung). Sonst waere die Task-ID ein Umweg um assigned_users.
            if not visible and (task.get("user") or "") != user_id:
                return content, {"forbidden": True, "found": False, "task": None}
            already_completed = task.get("completed") == completed
            if not already_completed:
                task["completed"] = completed
                task["completed_at"] = now.isoformat() if completed else None
                task["completed_by"] = user_id if completed else None
                _touch(project)
                return serialize_project(project), {
                    "found": True,
                    "already_completed": False,
                    "task": task,
                }
            return content, {
                "found": True,
                "already_completed": True,
                "task": task,
            }
        return content, {"found": False, "already_completed": False, "task": None}

    return update_text_file(path, update)


def _all_project_paths():
    _ensure_dirs()
    if not PROJECTS_DIR.is_dir():
        return []
    return sorted(PROJECTS_DIR.glob("*.md"))


# ─── Archiv-Helfer ─────────────────────────────────────────────────────
def _archive_task(project_id, task):
    """Verschiebt einen erledigten Task ins Archiv."""
    _ensure_dirs()
    if not _valid_id(project_id) or not _valid_id(task.get("id")):
        return False
    archive_path = ARCHIVE_DIR / f"{task['id']}.md"
    fm = [
        "---",
        f"id: {task.get('id', '')}",
        f"title: {task.get('title', '')}",
        f"user: {task.get('user', '')}",
        f"completed_at: {task.get('completed_at') or ''}",
        f"completed_by: {task.get('completed_by') or ''}",
        f"project_id: {project_id}",
        f"target_date: {task.get('target_date', '')}",
        f"created_at: {task.get('created_at', '')}",
        f"created_by: {task.get('created_by', '')}",
        "---",
        "",
        f"## {task.get('title', '')}",
        "",
    ]
    _atomic_write(archive_path, "\n".join(fm))
    return True


_ARCHIVE_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def _parse_archive_file(path):
    content = _read_file(path)
    if content is None:
        return None
    m = _ARCHIVE_FM_RE.match(content)
    item = {"file": path.name}
    if m:
        for line in m.group(1).split("\n"):
            kv = _KV_RE.match(line)
            if kv:
                item[kv.group(1)] = kv.group(2).strip()
        item["body"] = m.group(2).strip()
    return item


def _archive_item_visible(item, uid):
    project_id = item.get("project_id") if item else None
    if project_id == RECURRING_PROJECT_ID:
        return item.get("user") == uid
    return _project_visible(_load_project(project_id), uid)


# ─── Lazy Archivierung uefaelliger erledigter Tasks ───────────────────
def _archive_overdue_completed():
    """Prueft alle Projekte: erledigte Tasks mit completed_at + 7d < heute
    werden archiviert (aus Projekt entfernt, ins Archiv geschrieben)."""
    now, _ = _get_tz_aware_now()
    changed = False
    for path in _all_project_paths():
        proj = parse_project_file(path)
        if not _project_integrity_valid(proj):
            continue
        keep = []
        for t in proj.get("tasks", []):
            if t.get("completed") and t.get("completed_at"):
                try:
                    ca = datetime.fromisoformat(t["completed_at"])
                    if ca.tzinfo is None:
                        ca = ca.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    keep.append(t)
                    continue
                if (now - ca) > timedelta(days=7):
                    if _archive_task(proj["id"], t):
                        changed = True
                        continue
            keep.append(t)
        if len(keep) != len(proj.get("tasks", [])):
            proj["tasks"] = keep
            _atomic_write(path, serialize_project(proj))

    recurring_to_remove = []
    for task in parse_recurring_tasks():
        if not task.get("completed") or not task.get("completed_at"):
            continue
        completed_at = _parse_iso(task.get("completed_at"))
        if completed_at and (now - completed_at) > timedelta(days=7):
            if _archive_task(RECURRING_PROJECT_ID, task):
                recurring_to_remove.append(task["id"])
    if recurring_to_remove:
        remove_recurring_tasks(FAMILY_TASKS_FILE, recurring_to_remove)
        changed = True
    return changed


def _run_due_scheduler():
    """Materialize due tasks on access in addition to the daily sidecar."""
    _ensure_dirs()
    now, _ = _get_tz_aware_now()
    return materialize_due_tasks(PLANNER_FILE, FAMILY_TASKS_FILE, now)


def _recurring_project(tasks):
    return {
        "id": RECURRING_PROJECT_ID,
        "title": "Geplante Aufgaben",
        "template_id": "aufgabenplaner",
        "target_file": FAMILY_TASKS_FILE.name,
        "assigned_users": [],
        "created_at": None,
        "created_by": None,
        "tasks": tasks,
        "comments": [],
        "file": FAMILY_TASKS_FILE.name,
    }


def _planner_value(value, label, max_length=200):
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{label} fehlt")
    if len(cleaned) > max_length:
        raise ValueError(f"{label} ist zu lang")
    if "|" in cleaned or "\n" in cleaned or "\r" in cleaned:
        raise ValueError(f"{label} enthält ungültige Zeichen")
    return cleaned


def _validate_planner_payload(data):
    title = _planner_value(data.get("title"), "Titel")
    user_uid = _planner_value(data.get("user"), "Verantwortliche Person", max_length=100)
    recurrence = _planner_value(data.get("recurrence"), "Wiederholung", max_length=40)
    if recurrence not in VALID_RECURRENCES:
        raise ValueError("Unbekannte Wiederholung")

    start_date = _planner_value(
        data.get("start_date") or data.get("target_date"),
        "Startdatum",
        max_length=10,
    )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date):
        raise ValueError("Startdatum muss das Format JJJJ-MM-TT haben")
    try:
        parsed_start_date = date.fromisoformat(start_date)
    except ValueError as exc:
        raise ValueError("Startdatum ist ungültig") from exc
    if parsed_start_date.isoformat() != start_date:
        raise ValueError("Startdatum muss das Format JJJJ-MM-TT haben")

    users = _main_module().get_all_users()
    if user_uid not in {item.get("id") for item in users}:
        raise ValueError("Verantwortliche Person ist unbekannt")

    active = data.get("active", True)
    if not isinstance(active, bool):
        raise ValueError("Status ist ungültig")
    return {
        "title": title,
        "user": user_uid,
        "recurrence": recurrence,
        "start_date": start_date,
        "active": active,
    }


def _planner_api_item(item, today):
    due = next_due_date(item, today)
    public_item = {key: value for key, value in item.items() if not key.startswith("_")}
    return {
        **public_item,
        "user_display": _user_display(item.get("user")),
        "recurrence_label": RECURRENCE_LABELS.get(
            item.get("recurrence"), item.get("recurrence", "")
        ),
        "next_due_date": due.isoformat() if due else None,
    }


def _create_recurring_plan(title, user_uid, recurrence, start_date, created_by, active=True):
    values = _validate_planner_payload({
        "title": title,
        "user": user_uid,
        "recurrence": recurrence,
        "start_date": start_date,
        "active": active,
    })
    now, _ = _get_tz_aware_now()
    item = {
        "id": f"task-{uuid.uuid4().hex[:12]}",
        **values,
        "created_at": now.isoformat(),
        "created_by": created_by or "",
        "updated_at": "",
        "updated_by": "",
    }

    def append_item(content):
        return append_planner_item(content, item), item

    _ensure_dirs()
    with scheduler_guard(PLANNER_FILE):
        return update_text_file(PLANNER_FILE, append_item)


# ─── Decorators (auth + csrf) – nutzen main.py ────────────────────────
def _check_csrf():
    """Prueft X-CSRF-Token gegen Session. Liefert (resp, status) oder None."""
    import secrets as _secrets
    from flask import request as _req
    if _req.method in ("POST", "PUT", "DELETE", "PATCH"):
        token = _req.headers.get("X-CSRF-Token")
        sess_token = session.get("csrf_token")
        if not sess_token or not token or not _secrets.compare_digest(token, sess_token):
            return jsonify({"error": "Invalid CSRF token"}), 403
    return None


# ─── Endpunkte ────────────────────────────────────────────────────────
def _parse_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _sanitize_project(proj):
    """Bereitet ein Projekt-dict fuer die API-Antwort vor (Usernamen etc.)."""
    out = {
        "id": proj.get("id"),
        "title": proj.get("title"),
        "type": family_views.normalize_type(proj.get("type")),
        "template_id": proj.get("template_id"),
        "target_file": proj.get("target_file") or None,
        "assigned_users": proj.get("assigned_users", []),
        "created_at": proj.get("created_at"),
        "created_by": proj.get("created_by"),
        "created_by_display": _user_display(proj.get("created_by")),
        "updated_at": proj.get("updated_at"),
        "owner": proj.get("owner") or None,
        "archived_at": proj.get("archived_at") or None,
        "exclude_from_personal": bool(proj.get("exclude_from_personal")),
        "personal": family_views.is_personal_bucket(proj),
        # Der freie Notiztext gehoert zur Karte: ohne ihn zeigt eine Notiz mit
        # Aufgaben nur noch die Aufgaben. `notes` steht ueber der Aufgabenliste,
        # `notes_after` darunter - so, wie beides in der Datei liegt.
        "notes": proj.get("notes") or "",
        "notes_after": proj.get("notes_after") or "",
        "tasks": [],
        "comments": [],
        "file": proj.get("file"),
    }
    for t in proj.get("tasks", []):
        out["tasks"].append({
            "id": t.get("id"),
            "title": t.get("title"),
            "user": t.get("user"),
            "user_display": _user_display(t.get("user")),
            "target_date": t.get("target_date"),
            "completed": bool(t.get("completed")),
            "created_at": t.get("created_at"),
            "created_by": t.get("created_by"),
            "completed_at": t.get("completed_at"),
            "completed_by": t.get("completed_by"),
            "completed_by_display": _user_display(t.get("completed_by")),
            "source": t.get("source", "manual"),
            "recurrence": t.get("recurrence"),
            "recurrence_label": RECURRENCE_LABELS.get(t.get("recurrence"), ""),
            "plan_id": t.get("plan_id"),
            "sort_index": t.get("sort_index"),
            "gruppe": t.get("gruppe", ""),
            # Nur in Sammelansichten gesetzt: wo die Aufgabe wirklich lebt.
            "origin_id": t.get("origin_id"),
            "origin_title": t.get("origin_title"),
            "origin_accessible": t.get("origin_accessible", True),
        })
    for c in proj.get("comments", []):
        out["comments"].append({
            "id": c.get("id"),
            "user": c.get("user"),
            "user_display": _user_display(c.get("user")),
            "text": c.get("text"),
            "created_at": c.get("created_at"),
        })
    return out


# ─── Append-API (genutzt von api_submit bei target_file-Templates) ────
def append_task_to_target_file(target_file, title, user_uid, target_date="", created_by=None, group=""):
    """Oeffnet/legt ein Projekt pro target_file an und fuegt einen Task hinzu.

    Projekt-ID = slug von target_file (z.B. 'haushalt.md' → 'haushalt').
    """
    _ensure_dirs()
    if not target_file:
        return None
    slug = Path(target_file).stem
    project_id = slug
    path = _project_path(project_id)
    proj = parse_project_file(path)
    now, _ = _get_tz_aware_now()
    if not proj:
        proj = {
            "id": project_id,
            "title": slug.replace("_", " ").capitalize(),
            "template_id": slug,
            "target_file": target_file,
            "assigned_users": [],
            "created_at": now.isoformat(),
            "created_by": created_by or user_uid,
            "tasks": [],
            "comments": [],
        }
    shopping_list = target_file.endswith("einkaufsliste.md")
    existing_order = [task.get("sort_index") for task in proj.get("tasks", []) if isinstance(task.get("sort_index"), int)]
    task = {
        "id": str(uuid.uuid4()),
        "title": title,
        "user": user_uid or "",
        "target_date": target_date or "",
        "completed": False,
        "created_at": now.isoformat(),
        "created_by": created_by or user_uid,
    }
    if shopping_list:
        # Der Schreiben-Tab kann bereits sortierte Abschnitte liefern.  Die
        # Gruppenüberschrift wird daher unverändert gespeichert, ohne einen
        # nachgelagerten Sortier- oder KI-Schritt zu erzwingen.
        task["gruppe"] = str(group or "Unsortiert").strip() or "Unsortiert"
        task["sort_index"] = max(existing_order, default=0) + 1
    proj.setdefault("tasks", []).append(task)
    _atomic_write(path, serialize_project(proj))
    return task


def add_recurring_to_planner(title, user_uid, recurrence, target_date="", created_by=None):
    """Fuegt einen recurring-Eintrag zu planner/recurring.md hinzu."""
    now, _ = _get_tz_aware_now()
    item = _create_recurring_plan(
        title=title,
        user_uid=user_uid,
        recurrence=recurrence,
        start_date=target_date or now.date().isoformat(),
        created_by=created_by,
    )
    try:
        _run_due_scheduler()
    except OSError as exc:
        logger.error("Plan %s saved, but scheduler run failed: %s", item["id"], exc)
    return item["id"]


def create_standalone_project(title, template_id, assigned_users=None, created_by=None, target_file=None):
    """Erstellt ein neues Projekt (UUID) – genutzt von 'projekt'-Template."""
    _ensure_dirs()
    now, _ = _get_tz_aware_now()
    pid = str(uuid.uuid4())
    proj = {
        "id": pid,
        "title": title,
        "template_id": template_id,
        "target_file": target_file or "",
        "assigned_users": assigned_users or [],
        "created_at": now.isoformat(),
        "created_by": created_by,
        "tasks": [],
        "comments": [],
    }
    path = _project_path(pid)
    _atomic_write(path, serialize_project(proj))
    return proj


# Document persistence loads before the routes: it builds on the parsers above
# and its helpers must be part of the facade the route modules copy from.
import family_documents as _family_documents
for _export_name in _family_documents.__all__:
    globals()[_export_name] = getattr(_family_documents, _export_name)

# Route modules load last so every parser, serializer and write helper above is
# available to their compatibility context. Keep route ownership split by domain.
import family_routes as _family_routes
for _export_name in _family_routes.__all__:
    globals()[_export_name] = getattr(_family_routes, _export_name)

import family_content_routes as _family_content_routes
for _export_name in _family_content_routes.__all__:
    globals()[_export_name] = getattr(_family_content_routes, _export_name)

import family_board_routes as _family_board_routes
for _export_name in _family_board_routes.__all__:
    globals()[_export_name] = getattr(_family_board_routes, _export_name)
