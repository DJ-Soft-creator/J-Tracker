"""Family Tracker Backend – Phase 1.

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

logger = logging.getLogger(__name__)

# ─── Pfade ────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
FAMILY_DIR = DATA_DIR / "family"
PROJECTS_DIR = FAMILY_DIR / "projects"
PLANNER_DIR = FAMILY_DIR / "planner"
ARCHIVE_DIR = FAMILY_DIR / "archive"
PLANNER_FILE = PLANNER_DIR / "recurring.md"
FAMILY_TASKS_FILE = FAMILY_DIR / "Familien-Aufgaben.md"
RECURRING_PROJECT_ID = "wiederkehrende-aufgaben"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

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
    for d in (FAMILY_DIR, PROJECTS_DIR, PLANNER_DIR, ARCHIVE_DIR):
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


def _valid_id(value):
    return isinstance(value, str) and _SAFE_ID_RE.fullmatch(value) is not None


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


# ─── Projekt-Parser/Serializer ─────────────────────────────────────────
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)
_KV_RE = re.compile(r"^(\w+):\s*(.*)$")

_TASK_RE = re.compile(
    r'^-\s*\[(?P<check>[ xX])\]\s*'
    r'id:\s*(?P<id>[^|]+?)\s*\|\s*'
    r'title:\s*(?P<title>[^|]*?)\s*\|\s*'
    r'user:\s*(?P<user>[^|]*?)\s*\|\s*'
    r'target-date:\s*(?P<target_date>[^|]*?)\s*'
    r'(?:\|\s*created_at:\s*(?P<created_at>[^|]*?))?'
    r'(?:\|\s*created_by:\s*(?P<created_by>[^|]*?))?'
    r'(?:\|\s*completed_at:\s*(?P<completed_at>[^|]*?))?\s*'
    r'(?:\|\s*completed_by:\s*(?P<completed_by>[^|]*?))?\s*$'
)

_COMMENT_RE = re.compile(
    r'^-\s*id:\s*(?P<id>[^|]+?)\s*\|\s*'
    r'user:\s*(?P<user>[^|]*?)\s*\|\s*'
    r'text:\s*(?P<text>.*?)\s*\|\s*'
    r'created_at:\s*(?P<created_at>[^|]*?)\s*$'
)

def parse_project_file(path: Path):
    """Liest eine Projekt-Datei und liefert ein dict (oder None)."""
    content = _read_file(path)
    if content is None:
        return None
    return parse_project_content(content, path.name)


def parse_project_content(content, file_name=None):
    """Parst Projekt-Markdown aus einer bereits gesperrten Transaktion."""
    project = {
        "id": None,
        "title": None,
        "template_id": None,
        "target_file": None,
        "assigned_users": [],
        "created_at": None,
        "created_by": None,
        "tasks": [],
        "comments": [],
        "file": file_name,
        "_frontmatter_valid": True,
        "_task_ids_valid": True,
    }

    # Frontmatter
    normalized = (content or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    fm_match = _FRONTMATTER_RE.match(normalized)
    body = normalized
    if fm_match:
        lines = fm_match.group(1).split("\n")
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#"):
                index += 1
                continue
            m = _KV_RE.match(line)
            if not m:
                project["_frontmatter_valid"] = False
                index += 1
                continue
            key, val = m.group(1), m.group(2).strip()
            if key == "assigned_users":
                v = val.strip()
                if v.startswith("["):
                    if not v.endswith("]"):
                        project["_frontmatter_valid"] = False
                        index += 1
                        continue
                    inner = v[1:-1].strip()
                    if inner:
                        parts = [p.strip().strip('"').strip("'") for p in inner.split(",")]
                        project["assigned_users"] = [p for p in parts if p]
                    else:
                        project["assigned_users"] = []
                elif v:
                    project["assigned_users"] = [v] if v else []
                else:
                    assigned = []
                    while index + 1 < len(lines):
                        list_match = re.match(r"^\s+-\s+(.+?)\s*$", lines[index + 1])
                        if not list_match:
                            break
                        assigned.append(list_match.group(1).strip().strip("'\""))
                        index += 1
                    project["assigned_users"] = [item for item in assigned if item]
            else:
                project[key] = val
            index += 1
        body = normalized[fm_match.end():]
    elif normalized.startswith("---"):
        project["_frontmatter_valid"] = False
        body = ""

    # Body parsen
    section = None
    seen_task_ids = set()
    for raw_line in body.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            header = stripped[3:].strip().lower()
            if "aufgabe" in header:
                section = "tasks"
            elif "kommentar" in header:
                section = "comments"
            else:
                section = None
            continue

        if not stripped:
            continue

        if section == "tasks":
            m = _TASK_RE.match(stripped)
            if m:
                task_id = m.group("id").strip()
                if not _valid_id(task_id) or task_id in seen_task_ids:
                    project["_task_ids_valid"] = False
                    continue
                seen_task_ids.add(task_id)
                project["tasks"].append({
                    "id": task_id,
                    "title": m.group("title").strip(),
                    "user": m.group("user").strip(),
                    "target_date": m.group("target_date").strip(),
                    "completed": m.group("check").lower() == "x",
                    "created_at": (m.group("created_at") or "").strip(),
                    "created_by": (m.group("created_by") or "").strip(),
                    "completed_at": (m.group("completed_at") or "").strip() or None,
                    "completed_by": (m.group("completed_by") or "").strip() or None,
                    "source": "manual",
                })
            continue

        if section == "comments":
            m = _COMMENT_RE.match(stripped)
            if m:
                project["comments"].append({
                    "id": m.group("id").strip(),
                    "user": m.group("user").strip(),
                    "text": m.group("text").strip(),
                    "created_at": m.group("created_at").strip(),
                })
            continue

    return project


def serialize_project(project):
    """Serialisiert ein Projekt-dict in MD-String (Frontmatter + Body)."""
    lines = ["---"]
    lines.append(f"id: {project.get('id') or ''}")
    lines.append(f"title: {project.get('title') or ''}")
    lines.append(f"template_id: {project.get('template_id') or ''}")
    lines.append(f"target_file: {project.get('target_file') or ''}")
    au = project.get("assigned_users") or []
    if au:
        lines.append("assigned_users: [" + ", ".join(au) + "]")
    else:
        lines.append("assigned_users: []")
    lines.append(f"created_at: {project.get('created_at') or ''}")
    lines.append(f"created_by: {project.get('created_by') or ''}")
    lines.append("---")
    lines.append("")
    lines.append("## Aufgaben")
    for t in project.get("tasks", []):
        check = "x" if t.get("completed") else " "
        parts = [
            f"id: {t.get('id', '')}",
            f"title: {t.get('title', '')}",
            f"user: {t.get('user', '')}",
            f"target-date: {t.get('target_date', '')}",
        ]
        if t.get("created_at"):
            parts.append(f"created_at: {t.get('created_at')}")
        if t.get("created_by"):
            parts.append(f"created_by: {t.get('created_by')}")
        if t.get("completed_at"):
            parts.append(f"completed_at: {t.get('completed_at')}")
        if t.get("completed_by"):
            parts.append(f"completed_by: {t.get('completed_by')}")
        lines.append(f"- [{check}] " + " | ".join(parts))
    lines.append("")
    lines.append("## Kommentare")
    for c in project.get("comments", []):
        lines.append(
            f"- id: {c.get('id', '')} | user: {c.get('user', '')} | "
            f"text: {c.get('text', '')} | created_at: {c.get('created_at', '')}"
        )
    lines.append("")
    return "\n".join(lines)


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


def _save_project(project):
    _ensure_dirs()
    pid = project.get("id")
    if not pid:
        pid = str(uuid.uuid4())
        project["id"] = pid
    if not _valid_id(pid):
        raise ValueError("Invalid Family project id")
    project["id"] = pid
    path = _project_path(pid)
    _atomic_write(path, serialize_project(project))
    return project


def set_task_completion(project_id, task_id, user_id, completed, can_access=None):
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
        if not _project_visible(project, user_id):
            return content, {"forbidden": True, "found": False, "task": None}
        for task in project.get("tasks", []):
            if task.get("id") != task_id:
                continue
            already_completed = task.get("completed") == completed
            if not already_completed:
                task["completed"] = completed
                task["completed_at"] = now.isoformat() if completed else None
                task["completed_by"] = user_id if completed else None
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
    title = (data.get("title") or "").strip()
    template_id = (data.get("template_id") or "").strip()
    target_file = data.get("target_file")
    assigned_users = data.get("assigned_users") or []
    if not title or not template_id:
        return jsonify({"error": "title and template_id required"}), 400
    now, _ = _get_tz_aware_now()
    project = {
        "id": str(uuid.uuid4()),
        "title": title,
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
        proj["title"] = data["title"]
    if "template_id" in data:
        proj["template_id"] = data["template_id"]
    if "target_file" in data:
        proj["target_file"] = data["target_file"]
    if "assigned_users" in data:
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

    result = set_task_completion(project_id, task_id, user["id"], completed)
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


@family_bp.route("/project/<project_id>/ai-suggest", methods=["POST"])
def family_project_ai_suggest(project_id):
    """Ruft einen AI-Provider auf und fuegt die vorgeschlagenen Artikel
    als neue Tasks zur Einkaufsliste hinzu.

    Konfiguration via config.json → "shopping_ai":
      {
        "ai_provider_id": "<id aus ai_providers>",
        "system_prompt":   "...",
        "max_tokens":      500,           # optional
        "temperature":     0.6            # optional
      }
    """
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_err = _check_csrf()
    if csrf_err:
        return csrf_err

    main = _main_module()
    config = main.load_config()

    shopping_ai = config.get("shopping_ai") or {}
    provider_id = shopping_ai.get("ai_provider_id")
    if not provider_id:
        return jsonify({"error": "shopping_ai.ai_provider_id ist nicht konfiguriert"}), 400

    provider = None
    for p in config.get("ai_providers", []):
        if p.get("id") == provider_id:
            provider = p
            break
    if not provider:
        return jsonify({"error": f"Unbekannter AI-Provider '{provider_id}'"}), 400

    system_prompt = shopping_ai.get("system_prompt", "")
    if not system_prompt:
        return jsonify({"error": "shopping_ai.system_prompt ist leer"}), 400

    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_visible(proj, user["id"]):
        return jsonify({"error": "Not found"}), 404

    bestehende = [t for t in proj.get("tasks", []) if not t.get("recurring")]
    # Index-basiert: KI bekommt nummerierte Items, gibt nur Indizes + ##-Header zurueck.
    # 1-basierte Indizes, wie sie in der user_message stehen.
    numbered = []
    for i, t in enumerate(bestehende, 1):
        title = (t.get("title", "") or "").strip()
        if title:
            numbered.append((i, t))

    user_message = (
        "Sortiere die folgende Einkaufsliste nach Rubriken.\n"
        "Du erhältst eine nummerierte Liste. Gib NUR die Zahlen in der neuen Reihenfolge zurück.\n"
        "Vor jede Rubrik schreibe '## <Rubrikname>' als Überschrift.\n"
        "Rubriken: Obst & Gemüse, Kühlung, Tiefkühlung, Non-Food, Konserven, Süßigkeiten, Sonstiges.\n"
        "Gib keine Artikel-Namen aus, nur die Zahlen.\n"
        "Keine neuen Artikel hinzufügen, keine Erklärungen.\n\n"
        "Aktuelle Einkaufsliste:\n"
        + ("\n".join(f"{i}. {t.get('title', '')}" for i, t in numbered) if numbered else "(noch leer)")
    )

    ai_function = {
        "system_prompt": system_prompt,
        "max_tokens": shopping_ai.get("max_tokens", 500),
        "temperature": shopping_ai.get("temperature", 0.7),
        "api_url": provider.get("api_url", ""),
        "api_key": provider.get("api_key", ""),
        "model": provider.get("model", ""),
    }

    try:
        ai_response = main._call_ai_api(provider, ai_function, user_message)
    except (ConnectionError, ValueError) as e:
        logger.error(f"shopping_ai error: {e}")
        return jsonify({"error": str(e)}), 500

    # AI-Response parsen (index-basiert):
    #   '## ...' oder '# ...' oder '**...**' → Gruppenname (Rubrik)
    #   Zeile mit Integer → Index eines bestehenden Items
    recurring_tasks = [t for t in proj.get("tasks", []) if t.get("recurring")]
    index_map = {i: t for i, t in numbered}

    neue_tasks = []
    current_gruppe = ""
    used_indices = set()
    sort_idx = 0

    for line in ai_response.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Gruppenüberschrift erkennen: '## X', '# X', '**X**'
        if stripped.startswith("#"):
            current_gruppe = stripped.lstrip("#").strip()
            continue
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            current_gruppe = stripped.strip("*").strip()
            continue
        # Integer-Index extrahieren (deckt '3', '3.', '3)', '- 3', '3. Item' ab)
        m = re.match(r"^\s*[-*]?\s*(\d+)", stripped)
        if m:
            idx = int(m.group(1))
            if idx in index_map and idx not in used_indices:
                used_indices.add(idx)
                t = index_map[idx]
                sort_idx += 1
                t["sort_index"] = sort_idx
                if current_gruppe:
                    t["gruppe"] = current_gruppe
                else:
                    t.pop("gruppe", None)
                neue_tasks.append(t)

    # Items, die die KI nicht erwähnt hat, hinten anhängen (gehen nicht verloren)
    for i, t in numbered:
        if i not in used_indices:
            sort_idx += 1
            t["sort_index"] = sort_idx
            t.pop("gruppe", None)
            neue_tasks.append(t)

    proj["tasks"] = recurring_tasks + neue_tasks
    _save_project(proj)

    return jsonify({
        "ok": True,
        "sorted_count": len(used_indices),
        "total_count": len(neue_tasks),
        "raw_response": ai_response,
    })


@family_bp.route("/project/<project_id>/raw", methods=["GET"])
def family_project_raw_get(project_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    proj = _load_project(project_id)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    if not _project_visible(proj, user["id"]):
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
    if not _project_visible(proj, user["id"]):
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if content is None or not isinstance(content, str):
        return jsonify({"error": "content (string) required"}), 400
    _ensure_dirs()
    path = _project_path(project_id)
    _atomic_write(path, content)
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
    for item in items:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        item_id = item.get("id")
        completed = bool(item.get("completed", False))
        if item_id and item_id in existing:
            t = existing[item_id]
            t["title"] = title
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
            new_tasks.append({
                "id": str(uuid.uuid4()),
                "title": title,
                "user": "",
                "target_date": "",
                "completed": completed,
                "created_at": now.isoformat(),
                "created_by": user["id"],
                "completed_at": now.isoformat() if completed else None,
                "completed_by": user["id"] if completed else None,
            })

    recurring_tasks = [t for t in proj.get("tasks", []) if t.get("source") == "recurring"]
    proj["tasks"] = new_tasks + recurring_tasks
    _save_project(proj)
    return jsonify({"ok": True, "count": len(new_tasks)})


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

    is_shopping = proj.get("template_id") == "einkaufsliste" or (proj.get("target_file") or "").endswith("einkaufsliste.md")
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
    for item in tasks_in:
        title = str(item.get("title", "")).strip()
        if not title:
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
            new_tasks.append({
                "id": str(uuid.uuid4()),
                "title": title,
                "user": user_val,
                "target_date": target_date,
                "completed": completed,
                "created_at": now.isoformat(),
                "created_by": user["id"],
                "completed_at": now.isoformat() if completed else None,
                "completed_by": user["id"] if completed else None,
            })

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


@family_bp.route("/search", methods=["GET"])
def family_search():
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify({"results": []})

    uid = user["id"]
    results = []

    def _matches_user_id_or_name(uid_or_name):
        if not uid_or_name:
            return False
        if q in uid_or_name.lower():
            return True
        name = _user_display(uid_or_name).lower()
        return q in name

    for path in _all_project_paths():
        proj = parse_project_file(path)
        if not proj:
            continue
        if not _project_visible(proj, uid):
            continue
        pid = proj.get("id")
        ptitle = proj.get("title") or ""
        if q in ptitle.lower():
            results.append({
                "project_id": pid,
                "project_title": ptitle,
                "task_id": None,
                "task_title": None,
                "match_field": "project_title",
            })
        for t in proj.get("tasks", []):
            ttitle = t.get("title") or ""
            matched = None
            if q in ttitle.lower():
                matched = "task_title"
            elif _matches_user_id_or_name(t.get("user") or ""):
                matched = "responsible"
            if matched:
                results.append({
                    "project_id": pid,
                    "project_title": ptitle,
                    "task_id": t.get("id"),
                    "task_title": ttitle,
                    "match_field": matched,
                })
        for c in proj.get("comments", []):
            text = c.get("text") or ""
            if q in text.lower():
                results.append({
                    "project_id": pid,
                    "project_title": ptitle,
                    "task_id": None,
                    "task_title": None,
                    "match_field": "comment",
                })
    return jsonify({"results": results})


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
    """Badge-Status {blue, red}.

    Blau: seit last_family_visit haben andere User sichtbare Items erstellt.
    Rot:  mindestens eine offene sichtbare Task ist heute oder früher fällig.
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

    blue = False
    red = False

    recurring = parse_recurring_tasks()

    for path in _all_project_paths():
        proj = parse_project_file(path)
        if not proj:
            continue
        if not _project_visible(proj, uid):
            continue
        # Blue: Projekt selbst
        if proj.get("created_by") and proj["created_by"] != uid:
            ca = _parse_iso(proj.get("created_at"))
            if ca and (last_visit is None or ca > last_visit):
                blue = True
        for t in proj.get("tasks", []):
            # Blue
            if t.get("created_by") and t["created_by"] != uid:
                ca = _parse_iso(t.get("created_at"))
                if ca and (last_visit is None or ca > last_visit):
                    blue = True
            # Red: offene Task heute faellig
            if not t.get("completed") and t.get("target_date"):
                td = t["target_date"].strip()
                if td <= today_str:
                    red = True
    for task in recurring:
        if task.get("created_by") and task["created_by"] != uid:
            created_at = _parse_iso(task.get("created_at"))
            if created_at and (last_visit is None or created_at > last_visit):
                blue = True
        if not task.get("completed") and task.get("target_date"):
            if task["target_date"].strip() <= today_str:
                red = True

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
        "template_id": proj.get("template_id"),
        "target_file": proj.get("target_file") or None,
        "assigned_users": proj.get("assigned_users", []),
        "created_at": proj.get("created_at"),
        "created_by": proj.get("created_by"),
        "created_by_display": _user_display(proj.get("created_by")),
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
def append_task_to_target_file(target_file, title, user_uid, target_date="", created_by=None):
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
    task = {
        "id": str(uuid.uuid4()),
        "title": title,
        "user": user_uid or "",
        "target_date": target_date or "",
        "completed": False,
        "created_at": now.isoformat(),
        "created_by": created_by or user_uid,
    }
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
