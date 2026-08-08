"""Shared scheduling logic for recurring family tasks.

The Flask app and the standalone scheduler use this module so both read and
write exactly the same Markdown format.
"""

import fcntl
import math
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path


RECURRENCE_OPTIONS = (
    {"value": "once", "label": "Einmalig"},
    {"value": "daily", "label": "Jeden Tag"},
    {"value": "weekly_monday", "label": "Jeden Montag"},
    {"value": "weekly_tuesday", "label": "Jeden Dienstag"},
    {"value": "weekly_wednesday", "label": "Jeden Mittwoch"},
    {"value": "weekly_thursday", "label": "Jeden Donnerstag"},
    {"value": "weekly_friday", "label": "Jeden Freitag"},
    {"value": "weekly_saturday", "label": "Jeden Samstag"},
    {"value": "weekly_sunday", "label": "Jeden Sonntag"},
    {"value": "biweekly", "label": "Alle 2 Wochen"},
    {"value": "monthly_first", "label": "Am 1. jedes Monats"},
)
RECURRENCE_LABELS = {item["value"]: item["label"] for item in RECURRENCE_OPTIONS}
VALID_RECURRENCES = frozenset(RECURRENCE_LABELS)

_WEEKDAYS = {
    "weekly_monday": 0,
    "weekly_tuesday": 1,
    "weekly_wednesday": 2,
    "weekly_thursday": 3,
    "weekly_friday": 4,
    "weekly_saturday": 5,
    "weekly_sunday": 6,
}
_TASK_LINE_RE = re.compile(r"^-\s*\[(?P<check>[ xX]?)\]\s*(?P<body>.+)$")
_PLANNER_FIELDS = {
    "id", "title", "user", "recurrence", "start_date", "target_date",
    "target-date", "active", "created_at", "created_by", "updated_at", "updated_by",
}


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value


def _parse_metadata(value):
    result = {}
    for part in re.split(r"\s*\|\s*", value.strip()):
        if ":" not in part:
            continue
        key, raw_value = part.split(":", 1)
        result[key.strip()] = _unquote(raw_value)
    return result


def _parse_bool(value, default=True):
    if value is None or value == "":
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "paused"}


def _date_from_value(value):
    if not value:
        return None
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return None


def parse_planner(content):
    """Parse planner definitions, including the legacy five-field format."""
    items = []
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("-"):
            continue
        metadata = _parse_metadata(line[1:].strip())
        if not metadata.get("id") or not metadata.get("recurrence"):
            continue
        created_at = metadata.get("created_at", "")
        start_date = (
            metadata.get("start_date")
            or metadata.get("target_date")
            or metadata.get("target-date")
        )
        if not start_date and _date_from_value(created_at):
            start_date = created_at[:10]
        items.append({
            "id": metadata.get("id", ""),
            "title": metadata.get("title", ""),
            "user": metadata.get("user", ""),
            "recurrence": metadata.get("recurrence", ""),
            "start_date": start_date or "",
            "active": _parse_bool(metadata.get("active"), default=True),
            "created_at": created_at,
            "created_by": metadata.get("created_by", ""),
            "updated_at": metadata.get("updated_at", ""),
            "updated_by": metadata.get("updated_by", ""),
            "_extra": {
                key: value for key, value in metadata.items() if key not in _PLANNER_FIELDS
            },
        })
    return items


def serialize_planner_item(item):
    parts = [
        f"id: {item.get('id', '')}",
        f"title: {item.get('title', '')}",
        f"user: {item.get('user', '')}",
        f"recurrence: {item.get('recurrence', '')}",
        f"start_date: {item.get('start_date', '')}",
        f"active: {'true' if item.get('active', True) else 'false'}",
    ]
    for key in ("created_at", "created_by", "updated_at", "updated_by"):
        if item.get(key):
            parts.append(f"{key}: {item[key]}")
    for key, value in (item.get("_extra") or {}).items():
        if key not in _PLANNER_FIELDS and value != "":
            parts.append(f"{key}: {value}")
    return "- " + " | ".join(parts)


def serialize_planner(items):
    lines = [serialize_planner_item(item) for item in items]
    return "\n".join(lines) + ("\n" if lines else "")


def append_planner_item(content, item):
    prefix = content or ""
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + serialize_planner_item(item) + "\n"


def replace_planner_item(content, plan_id, item=None):
    """Replace or remove one plan line while preserving the rest of the file."""
    lines = (content or "").splitlines()
    replaced = False
    output = []
    for line in lines:
        parsed = parse_planner(line)
        if not replaced and parsed and parsed[0].get("id") == plan_id:
            replaced = True
            if item is not None:
                output.append(serialize_planner_item(item))
            continue
        output.append(line)
    suffix = "\n" if (content or "").endswith("\n") and output else ""
    return "\n".join(output) + suffix, replaced


def parse_recurring_task_line(line):
    """Parse canonical ``[ ]`` and legacy ``[]`` recurring task lines."""
    match = _TASK_LINE_RE.match((line or "").strip())
    if not match:
        return None
    metadata = _parse_metadata(match.group("body"))
    if not metadata.get("id") or not metadata.get("title"):
        return None
    source = metadata.get("source", "recurring")
    if source != "recurring":
        return None
    return {
        "id": metadata.get("id", ""),
        "title": metadata.get("title", ""),
        "user": metadata.get("user", ""),
        "target_date": metadata.get("target-date", metadata.get("target_date", "")),
        "completed": match.group("check").lower() == "x",
        "source": "recurring",
        "recurrence": metadata.get("recurrence", ""),
        "plan_id": metadata.get("plan_id", ""),
        "created_at": metadata.get("created_at", ""),
        "created_by": metadata.get("created_by", ""),
        "completed_at": metadata.get("completed_at") or None,
        "completed_by": metadata.get("completed_by") or None,
    }


def parse_recurring_tasks(content):
    tasks = []
    for line in (content or "").splitlines():
        task = parse_recurring_task_line(line)
        if task:
            tasks.append(task)
    return tasks


def serialize_recurring_task(item):
    check = "x" if item.get("completed") else " "
    parts = [
        f"id: {item.get('id', '')}",
        f"title: {item.get('title', '')}",
        f"user: {item.get('user', '')}",
        f"target-date: {item.get('target_date', '')}",
        "source: recurring",
        f"recurrence: {item.get('recurrence', '')}",
    ]
    for key in ("plan_id", "created_at", "created_by", "completed_at", "completed_by"):
        if item.get(key):
            parts.append(f"{key}: {item[key]}")
    return f"- [{check}] " + " | ".join(parts)


def _anchor_date(item):
    return _date_from_value(item.get("start_date")) or _date_from_value(item.get("created_at"))


def is_due_on(item, day):
    if not item.get("active", True):
        return False
    anchor = _anchor_date(item)
    if not anchor or day < anchor:
        return False
    recurrence = (item.get("recurrence") or "").strip()
    if recurrence == "once":
        return day == anchor
    if recurrence == "daily":
        return True
    if recurrence in _WEEKDAYS:
        return day.weekday() == _WEEKDAYS[recurrence]
    if recurrence == "biweekly":
        return (day - anchor).days % 14 == 0
    if recurrence == "monthly_first":
        return day.day == 1
    return False


def next_due_date(item, from_date):
    """Return the next due date on or after ``from_date``."""
    if not item.get("active", True):
        return None
    anchor = _anchor_date(item)
    if not anchor:
        return None
    start = max(anchor, from_date)
    recurrence = (item.get("recurrence") or "").strip()
    if recurrence == "once":
        return anchor if anchor >= from_date else None
    if recurrence == "daily":
        return start
    if recurrence in _WEEKDAYS:
        return start + timedelta(days=(_WEEKDAYS[recurrence] - start.weekday()) % 7)
    if recurrence == "biweekly":
        elapsed = (start - anchor).days
        cycles = max(0, math.ceil(elapsed / 14))
        return anchor + timedelta(days=cycles * 14)
    if recurrence == "monthly_first":
        candidate = date(start.year, start.month, 1)
        if candidate < start:
            if start.month == 12:
                candidate = date(start.year + 1, 1, 1)
            else:
                candidate = date(start.year, start.month + 1, 1)
        return candidate
    return None


@contextmanager
def _path_lock(path, exclusive):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def path_lock(path, exclusive):
    """Expose the stable ``<path>.lock`` protocol to related storage modules."""
    return _path_lock(path, exclusive)


@contextmanager
def scheduler_guard(planner_path):
    """Serialize planner changes with task materialization."""
    guard_path = Path(planner_path).parent / ".scheduler"
    with _path_lock(guard_path, exclusive=True):
        yield


def _read_unlocked(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _atomic_write_unlocked(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_stat = path.stat() if path.exists() else None
    file_mode = stat.S_IMODE(existing_stat.st_mode) if existing_stat else 0o660
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temp_file:
            temp_name = temp_file.name
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.chmod(temp_name, file_mode)
        if existing_stat and hasattr(os, "chown") and os.geteuid() == 0:
            os.chown(temp_name, existing_stat.st_uid, existing_stat.st_gid)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def read_text_file(path):
    with _path_lock(path, exclusive=False):
        return _read_unlocked(path)


def update_text_file(path, updater):
    """Run a read-modify-write transaction protected by a stable lock file.

    ``updater`` returns either new content or ``(new content, result)``.
    """
    with _path_lock(path, exclusive=True):
        current = _read_unlocked(path)
        updated = updater(current)
        result = None
        if isinstance(updated, tuple):
            updated, result = updated
        if updated != current:
            _atomic_write_unlocked(path, updated)
        return result


def write_text_file(path, content):
    update_text_file(path, lambda _current: content)


def ensure_text_file(path):
    """Create an empty file under lock without touching an existing file."""
    path = Path(path)
    with _path_lock(path, exclusive=True):
        if not path.exists():
            _atomic_write_unlocked(path, "")


def _latest_materialization_date(item, today):
    if not item.get("active", True):
        return None
    anchor = _anchor_date(item)
    if not anchor or today < anchor:
        return None
    recurrence = (item.get("recurrence") or "").strip()
    if recurrence == "once":
        return anchor
    if recurrence == "daily":
        return today
    if recurrence in _WEEKDAYS:
        candidate = today - timedelta(days=(today.weekday() - _WEEKDAYS[recurrence]) % 7)
        return candidate if candidate >= anchor else None
    if recurrence == "biweekly":
        elapsed = (today - anchor).days
        return anchor + timedelta(days=(elapsed // 14) * 14)
    if recurrence == "monthly_first":
        candidate = date(today.year, today.month, 1)
        return candidate if candidate >= anchor else None
    return None


def materialize_due_tasks(planner_path, tasks_path, now):
    """Create the latest due task instance exactly once.

    Missed weekly, biweekly and monthly occurrences are caught up as overdue;
    daily plans intentionally create only today's instance.
    """
    with scheduler_guard(planner_path):
        plans = parse_planner(read_text_file(planner_path))
        today = now.date()
        due_plans = []
        for item in plans:
            target_date = _latest_materialization_date(item, today)
            if target_date:
                due_plans.append((item, target_date))

        archive_dir = Path(tasks_path).parent / "archive"
        archived_ids = {
            path.stem for path in archive_dir.glob("*.md")
        } if archive_dir.is_dir() else set()

        def append_due(content):
            existing_ids = {task["id"] for task in parse_recurring_tasks(content)}
            existing_ids.update(archived_ids)
            lines = []
            added_ids = []
            for plan, target in due_plans:
                target_string = target.isoformat()
                task_id = f"{plan['id']}-{target_string}"
                if task_id in existing_ids:
                    continue
                task = {
                    "id": task_id,
                    "title": plan.get("title", ""),
                    "user": plan.get("user", ""),
                    "target_date": target_string,
                    "completed": False,
                    "recurrence": plan.get("recurrence", ""),
                    "plan_id": plan.get("id", ""),
                    "created_at": now.isoformat(),
                    "created_by": plan.get("created_by", ""),
                }
                lines.append(serialize_recurring_task(task))
                added_ids.append(task_id)
                existing_ids.add(task_id)
            if not lines:
                return content, []
            prefix = content
            if prefix and not prefix.endswith("\n"):
                prefix += "\n"
            return prefix + "\n".join(lines) + "\n", added_ids

        added_ids = update_text_file(tasks_path, append_due) or []
        return {
            "date": today.isoformat(),
            "due": len(due_plans),
            "added": len(added_ids),
            "task_ids": added_ids,
        }


def complete_recurring_task(tasks_path, task_id, completed_by, completed_at):
    """Complete a recurring task idempotently (first completion wins)."""
    return set_recurring_task_completion(tasks_path, task_id, completed_by, completed_at, True)


def set_recurring_task_completion(tasks_path, task_id, completed_by, completed_at, completed, can_access=None):
    """Set a recurring task's state while retaining its completion audit fields."""
    def update(content):
        if can_access and not can_access(content):
            return content, {"forbidden": True, "found": False, "task": None}
        lines = content.splitlines()
        for index, line in enumerate(lines):
            task = parse_recurring_task_line(line)
            if not task or task.get("id") != task_id:
                continue
            if task.get("completed") == completed:
                return content, {"found": True, "already_completed": completed, "task": task}
            task["completed"] = completed
            task["completed_at"] = completed_at if completed else None
            task["completed_by"] = completed_by if completed else None
            lines[index] = serialize_recurring_task(task)
            suffix = "\n" if content.endswith("\n") else ""
            return "\n".join(lines) + suffix, {
                "found": True,
                "already_completed": False if completed else None,
                "task": task,
            }
        return content, {"found": False, "already_completed": False, "task": None}

    return update_text_file(tasks_path, update)


def remove_recurring_tasks(tasks_path, task_ids):
    ids = set(task_ids)
    if not ids:
        return 0

    def remove(content):
        kept = []
        removed = 0
        for line in content.splitlines():
            task = parse_recurring_task_line(line)
            if task and task.get("id") in ids:
                removed += 1
                continue
            kept.append(line)
        suffix = "\n" if content.endswith("\n") and kept else ""
        return "\n".join(kept) + suffix, removed

    return update_text_file(tasks_path, remove) or 0
