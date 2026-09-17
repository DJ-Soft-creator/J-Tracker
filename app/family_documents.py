"""Family document persistence for the Aufgaben/Projekte/Notizen subtabs.

AI maintenance contract:
Own the file-level operations the Familie tab needs beyond plain project CRUD:
the per-user personal bucket, the aggregated board payload, project completion
into the archive, appending to a chosen shopping list and the per-user pin
store. Keep the pure vocabulary (types, ordering, permission predicates) in
family_views.py and HTTP orchestration in the route modules.

Loaded by family.py before the route modules, so every parser, serializer and
write helper of the facade is available here. Test-replaceable paths are
synchronized from the facade on each public call.
"""

import re
import sys
import uuid

_family = sys.modules.get("family")
if _family is None:  # pragma: no cover - enforced by family.py import order
    raise RuntimeError("family facade must exist before family_documents is imported")
globals().update({name: getattr(_family, name) for name in dir(_family) if not name.startswith("__")})

_DYNAMIC_NAMES = (
    "DATA_DIR", "FAMILY_DIR", "PROJECTS_DIR", "PLANNER_DIR", "ARCHIVE_DIR",
    "ARCHIVED_PROJECTS_DIR", "PLANNER_FILE", "FAMILY_TASKS_FILE",
    "_load_project", "_save_project", "_ensure_dirs", "_current_user",
    "_get_tz_aware_now", "_project_path",
    # Append-Helfer bewusst dynamisch: Tests patchen sie auf der Fassade, und
    # ein bei Import kopierter Verweis wuerde diese Patches ins Leere laufen.
    "append_task_to_target_file", "append_task_to_project",
)


def _sync_runtime():
    for name in _DYNAMIC_NAMES:
        if hasattr(_family, name):
            globals()[name] = getattr(_family, name)


# ─── Persoenlicher Bucket ─────────────────────────────────────────────
def ensure_personal_bucket(user):
    """Return the user's own bucket, creating it on first access.

    Created lazily instead of by migration: existing installations get their
    personal buckets simply by opening the Familie tab, and a deleted file
    heals itself. Mirrors the existing lazy materialization of due tasks.
    """
    _sync_runtime()
    _ensure_dirs()
    user_id = user["id"]
    bucket_id = family_views.personal_bucket_id(user_id)
    existing = _load_project(bucket_id)
    if existing and _project_integrity_valid(existing):
        return existing
    if existing:
        # Kaputte Datei nicht ueberschreiben: sonst gehen eigene Aufgaben
        # unbemerkt verloren. Fail closed und sichtbar machen.
        raise ValueError(f"Persoenlicher Bucket {bucket_id} ist beschaedigt")
    now, _ = _get_tz_aware_now()
    bucket = family_views.new_personal_bucket(
        user_id,
        _user_display(user_id) or user.get("username") or "",
        now.isoformat(),
    )
    _save_project(bucket, touch=False)
    return bucket


# ─── Dokumente laden ──────────────────────────────────────────────────
def load_documents(user_id):
    """All Family documents as ``(project, accessible)`` pairs.

    Inaccessible documents stay in the list on purpose: tasks assigned to the
    requesting user are surfaced from them, with metadata only.
    """
    _sync_runtime()
    documents = []
    for path in _all_project_paths():
        project = parse_project_file(path)
        if not project or not _project_integrity_valid(project):
            continue
        documents.append((project, _project_visible(project, user_id)))
    return documents


def visible_documents(user_id, document_type=None):
    """Accessible documents, optionally filtered by type."""
    wanted = family_views.normalize_type(document_type) if document_type else None
    result = []
    for project, accessible in load_documents(user_id):
        if not accessible:
            continue
        if wanted and family_views.normalize_type(project.get("type")) != wanted:
            continue
        result.append(project)
    return result


def shopping_lists(user_id):
    """Visible buckets that behave as shopping lists, newest edit first."""
    lists = [
        project for project in visible_documents(user_id, family_views.TYPE_BUCKET)
        if _is_shopping_project(project)
    ]
    return family_views.sort_by_last_edit(lists)


# ─── Board ────────────────────────────────────────────────────────────
def _decorate(project, user):
    document = _sanitize_project(project)
    document["can_delete"] = family_views.can_delete(project, user)
    document["can_change_visibility"] = family_views.can_change_visibility(user)
    document["can_edit"] = True
    return document


def build_board(user):
    """Payload of the Familie tab: three subtabs plus the aggregate buckets."""
    _sync_runtime()
    _ensure_dirs()
    user_id = user["id"]
    personal = ensure_personal_bucket(user)
    documents = load_documents(user_id)

    recurring_tasks = parse_recurring_tasks()
    if recurring_tasks:
        documents.append((_recurring_project(recurring_tasks), True))

    own_bucket_id = personal.get("id")
    linked = family_views.collect_assigned_tasks(documents, user_id, own_bucket_id)
    unassigned = family_views.collect_unassigned_tasks(documents, user_id)

    personal_view = _sanitize_project(
        family_views.build_personal_view(personal, linked, _user_display(user_id))
    )
    personal_view.update({"personal": True, "can_delete": False, "can_edit": True})

    unassigned_source = family_views.build_unassigned_view(unassigned)
    unassigned_view = _sanitize_project(unassigned_source) if unassigned_source else None
    if unassigned_view:
        unassigned_view.update({"aggregate": True, "can_delete": False, "can_edit": False})

    pins = user.get("family_pins") if isinstance(user.get("family_pins"), dict) else {}
    scopes = {}
    for document_type, scope in family_views.SCOPE_BY_TYPE.items():
        entries = [
            _decorate(project, user)
            for project, accessible in documents
            if accessible
            and project.get("id") != own_bucket_id
            and family_views.normalize_type(project.get("type")) == document_type
        ]
        scopes[scope] = family_views.order_documents(
            entries, family_views.normalize_pins(pins, scope)
        )

    return {
        "aufgaben": scopes[family_views.SCOPE_BY_TYPE[family_views.TYPE_BUCKET]],
        "projekte": scopes[family_views.SCOPE_BY_TYPE[family_views.TYPE_PROJECT]],
        "notizen": scopes[family_views.SCOPE_BY_TYPE[family_views.TYPE_NOTE]],
        "personal": personal_view,
        "unassigned": unassigned_view,
        "is_admin": family_views.is_admin(user),
        "users": [
            {"id": item.get("id"), "display": item.get("username") or item.get("id")}
            for item in _main_module().get_all_users()
        ],
    }


# ─── Pins (pro Benutzer, nicht in der gemeinsamen Datei) ──────────────
def save_pins(user, scope, ids):
    """Persist one subtab's pin order on the user object.

    Deliberately user state, not document state: the order is personal. The
    store only ever holds document ids, so a lost update costs a preference,
    never content.
    """
    if scope not in family_views.VALID_SCOPES:
        raise ValueError("Unbekannter Bereich")
    if not isinstance(ids, list):
        raise ValueError("ids (Liste) erforderlich")
    cleaned = []
    for value in ids:
        if not isinstance(value, str) or not _valid_id(value):
            raise ValueError("Ungültige Dokument-ID")
        if value not in cleaned:
            cleaned.append(value)
    if len(cleaned) > 20:
        raise ValueError("Zu viele angeheftete Einträge")
    pins = user.get("family_pins")
    pins = dict(pins) if isinstance(pins, dict) else {}
    pins[scope] = cleaned
    user["family_pins"] = pins
    _main_module().update_user(user)
    return cleaned


# ─── Projektabschluss ─────────────────────────────────────────────────
def complete_project(project_id, user):
    """Move a finished project into the archive.

    Two files are touched, so the copy is written first and only removed again
    if unlinking the original fails. A partial state is reported instead of
    being swallowed, because it would otherwise duplicate the project.
    """
    _sync_runtime()
    _ensure_dirs()
    project = _load_project(project_id)
    if not project or not _project_visible(project, user["id"]):
        raise LookupError("Not found")
    if family_views.normalize_type(project.get("type")) != family_views.TYPE_PROJECT:
        raise ValueError("Nur Projekte können abgeschlossen werden")
    now, _ = _get_tz_aware_now()
    project["archived_at"] = now.isoformat()
    project["updated_at"] = now.isoformat()
    target = ARCHIVED_PROJECTS_DIR / f"{project_id}.md"
    source = _project_path(project_id)
    _atomic_write(target, serialize_project(project))
    try:
        source.unlink(missing_ok=True)
    except OSError:
        target.unlink(missing_ok=True)
        raise
    return project


# ─── Aufgabe an eine gewaehlte Liste anhaengen ────────────────────────
def append_task_to_project(project_id, title, user_uid, created_by, group="", target_date=""):
    """Append one task to an explicitly chosen, visible document.

    Used by the Einkaufsliste template, which now picks its target list at
    write time instead of relying on a single template-wide ``target_file``.
    """
    _sync_runtime()
    _ensure_dirs()
    path = _project_path(project_id)
    # Der Planner-Bucket ist virtuell: ein Append wuerde eine Datei anlegen,
    # die der Scheduler nie wieder anfasst.
    if path is None or project_id == RECURRING_PROJECT_ID or not path.exists():
        raise LookupError("Not found")
    now, _ = _get_tz_aware_now()
    clean_title = family_views.validate_title(title, "Eintrag")

    def append(content):
        project = parse_project_content(content, path.name)
        if not _project_visible(project, created_by):
            return content, {"forbidden": True}
        existing_order = [
            task.get("sort_index") for task in project.get("tasks", [])
            if isinstance(task.get("sort_index"), int)
        ]
        task = {
            "id": str(uuid.uuid4()),
            "title": clean_title,
            "user": user_uid or "",
            "target_date": target_date or "",
            "completed": False,
            "created_at": now.isoformat(),
            "created_by": created_by,
        }
        if _is_shopping_project(project):
            task["gruppe"] = str(group or "Unsortiert").strip() or "Unsortiert"
            task["sort_index"] = max(existing_order, default=0) + 1
        project.setdefault("tasks", []).append(task)
        project["updated_at"] = now.isoformat()
        return serialize_project(project), {"task": task}

    result = update_text_file(path, append) or {}
    if result.get("forbidden") or not result.get("task"):
        raise LookupError("Not found")
    return result["task"]


_SHOPPING_HEADING_RE = re.compile(r"#{1,6}\s+.+")
_SHOPPING_BULLET_RE = re.compile(r"^(?:[-*•·]|\d+[.)])\s*")


def parse_shopping_lines(text):
    """Split pasted shopping text into ``(category, item)`` pairs.

    The input can come from the old form field or from a whole sorted list in
    the writing editor, so Markdown headings and bold lines are read as
    category separators rather than as items.
    """
    category = "Unsortiert"
    items = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _SHOPPING_HEADING_RE.fullmatch(line):
            heading = line.lstrip("#").strip()
            category = "Unsortiert" if heading.casefold() == "einkaufsliste" else heading
            continue
        if line.startswith("**") and line.endswith("**") and len(line) > 4:
            category = line.strip("*").strip()
            continue
        item = _SHOPPING_BULLET_RE.sub("", line).strip()
        if item:
            items.append((category, item))
    return items


def import_shopping_lines(text, target_project, target_file, user_uid, created_by):
    """Append pasted shopping lines to the chosen list.

    ``target_project`` is the list the user picked while writing. It is only a
    proposal: ``append_task_to_project`` re-checks visibility, so an unknown or
    restricted id raises instead of writing. Without a choice the historic
    single ``target_file`` list stays the target.
    """
    _sync_runtime()
    added = 0
    for group, title in parse_shopping_lines(text):
        if target_project:
            append_task_to_project(
                project_id=target_project,
                title=title,
                user_uid=user_uid,
                created_by=created_by,
                group=group,
            )
        else:
            append_task_to_target_file(
                target_file=target_file,
                title=title,
                user_uid=user_uid,
                target_date="",
                created_by=created_by,
                group=group,
            )
        added += 1
    return added


__all__ = [
    "ensure_personal_bucket", "load_documents", "visible_documents",
    "shopping_lists", "build_board", "save_pins", "complete_project",
    "append_task_to_project", "parse_shopping_lines", "import_shopping_lines",
]
