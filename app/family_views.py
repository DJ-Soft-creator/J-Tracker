"""Family document types, aggregation and per-user ordering.

AI maintenance contract:
Own the vocabulary of the Familie tab — document types (bucket/projekt/notiz),
the personal and unassigned aggregate buckets, per-user pin ordering and the
permission predicates that belong to those concepts. Keep every function here
pure: callers pass already parsed project dicts, users and pins. No file IO, no
HTTP, no Flask imports, so both route modules and the migration script can use
one implementation.

Terminology (see docs/project/Anforderung-Familie-Tab-Neuaufbau.md, section 2):
the UI calls a task list a *Bucket*; on disk and in the HTTP API it stays a
Family *project* (``family/projects/<id>.md``, ``/api/family/project*``). The
``type`` front matter field is the only distinction between the three subtabs.
"""

TYPE_BUCKET = "bucket"
TYPE_PROJECT = "projekt"
TYPE_NOTE = "notiz"
VALID_TYPES = (TYPE_BUCKET, TYPE_PROJECT, TYPE_NOTE)

# Subtab keys used by the client and by the per-user pin store.
SCOPE_BY_TYPE = {
    TYPE_BUCKET: "aufgaben",
    TYPE_PROJECT: "projekte",
    TYPE_NOTE: "notizen",
}
VALID_SCOPES = tuple(SCOPE_BY_TYPE.values())

PERSONAL_TEMPLATE_ID = "persoenlich"
PERSONAL_BUCKET_PREFIX = "user-"
UNASSIGNED_BUCKET_ID = "__unassigned__"

MAX_TITLE_LENGTH = 200
_FORBIDDEN_TITLE_CHARS = ("|", "\n", "\r")


def normalize_type(value):
    """Map any stored value to a known type.

    Files written before the Familie-Tab rework carry no ``type`` at all. They
    are task lists, so an unknown or missing value must resolve to ``bucket``
    instead of hiding the document from every subtab.
    """
    cleaned = str(value or "").strip().casefold()
    return cleaned if cleaned in VALID_TYPES else TYPE_BUCKET


def scope_for_type(value):
    return SCOPE_BY_TYPE[normalize_type(value)]


def validate_title(value, label="Titel"):
    """Return a title that survives the ``|``-delimited Markdown schema."""
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{label} fehlt")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise ValueError(f"{label} ist zu lang (max. {MAX_TITLE_LENGTH} Zeichen)")
    for char in _FORBIDDEN_TITLE_CHARS:
        if char in cleaned:
            raise ValueError(f"{label} enthält ungültige Zeichen")
    return cleaned


def personal_bucket_id(user_id):
    """Stable id of a user's own bucket.

    User ids are UUIDs (or short test ids); both stay inside the 128 character
    id limit and contain no path separators.
    """
    return f"{PERSONAL_BUCKET_PREFIX}{user_id}"


def personal_bucket_owner(project):
    """Return the owner id of a personal bucket, else ``None``."""
    if not project:
        return None
    owner = str(project.get("owner") or "").strip()
    if not owner:
        return None
    return owner if project.get("id") == personal_bucket_id(owner) else None


def is_personal_bucket(project):
    return personal_bucket_owner(project) is not None


def is_admin(user):
    return bool(user) and user.get("admin") is True


def can_change_visibility(user):
    """Only administrators assign documents to users (D-011)."""
    return is_admin(user)


def can_delete(project, user):
    """Creator or admin may delete; a personal bucket is never deletable."""
    if not project or not user:
        return False
    if is_personal_bucket(project):
        return False
    if is_admin(user):
        return True
    return bool(project.get("created_by")) and project.get("created_by") == user.get("id")


def last_edit(document):
    """Timestamp the ordering uses; empty string when nothing is recorded."""
    return str(document.get("updated_at") or document.get("created_at") or "")


def sort_by_last_edit(documents):
    """Newest edit first, equal timestamps alphabetically, unstamped last.

    Two stable passes instead of one inverted key: sorting ISO timestamps
    descending and titles ascending cannot be expressed in a single key.
    """
    ordered = sorted(documents, key=lambda item: str(item.get("title") or "").casefold())
    ordered.sort(key=last_edit, reverse=True)
    return ordered


def normalize_pins(raw, scope):
    """Read one scope out of the per-user pin store, tolerating old shapes."""
    if not isinstance(raw, dict):
        return []
    values = raw.get(scope)
    if not isinstance(values, list):
        return []
    seen = set()
    pins = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        pins.append(cleaned)
    return pins


def order_documents(documents, pinned_ids):
    """Pinned documents in the user's order first, then newest edit first."""
    by_id = {}
    for document in documents:
        document_id = document.get("id")
        if document_id is not None:
            by_id.setdefault(document_id, document)
    pinned = [by_id[pin] for pin in pinned_ids if pin in by_id]
    pinned_set = {document.get("id") for document in pinned}
    rest = sort_by_last_edit(
        [document for document in documents if document.get("id") not in pinned_set]
    )
    for document in pinned:
        document["pinned"] = True
    for document in rest:
        document["pinned"] = False
    return pinned + rest


def _origin_task(task, source_document, accessible):
    """Copy a task and record where it really lives.

    ``accessible`` is False when the task is only visible because it is
    assigned to the requesting user. The Familie tab then shows title, date,
    state and the origin title, but must not offer to open the document.
    """
    enriched = dict(task)
    enriched["origin_id"] = source_document.get("id")
    enriched["origin_title"] = source_document.get("title") or ""
    enriched["origin_accessible"] = bool(accessible)
    return enriched


def collect_assigned_tasks(documents, user_id, own_bucket_id=None):
    """Open tasks assigned to ``user_id`` across every Family document.

    ``documents`` is a list of ``(document, accessible)`` pairs covering *all*
    documents, including those the user may not open. A task assigned to the
    user is deliberately surfaced anyway (decision 2 of the requirement); the
    caller only exposes task metadata plus the origin title.

    Documents flagged ``exclude_from_personal`` stay out entirely. Shopping
    lists carry that flag by default, because their items are goods, not
    personal tasks, and would otherwise flood the personal bucket.
    """
    collected = []
    for document, accessible in documents:
        if document.get("id") == own_bucket_id:
            continue
        if document.get("exclude_from_personal"):
            continue
        for task in document.get("tasks", []):
            if task.get("completed"):
                continue
            if (task.get("user") or "") != user_id:
                continue
            collected.append(_origin_task(task, document, accessible))
    return collected


RECENTLY_COMPLETED_LIMIT = 30


def collect_recently_completed(documents, user_id, limit=RECENTLY_COMPLETED_LIMIT):
    """Completed tasks that are not archived yet, newest first.

    Lets the Familie tab undo an accidental check even after a reload: the
    aggregate buckets drop completed tasks, so without this list a task
    checked there could only be reset in its origin document - or not at all
    when that document is not shared. Same visibility as the board: tasks
    from accessible documents plus tasks assigned to the user. Shopping lists
    (``exclude_from_personal``) stay out; their items are goods, not todos.
    """
    collected = []
    for document, accessible in documents:
        if document.get("exclude_from_personal"):
            continue
        for task in document.get("tasks", []):
            if not task.get("completed") or not task.get("id"):
                continue
            if not accessible and (task.get("user") or "") != user_id:
                continue
            collected.append(_origin_task(task, document, accessible))
    collected.sort(key=lambda task: str(task.get("completed_at") or ""), reverse=True)
    return collected[:limit]


def collect_unassigned_tasks(documents, user_id):
    """Open tasks without an assignee from documents the user may open.

    Honours ``exclude_from_personal`` as well: a bucket kept out of "Meine
    Aufgaben" should not reappear through the shared bucket either.
    """
    collected = []
    for document, accessible in documents:
        if not accessible or document.get("exclude_from_personal"):
            continue
        for task in document.get("tasks", []):
            if task.get("completed") or (task.get("user") or ""):
                continue
            collected.append(_origin_task(task, document, True))
    return collected


def _aggregate_document(bucket_id, title, tasks, extra=None):
    aggregate = {
        "id": bucket_id,
        "title": title,
        "type": TYPE_BUCKET,
        "aggregate": True,
        "tasks": tasks,
        "comments": [],
        "assigned_users": [],
        "template_id": "",
        "target_file": None,
        "can_delete": False,
        "can_edit": False,
        "pinned": False,
    }
    if extra:
        aggregate.update(extra)
    return aggregate


def build_personal_view(own_bucket, linked_tasks, display_name):
    """Merge a user's own bucket with the tasks assigned to them elsewhere.

    The own bucket keeps its identity so new personal tasks have a real file to
    live in; linked tasks stay owned by their source document.
    """
    tasks = []
    for task in (own_bucket or {}).get("tasks", []):
        tasks.append(_origin_task(task, own_bucket, True))
    tasks.extend(linked_tasks)
    view = _aggregate_document(
        (own_bucket or {}).get("id"),
        f"Meine Aufgaben ({display_name})" if display_name else "Meine Aufgaben",
        tasks,
        {
            "aggregate": False,
            "personal": True,
            "can_edit": True,
            "template_id": PERSONAL_TEMPLATE_ID,
        },
    )
    return view


def build_unassigned_view(tasks):
    """Return the shared bucket, or ``None`` while it holds nothing."""
    if not tasks:
        return None
    return _aggregate_document(
        UNASSIGNED_BUCKET_ID,
        "Nicht zugewiesen",
        tasks,
        {"personal": False},
    )


def new_personal_bucket(user_id, display_name, now_iso):
    """Skeleton of a personal bucket, ready for the normal serializer."""
    return {
        "id": personal_bucket_id(user_id),
        "title": f"Aufgaben {display_name}" if display_name else "Meine Aufgaben",
        "type": TYPE_BUCKET,
        "template_id": PERSONAL_TEMPLATE_ID,
        "target_file": "",
        "assigned_users": [user_id],
        "owner": user_id,
        "created_at": now_iso,
        "created_by": user_id,
        "updated_at": now_iso,
        "tasks": [],
        "comments": [],
    }


__all__ = [
    "TYPE_BUCKET", "TYPE_PROJECT", "TYPE_NOTE", "VALID_TYPES",
    "SCOPE_BY_TYPE", "VALID_SCOPES", "PERSONAL_TEMPLATE_ID",
    "PERSONAL_BUCKET_PREFIX", "UNASSIGNED_BUCKET_ID", "MAX_TITLE_LENGTH",
    "normalize_type", "scope_for_type", "validate_title", "personal_bucket_id",
    "personal_bucket_owner", "is_personal_bucket", "is_admin",
    "can_change_visibility", "can_delete", "last_edit", "sort_by_last_edit",
    "normalize_pins",
    "order_documents", "collect_assigned_tasks", "collect_unassigned_tasks",
    "build_personal_view", "build_unassigned_view", "new_personal_bucket",
]
