"""Brain View: local Markdown search, timeline, tasks, and metadata.

The source Markdown files remain authoritative.  This module keeps a rebuildable
file-per-document index beside each user's data and a separate metadata file for
manual Brain annotations.
"""

import copy
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import unicodedata
import fcntl
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

from flask import Blueprint, jsonify, request
import family as family_module
import historical_tagging
import tagging as tagging_module
import ai_sessions as ai_sessions_module
from scheduling import path_lock, read_text_file, update_text_file, write_text_file


logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
FAMILY_DIR = DATA_DIR / "family"
INDEX_DIR_NAME = "brain_index"
METADATA_FILE_NAME = "brain_metadata.json"
INDEX_INTERVAL_SECONDS = max(300, int(os.environ.get("BRAIN_INDEX_INTERVAL_SECONDS", "3600")))

brain_bp = Blueprint("brain", __name__, url_prefix="/api/brain")

_INDEXED_TECHNICAL_NAMES = {
    "readme.md",
    "changelog.md",
    "license.md",
    "copying.md",
    "contributing.md",
    "code_of_conduct.md",
    "security.md",
    "index.md",
}
_TECHNICAL_DIRS = {"indexes", "brain_index", "__pycache__"}
_TASK_RE = re.compile(r"^(?P<indent>\s*)(?P<bullet>[-*+])\s+\[(?P<state>[ xX])\]\s*(?P<text>.*)$")
_TASK_STATE_RE = re.compile(r"^(?P<prefix>\s*[-*+]\s+)\[[ xX]\]", re.MULTILINE)
_FAMILY_TASK_ID_RE = re.compile(
    r"^id:\s*(?P<id>[^|]+?)\s*\|\s*title:\s*[^|]*?\s*\|\s*"
    r"user:\s*[^|]*?\s*\|\s*target-date:\s*[^|]*?(?:\s*\||$)"
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_PATH_RE = re.compile(
    r"^(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"Journal_(?P=year)-(?P=month)-(?P=day)\.md$"
)
_JOURNAL_DATE_RE = re.compile(r"Journal_(\d{4}-\d{2}-\d{2})\.md$")
_TIME_RE = re.compile(r"(?:Time:\s*|~~|^\s*[-*]\s*)(\d{2}:\d{2}:\d{2})", re.IGNORECASE | re.MULTILINE)
_DATETIME_RE = re.compile(
    r"Datum\s*&\s*Uhrzeit:\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})",
    re.IGNORECASE,
)
_BRAIN_TAG_RE = re.compile(r"Brainablage:\s*Tags:\s*(.*?)(?:\*\*Datum|\n|$)", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"(?<![\w#])#([\w-]+)", re.UNICODE)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_index_queue = queue.Queue()
_queued_rebuilds = set()
_running_rebuilds = set()
_dirty_rebuilds = set()
_queued_rebuilds_lock = threading.Lock()
_index_worker = None
_index_worker_lock_file = None
_index_snapshot_lock = threading.Lock()
_index_snapshots = {}


def _main_module():
    """Load main lazily so this blueprint can be imported by main.py."""
    import sys

    module = sys.modules.get("main") or sys.modules.get("__main__")
    return module


def _current_user():
    main = _main_module()
    return main._current_user() if main else None


def _csrf_error():
    """Use the existing CSRF implementation without creating an import cycle."""
    main = _main_module()
    return main.csrf_protect(lambda: None)() if main else (jsonify({"error": "Unauthorized"}), 401)


def _require_user():
    user = _current_user()
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    return user, None


def _is_admin(user):
    return user.get("admin") is True


def _atomic_write(path, content):
    """Write through the shared stable target lock used by the scheduler."""
    write_text_file(path, content)


def _read_text(path):
    try:
        if not Path(path).is_file():
            return None
        return read_text_file(path)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Brain could not read %s: %s", path, exc)
        return None


def _read_json(path, default):
    if not path.exists():
        return default
    content = _read_text(path)
    if content is None:
        return default
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Brain could not parse JSON %s", path)
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _write_json(path, value):
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _user_root(user_id):
    return DATA_DIR / user_id


def _archive_root():
    configured = os.environ.get("BRAIN_ARCHIVE_DIR", "").strip()
    return Path(configured) if configured else DATA_DIR / "_Archiv" / "Projekte"


def _index_dir(source, user_id=None):
    if source == "personal":
        return _user_root(user_id) / "indexes" / INDEX_DIR_NAME
    if source == "archive":
        return DATA_DIR / "indexes" / "brain_archive_index"
    return FAMILY_DIR / "indexes" / INDEX_DIR_NAME


def _metadata_path(user_id):
    return _user_root(user_id) / METADATA_FILE_NAME


def _normalise(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return value.casefold()


def _fingerprint(*parts):
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _source_signature(stat):
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
    }


def _read_source_snapshot(path):
    """Read a source whose filesystem identity stayed stable during the read."""
    for _attempt in range(3):
        try:
            before = path.stat()
            content = _read_text(path)
            after = path.stat()
        except OSError:
            return None, None
        if content is not None and _source_signature(before) == _source_signature(after):
            return content, after
    logger.warning("Brain source changed repeatedly while reading %s", path)
    return None, None


def _relative_path(root, path):
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _is_technical_file(path, root):
    relative = _relative_path(root, path)
    if not relative:
        return True
    parts = Path(relative).parts
    lower_parts = [part.lower() for part in parts]
    if any(part.startswith(".") for part in parts):
        return True
    if any(part in _TECHNICAL_DIRS for part in lower_parts[:-1]):
        return True
    name = path.name.lower()
    if name in _INDEXED_TECHNICAL_NAMES:
        return True
    if name.endswith((".bak", ".backup", ".tmp", ".lock")):
        return True
    return False


def _iter_markdown(root, allowed=None):
    if not root.is_dir():
        return []
    paths = []
    for path in root.rglob("*.md"):
        if path.is_symlink() or not path.is_file() or _is_technical_file(path, root):
            continue
        relative = _relative_path(root, path)
        if not relative or (allowed and not allowed(relative)):
            continue
        paths.append(path)
    return sorted(paths)


def _personal_kind(relative):
    if relative.startswith("projects/"):
        return "project"
    if relative.startswith("notes/"):
        return "note"
    if _JOURNAL_PATH_RE.match(relative):
        return "journal"
    return None


def _parse_frontmatter(content):
    result = {
        "assigned_users": [],
        "template_id": "",
        "project_id": "",
        "id": "",
        "user": "",
        "created_by": "",
        "created_at": "",
        "title": "",
        "_frontmatter_present": False,
        "_frontmatter_valid": True,
    }
    normalized = (content or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---"):
        return result
    result["_frontmatter_present"] = True
    match = _FRONTMATTER_RE.match(normalized)
    if not match:
        result["_frontmatter_valid"] = False
        return result
    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        key_match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not key_match:
            result["_frontmatter_valid"] = False
            index += 1
            continue
        key, value = key_match.group(1), key_match.group(2).strip()
        if key == "assigned_users":
            if value.startswith("["):
                if not value.endswith("]"):
                    result["_frontmatter_valid"] = False
                    index += 1
                    continue
                value = value[1:-1]
                result[key] = [item.strip().strip("'\"") for item in value.split(",") if item.strip()]
            elif value:
                result[key] = [value.strip("'\"")]
            else:
                assigned = []
                while index + 1 < len(lines):
                    list_match = re.match(r"^\s+-\s+(.+?)\s*$", lines[index + 1])
                    if not list_match:
                        break
                    assigned.append(list_match.group(1).strip().strip("'\""))
                    index += 1
                result[key] = [item for item in assigned if item]
        elif key in result:
            result[key] = value.strip("'\"")
        index += 1
    return result


def _template_assignments(template_id):
    if not template_id:
        return []
    main = _main_module()
    if not main:
        return []
    for template in main.load_config().get("templates", []):
        if template.get("id") == template_id:
            return list(template.get("assigned_users") or [])
    return []


def _template_membership(template_id):
    """Return whether a template exists and its configured membership."""
    if not template_id:
        return False, []
    main = _main_module()
    if not main:
        return False, []
    for template in main.load_config().get("templates", []):
        if template.get("id") == template_id:
            return True, list(template.get("assigned_users") or [])
    return False, []


def _family_visibility(path, relative, user_id, content=None):
    """Re-evaluate visibility from the source file, never from index metadata."""
    content = content if content is not None else _read_text(path)
    if content is None:
        return False, {}
    metadata = _parse_frontmatter(content)
    if metadata.get("_frontmatter_present") and not metadata.get("_frontmatter_valid"):
        return False, metadata
    assigned = metadata.get("assigned_users") or []
    template_id = metadata.get("template_id") or ""

    # Archived tasks do not retain a full project front matter in old files. Use
    # their still-existing project as the authority; unknown legacy archives are
    # denied rather than revealing an old restricted task.
    if relative.startswith("archive/") and not assigned:
        template_found, template_assigned = _template_membership(template_id)
        membership_verified = template_found
        if template_found:
            assigned = template_assigned
        elif template_id:
            return False, metadata
        project_id = metadata.get("project_id") or ""
        if not assigned and project_id == family_module.RECURRING_PROJECT_ID:
            assigned = [metadata.get("user")] if metadata.get("user") else []
            if not assigned:
                return False, metadata
            membership_verified = True
        elif not assigned and project_id:
            if not _PROJECT_ID_RE.fullmatch(project_id):
                return False, metadata
            project_path = FAMILY_DIR / "projects" / f"{project_id}.md"
            project_content = _read_text(project_path)
            if project_content is None:
                return False, metadata
            project_metadata = _parse_frontmatter(project_content)
            if not project_metadata.get("_frontmatter_valid"):
                return False, metadata
            assigned = project_metadata.get("assigned_users") or []
            template_id = project_metadata.get("template_id") or ""
            parent_template_found, parent_template_assigned = _template_membership(template_id)
            if not assigned and parent_template_found:
                assigned = parent_template_assigned
                membership_verified = True
            elif not assigned and template_id:
                return False, metadata
            else:
                membership_verified = True
        elif not assigned and not membership_verified:
            return False, metadata

    # Existing Family project files use either explicit project membership or
    # template membership. Explicit memberships always win.
    effective_assignments = assigned or _template_assignments(template_id)
    visible = not effective_assignments or user_id in effective_assignments
    return visible, {**metadata, "assigned_users": effective_assignments}


def _extract_tags(text):
    tags = set()
    for match in _BRAIN_TAG_RE.finditer(text):
        raw = match.group(1)
        for item in re.split(r"[,;|]", raw):
            cleaned = item.strip().strip("#").strip()
            if cleaned:
                tags.add(_normalise(cleaned))
    for match in _HASHTAG_RE.finditer(text):
        tags.add(_normalise(match.group(1)))
    return sorted(tag for tag in tags if tag)


def _block_anchor(text, start_line):
    datetime_match = _DATETIME_RE.search(text)
    if datetime_match:
        return f"timestamp:{datetime_match.group(1).replace(' ', 'T')}", True
    time_match = _TIME_RE.search(text)
    if time_match:
        return f"timestamp:{time_match.group(1)}", True
    return f"line:{start_line}", False


def _split_blocks(content, is_journal):
    """Return (text, start_line, end_line) using journal separators or headings."""
    lines = content.splitlines()
    if lines and lines[0].lstrip("\ufeff") == "---":
        closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
        if closing is not None:
            for index in range(closing + 1):
                lines[index] = ""
    separator_lines = [index for index, line in enumerate(lines) if line.strip() == "___"]
    ranges = []
    if separator_lines:
        boundaries = [-1, *separator_lines, len(lines)]
        for before, after in zip(boundaries, boundaries[1:]):
            start = before + 1
            end = after
            block_lines = lines[start:end]
            while block_lines and not block_lines[0].strip():
                start += 1
                block_lines.pop(0)
            while block_lines and not block_lines[-1].strip():
                end -= 1
                block_lines.pop()
            if block_lines:
                ranges.append(("\n".join(block_lines), start + 1, end))
    else:
        heading_lines = [index for index, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)]
        if heading_lines and not is_journal:
            preamble = lines[:heading_lines[0]]
            if any(line.strip() for line in preamble):
                first = next(index for index, line in enumerate(preamble) if line.strip())
                last = len(preamble) - next(index for index, line in enumerate(reversed(preamble)) if line.strip())
                ranges.append(("\n".join(preamble[first:last]), first + 1, last))
            boundaries = [*heading_lines, len(lines)]
            for start, end in zip(boundaries, boundaries[1:]):
                block_lines = lines[start:end]
                if block_lines:
                    ranges.append(("\n".join(block_lines).strip(), start + 1, end))
        elif lines:
            ranges.append(("\n".join(lines).strip(), 1, len(lines)))

    cleaned = []
    for text, start, end in ranges:
        if is_journal and text.startswith("# Journal "):
            header, separator, remainder = text.partition("\n")
            if not separator or not remainder.strip():
                continue
            removed = len(remainder) - len(remainder.lstrip("\n"))
            text = remainder.lstrip("\n")
            start += 1 + removed
        if text:
            cleaned.append((text, start, end))
    return cleaned


def _journal_date(relative):
    match = _JOURNAL_DATE_RE.search(relative)
    return match.group(1) if match else ""


def _local_timezone():
    main = _main_module()
    if main and hasattr(main, "get_tz_aware_now"):
        now, _ = main.get_tz_aware_now()
        if now.tzinfo:
            return now.tzinfo
    return timezone.utc


def _sort_time(journal_date, anchor, mtime):
    if journal_date:
        time_value = anchor.removeprefix("timestamp:")
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}", time_value):
            value = f"{journal_date}T{time_value}"
        elif time_value.startswith(journal_date):
            value = time_value
        else:
            value = f"{journal_date}T00:00:00"
        try:
            return datetime.fromisoformat(value).replace(tzinfo=_local_timezone()).timestamp()
        except ValueError:
            pass
    return mtime.timestamp()


def _task_identity(task_text):
    structured = _FAMILY_TASK_ID_RE.match(task_text)
    if structured:
        return f"family-task:{structured.group('id').strip()}"
    return _normalise(task_text)


def _block_identity(block_text):
    return _normalise(_TASK_STATE_RE.sub(r"\g<prefix>[ ]", block_text))


def _build_document(source, root, path, kind=None, content=None):
    relative = _relative_path(root, path)
    if content is None:
        content, stat = _read_source_snapshot(path)
    else:
        try:
            stat = path.stat()
        except OSError:
            return None
    if not relative or content is None or stat is None:
        return None
    if kind == "journal":
        content, _ = tagging_module.strip_footer(content)
    journal_date = _journal_date(relative) if kind == "journal" else ""
    mtime_timestamp = stat.st_mtime
    mtime = datetime.fromtimestamp(mtime_timestamp, timezone.utc).isoformat()
    doc_id = f"{source}:{relative}"
    blocks = []
    block_identity_counts = {}
    structured_task_counts = {}
    if source == "family":
        for line in content.splitlines():
            task_match = _TASK_RE.match(line)
            if not task_match:
                continue
            identity = _task_identity(task_match.group("text").strip())
            if identity.startswith("family-task:"):
                structured_task_counts[identity] = structured_task_counts.get(identity, 0) + 1
    for block_text, start_line, end_line in _split_blocks(content, kind == "journal"):
        anchor, stable = _block_anchor(block_text, start_line)
        legacy_block_fingerprint = _fingerprint(doc_id, anchor, _normalise(block_text))
        block_base = _fingerprint(doc_id, anchor, _block_identity(block_text))
        block_identity_counts[block_base] = block_identity_counts.get(block_base, 0) + 1
        block_fingerprint = _fingerprint(block_base, str(block_identity_counts[block_base]))
        tasks = []
        occurrence = 0
        for offset, line in enumerate(block_text.splitlines()):
            task_match = _TASK_RE.match(line)
            if not task_match:
                continue
            occurrence += 1
            task_text = task_match.group("text").strip()
            task_identity = _task_identity(task_text)
            if structured_task_counts.get(task_identity, 0) > 1:
                continue
            if task_identity.startswith("family-task:"):
                task_fingerprint = _fingerprint(doc_id, task_identity)
                legacy_task_fingerprint = task_fingerprint
            else:
                task_fingerprint = _fingerprint(
                    doc_id, block_fingerprint, task_identity, str(occurrence)
                )
                legacy_task_fingerprint = _fingerprint(
                    doc_id, anchor, task_identity, str(occurrence)
                )
            tasks.append({
                "fingerprint": task_fingerprint,
                "legacy_fingerprint": legacy_task_fingerprint,
                "line": start_line + offset,
                "text": task_text,
                "identity": task_identity,
                "completed": task_match.group("state").lower() == "x",
                "occurrence": occurrence,
            })
        blocks.append({
            "fingerprint": block_fingerprint,
            "legacy_fingerprint": legacy_block_fingerprint,
            "stable_anchor": anchor,
            "has_stable_anchor": stable,
            "start_line": start_line,
            "end_line": end_line,
            "text": block_text,
            "tags_auto": _extract_tags(block_text),
            "tasks": tasks,
            "sort_at": _sort_time(journal_date, anchor, datetime.fromtimestamp(mtime_timestamp, timezone.utc)),
        })
    if not blocks:
        if kind == "journal":
            return None
        anchor = "line:1"
        blocks.append({
            "fingerprint": _fingerprint(doc_id, anchor, ""),
            "stable_anchor": anchor,
            "has_stable_anchor": False,
            "start_line": 1,
            "end_line": 1,
            "text": "",
            "tags_auto": [],
            "tasks": [],
            "sort_at": mtime_timestamp,
        })
    document = {
        "version": 2,
        "doc_id": doc_id,
        "source": source,
        "path": relative,
        "kind": kind or "family",
        "read_only": source == "archive",
        "mtime": mtime,
        "mtime_ns": stat.st_mtime_ns,
        "source_signature": _source_signature(stat),
        "date": journal_date or mtime[:10],
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "blocks": blocks,
    }
    if source == "personal" and kind == "project":
        session = ai_sessions_module.parse_session(content, relative)
        if session:
            document["read_only"] = True
            document["ai_session"] = session
    if source == "family":
        document["family_metadata"] = _parse_frontmatter(content)
    return document


def _scan_personal_documents(user_id):
    root = _user_root(user_id)
    documents = []
    for path in _iter_markdown(root, _personal_kind):
        relative = _relative_path(root, path)
        document = _build_document("personal", root, path, _personal_kind(relative))
        if document:
            documents.append(document)
    return documents


def _scan_family_documents():
    documents = []
    for path in _iter_markdown(FAMILY_DIR):
        content = _read_text(path)
        document = _build_document("family", FAMILY_DIR, path, "family", content)
        if document:
            documents.append(document)
    return documents


def _scan_archive_documents():
    root = _archive_root()
    documents = []
    for path in _iter_markdown(root):
        document = _build_document("archive", root, path, "archive")
        if document:
            documents.append(document)
    return documents


def _index_file(index_dir, doc_id):
    return index_dir / f"{hashlib.sha256(doc_id.encode('utf-8')).hexdigest()}.json"


@contextmanager
def _index_guard(source, user_id=None, exclusive=False):
    """Serialize scans and publications for one index generation."""
    directory = _index_dir(source, user_id)
    with path_lock(directory / ".brain-index", exclusive=exclusive):
        yield


def _write_index_documents(source, documents, user_id=None):
    directory = _index_dir(source, user_id)
    directory.mkdir(parents=True, exist_ok=True)
    generation = f"{datetime.now(timezone.utc).isoformat()}-{os.getpid()}-{threading.get_ident()}"
    expected = set()
    for document in documents:
        path = _index_file(directory, document["doc_id"])
        expected.add(path.name)
        indexed_document = dict(document)
        indexed_document["_index_generation"] = generation
        _write_json(path, indexed_document)
    for stale in directory.glob("*.json"):
        if stale.name not in expected:
            stale.unlink(missing_ok=True)
    _write_json(directory / "status.json", {
        "version": 3,
        "generation": generation,
        "rebuilt_at": datetime.now(timezone.utc).isoformat(),
        "document_count": len(documents),
    })


def _write_index_document(source, document, user_id=None):
    """Refresh one toggled task without deleting the rest of an index."""
    directory = _index_dir(source, user_id)
    directory.mkdir(parents=True, exist_ok=True)
    status_path = directory / "status.json"
    status = _read_json(status_path, {})
    indexed_document = dict(document)
    generation = status.get("generation")
    if status.get("version") == 3 and isinstance(generation, str) and generation:
        indexed_document["_index_generation"] = generation
    _write_json(_index_file(directory, document["doc_id"]), indexed_document)
    if generation:
        status["document_count"] = sum(
            1 for path in directory.glob("*.json") if path.name != "status.json"
        )
        status["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(status_path, status)


def _delete_index_document(source, doc_id, user_id=None):
    directory = _index_dir(source, user_id)
    _index_file(directory, doc_id).unlink(missing_ok=True)
    status_path = directory / "status.json"
    status = _read_json(status_path, {})
    if status.get("version") == 3 and status.get("generation"):
        status["document_count"] = sum(
            1 for path in directory.glob("*.json") if path.name != "status.json"
        )
        status["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(status_path, status)


def _read_index_documents(source, user_id=None):
    directory = _index_dir(source, user_id)
    with _index_guard(source, user_id, exclusive=False):
        status_path = directory / "status.json"
        if not directory.is_dir() or not status_path.exists():
            return None
        status = _read_json(status_path, {})
        generation = status.get("generation")
        expected_count = status.get("document_count")
        if status.get("version") != 3 or not isinstance(generation, str) or not isinstance(expected_count, int):
            return None
        snapshot_key = str(directory)
        snapshot_signature = (
            generation,
            expected_count,
            status.get("updated_at"),
        )
        with _index_snapshot_lock:
            snapshot = _index_snapshots.get(snapshot_key)
            cached_documents = snapshot[1] if snapshot and snapshot[0] == snapshot_signature else None
        if cached_documents is not None:
            return copy.deepcopy(cached_documents)
        documents = []
        for path in sorted(directory.glob("*.json")):
            if path.name == "status.json":
                continue
            document = _read_json(path, {})
            if (
                not document.get("doc_id")
                or not isinstance(document.get("blocks"), list)
                or document.get("_index_generation") != generation
            ):
                return None
            document.pop("_index_generation", None)
            documents.append(document)
        if len(documents) != expected_count or len({document["doc_id"] for document in documents}) != len(documents):
            return None
        cached_documents = copy.deepcopy(documents)
        with _index_snapshot_lock:
            _index_snapshots[snapshot_key] = (snapshot_signature, cached_documents)
        return documents


def _metadata_default():
    return {"version": 1, "annotations": {}}


def _metadata_is_valid(metadata):
    if not isinstance(metadata, dict) or not isinstance(metadata.get("annotations"), dict):
        return False
    for reference, annotation in metadata["annotations"].items():
        if not isinstance(reference, str) or not isinstance(annotation, dict):
            return False
        if "tags" in annotation and (
            not isinstance(annotation["tags"], list)
            or any(not isinstance(tag, str) for tag in annotation["tags"])
        ):
            return False
        for field in ("kind", "doc_id", "path", "stable_anchor", "priority", "project", "task_text"):
            if field in annotation and not isinstance(annotation[field], str):
                return False
        if "task_occurrence" in annotation and not isinstance(annotation["task_occurrence"], int):
            return False
    return True


def _read_metadata(user_id):
    metadata = _read_json(_metadata_path(user_id), _metadata_default())
    if not _metadata_is_valid(metadata):
        logger.error("Brain metadata for %s has an invalid schema; ignoring it", user_id)
        return _metadata_default()
    return metadata


def _update_metadata(user_id, updater):
    """Apply one metadata change under the same cross-process file lock."""
    path = _metadata_path(user_id)

    def update(content):
        try:
            metadata = json.loads(content) if content else _metadata_default()
        except json.JSONDecodeError as exc:
            raise ValueError("Brain metadata is corrupt") from exc
        if not _metadata_is_valid(metadata):
            raise ValueError("Brain metadata is corrupt")
        result = updater(metadata)
        metadata["version"] = 1
        serialized = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return serialized, result

    return update_text_file(path, update)


def _annotation_candidates(metadata, document, block, kind, task=None):
    if sum(
        candidate.get("stable_anchor") == block.get("stable_anchor")
        for candidate in document.get("blocks", [])
    ) != 1:
        return []
    candidates = []
    for reference, annotation in metadata.get("annotations", {}).items():
        if annotation.get("kind") != kind or annotation.get("doc_id") != document["doc_id"]:
            continue
        if annotation.get("stable_anchor") != block.get("stable_anchor"):
            continue
        if not block.get("has_stable_anchor"):
            continue
        if kind == "task" and (
            annotation.get("task_text") != task.get("identity", _task_identity(task.get("text", "")))
            or annotation.get("task_occurrence") != task.get("occurrence")
        ):
            continue
        candidates.append((reference, annotation))
    return candidates


def _decorate_documents(user_id, documents):
    metadata = _read_metadata(user_id)
    annotations = metadata["annotations"]
    legacy_counts = {}
    for document in documents:
        for block in document.get("blocks", []):
            legacy = block.get("legacy_fingerprint")
            if legacy:
                legacy_counts[legacy] = legacy_counts.get(legacy, 0) + 1
            for task in block.get("tasks", []):
                legacy = task.get("legacy_fingerprint")
                if legacy:
                    legacy_counts[legacy] = legacy_counts.get(legacy, 0) + 1
    for document in documents:
        for block in document.get("blocks", []):
            reference = block["fingerprint"]
            annotation = annotations.get(reference)
            metadata_reference = reference
            if (
                not annotation
                and block.get("legacy_fingerprint")
                and legacy_counts.get(block["legacy_fingerprint"]) == 1
            ):
                metadata_reference = block["legacy_fingerprint"]
                annotation = annotations.get(metadata_reference)
            if not annotation:
                candidates = _annotation_candidates(metadata, document, block, "block")
                if len(candidates) == 1:
                    metadata_reference, annotation = candidates[0]
            annotation = annotation or {}
            block["metadata_ref"] = metadata_reference if annotation else None
            block["manual_tags"] = sorted(set(annotation.get("tags", [])))
            block["tags"] = sorted(set(block.get("tags_auto", [])) | set(block["manual_tags"]))
            block["priority"] = annotation.get("priority", "normal")
            block["project"] = annotation.get("project") or ""
            for task in block.get("tasks", []):
                task_reference = task["fingerprint"]
                task_annotation = annotations.get(task_reference)
                task_metadata_ref = task_reference
                if (
                    not task_annotation
                    and task.get("legacy_fingerprint")
                    and legacy_counts.get(task["legacy_fingerprint"]) == 1
                ):
                    task_metadata_ref = task["legacy_fingerprint"]
                    task_annotation = annotations.get(task_metadata_ref)
                if not task_annotation:
                    candidates = _annotation_candidates(metadata, document, block, "task", task)
                    if len(candidates) == 1:
                        task_metadata_ref, task_annotation = candidates[0]
                task_annotation = task_annotation or {}
                task["metadata_ref"] = task_metadata_ref if task_annotation else None
                task["manual_tags"] = sorted(set(task_annotation.get("tags", [])))
                task["tags"] = sorted(set(block.get("tags_auto", [])) | set(task["manual_tags"]))
                task["priority"] = task_annotation.get("priority", "normal")
                task["project"] = task_annotation.get("project") or ""
                task["created_at"] = document["date"]
    return documents


def _mark_orphaned_metadata(user_id, documents):
    active = set()
    anchored = {}
    legacy_links = {}
    for document in documents:
        for block in document.get("blocks", []):
            active.add(block["fingerprint"])
            legacy_links.setdefault(block.get("legacy_fingerprint"), []).append(block["fingerprint"])
            if block.get("has_stable_anchor"):
                anchored.setdefault((document["doc_id"], block["stable_anchor"], "block"), []).append(block)
                for task in block.get("tasks", []):
                    active.add(task["fingerprint"])
                    legacy_links.setdefault(task.get("legacy_fingerprint"), []).append(task["fingerprint"])
                    anchored.setdefault((document["doc_id"], block["stable_anchor"], "task"), []).append(task)
            else:
                for task in block.get("tasks", []):
                    active.add(task["fingerprint"])
                    legacy_links.setdefault(task.get("legacy_fingerprint"), []).append(task["fingerprint"])
    legacy_links.pop(None, None)
    def mark(metadata):
        changed = False
        annotations = metadata["annotations"]
        for reference, annotation in list(annotations.items()):
            migration_targets = legacy_links.get(reference, [])
            if reference not in active and len(migration_targets) == 1:
                target = migration_targets[0]
                if target not in annotations:
                    annotations[target] = annotation
                    del annotations[reference]
                    reference = target
                    changed = True
        for reference, annotation in annotations.items():
            linked = reference in active
            if not linked and annotation.get("stable_anchor", "").startswith("timestamp:"):
                candidates = anchored.get((annotation.get("doc_id"), annotation.get("stable_anchor"), annotation.get("kind")), [])
                if annotation.get("kind") == "task":
                    candidates = [
                        task for task in candidates
                        if annotation.get("task_text") == task.get("identity", _task_identity(task.get("text", "")))
                        and annotation.get("task_occurrence") == task.get("occurrence")
                    ]
                linked = len(candidates) == 1
            orphaned = not linked
            if annotation.get("orphaned", False) != orphaned:
                annotation["orphaned"] = orphaned
                changed = True
        return changed

    try:
        _update_metadata(user_id, mark)
    except ValueError:
        logger.error("Brain metadata for %s is corrupt; preserving it unchanged", user_id)


def rebuild_user_index(user_id):
    documents = _scan_personal_documents(user_id)
    tagging_module.propose_tags(
        user_id,
        [tag for document in documents for block in document.get("blocks", []) for tag in block.get("tags_auto", [])],
    )
    with _index_guard("personal", user_id, exclusive=True):
        _write_index_documents("personal", documents, user_id)
    # Metadata is per user and can annotate visible Family or archive content as
    # well as personal files. Resolve all of those sources before marking an
    # annotation orphaned; rebuilding a personal index must not orphan Family
    # annotations merely because they live in the shared index.
    family_documents = [
        document for document in _scan_family_documents()
        if _family_document_is_visible(document, user_id)
    ]
    _mark_orphaned_metadata(user_id, [*documents, *family_documents, *_scan_archive_documents()])
    return len(documents)


def rebuild_family_index():
    documents = _scan_family_documents()
    with _index_guard("family", exclusive=True):
        _write_index_documents("family", documents)
    return len(documents)


def rebuild_archive_index():
    documents = _scan_archive_documents()
    with _index_guard("archive", exclusive=True):
        _write_index_documents("archive", documents)
    return len(documents)


def _rebuild_request_dir():
    return DATA_DIR / "indexes" / "brain_rebuild_requests"


def _persist_rebuild_request(request_key):
    directory = _rebuild_request_dir()
    filename = f"{hashlib.sha256(request_key.encode('utf-8')).hexdigest()}.json"
    _write_json(directory / filename, {"request_key": request_key})


def _pop_rebuild_request():
    directory = _rebuild_request_dir()
    if not directory.is_dir():
        return None
    main = _main_module()
    valid_users = {
        user.get("id") for user in (main.get_all_users() if main else []) if user.get("id")
    }
    for path in sorted(directory.glob("*.json")):
        data = _read_json(path, {})
        path.unlink(missing_ok=True)
        request_key = data.get("request_key")
        if request_key == "__all__" or request_key in valid_users:
            return request_key
    return None


def _rebuild_for_user(user_id):
    personal_count = rebuild_user_index(user_id)
    family_count = rebuild_family_index()
    archive_count = rebuild_archive_index()
    main = _main_module()
    tagging_module.rebuild_index([item["id"] for item in main.get_all_users() if item.get("id")] if main else [user_id])
    logger.info(
        "Brain index rebuilt for %s (%s personal, %s family, %s archive)",
        user_id,
        personal_count,
        family_count,
        archive_count,
    )


def _rebuild_all():
    main = _main_module()
    if not main:
        return
    users = [user for user in main.get_all_users() if user.get("id")]
    for user in users:
        if user.get("id"):
            rebuild_user_index(user["id"])
    rebuild_family_index()
    rebuild_archive_index()
    tagging_module.rebuild_index([user["id"] for user in users])


def _index_worker_loop():
    next_periodic = time.monotonic() + INDEX_INTERVAL_SECONDS
    while True:
        request_key = _pop_rebuild_request()
        remaining = next_periodic - time.monotonic()
        if request_key is not None:
            pass
        elif remaining <= 0:
            request_key = "__all__"
            next_periodic = time.monotonic() + INDEX_INTERVAL_SECONDS
        else:
            try:
                request_key = _index_queue.get(timeout=min(remaining, 2.0))
            except queue.Empty:
                continue
        with _queued_rebuilds_lock:
            _queued_rebuilds.discard(request_key)
            _running_rebuilds.add(request_key)
        try:
            if request_key == "__all__":
                _rebuild_all()
            else:
                _rebuild_for_user(request_key)
        except Exception:
            logger.exception("Brain index rebuild failed")
        finally:
            requeue = False
            with _queued_rebuilds_lock:
                _running_rebuilds.discard(request_key)
                if request_key in _dirty_rebuilds:
                    _dirty_rebuilds.discard(request_key)
                    _queued_rebuilds.add(request_key)
                    requeue = True
            if requeue:
                _index_queue.put(request_key)


def start_index_worker(initial=False):
    """Start the low-frequency worker once per application process."""
    global _index_worker, _index_worker_lock_file
    if not _index_worker or not _index_worker.is_alive():
        if _index_worker_lock_file is None:
            lock_path = DATA_DIR / ".brain-index-worker"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(lock_path, "a+", encoding="utf-8")
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_file.close()
                return False
            _index_worker_lock_file = lock_file
        _index_worker = threading.Thread(target=_index_worker_loop, name="brain-indexer", daemon=True)
        _index_worker.start()
    if initial:
        enqueue_rebuild("__all__")
    return True


def enqueue_rebuild(user_id):
    if not start_index_worker():
        _persist_rebuild_request(user_id)
        return True
    with _queued_rebuilds_lock:
        if user_id in _running_rebuilds:
            _dirty_rebuilds.add(user_id)
            return True
        if user_id in _queued_rebuilds:
            return False
        _queued_rebuilds.add(user_id)
    _index_queue.put(user_id)
    return True


def _current_family_document(document, user_id):
    relative = document.get("path", "")
    path = FAMILY_DIR / relative
    if (
        _relative_path(FAMILY_DIR, path) != relative
        or path.suffix != ".md"
        or not path.is_file()
        or _is_technical_file(path, FAMILY_DIR)
    ):
        return None, True
    content = _read_text(path)
    if content is None:
        return None, True
    if not _family_visibility(path, relative, user_id, content)[0]:
        return None, False
    current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if document.get("content_hash") == current_hash:
        return document, False
    return _build_document("family", FAMILY_DIR, path, "family", content), True


def _family_document_is_visible(document, user_id):
    current, _ = _current_family_document(document, user_id)
    return current is not None


def _current_personal_documents(indexed_documents, user_id):
    root = _user_root(user_id)
    indexed_by_path = {document.get("path"): document for document in indexed_documents}
    documents = []
    current_paths = set()
    stale = False
    for path in _iter_markdown(root, _personal_kind):
        relative = _relative_path(root, path)
        indexed = indexed_by_path.get(relative)
        try:
            unchanged = indexed and indexed.get("source_signature") == _source_signature(path.stat())
        except OSError:
            stale = True
            continue
        if unchanged:
            documents.append(indexed)
            current_paths.add(relative)
            continue
        document = _build_document("personal", root, path, _personal_kind(relative))
        if indexed or document:
            stale = True
        if document:
            documents.append(document)
            current_paths.add(relative)
    if set(indexed_by_path) != current_paths:
        stale = True
    return documents, stale


def _current_family_documents(indexed_documents, user_id):
    indexed_by_path = {document.get("path"): document for document in indexed_documents}
    documents = []
    current_paths = set()
    stale = False
    visible_stale = False
    for path in _iter_markdown(FAMILY_DIR):
        relative = _relative_path(FAMILY_DIR, path)
        current_paths.add(relative)
        indexed = indexed_by_path.get(relative)
        try:
            unchanged = indexed and indexed.get("source_signature") == _source_signature(path.stat())
        except OSError:
            stale = True
            continue
        if unchanged:
            metadata = indexed.get("family_metadata") or {}
            if (
                not relative.startswith("archive/")
                and metadata.get("_frontmatter_valid", True)
                and not metadata.get("template_id")
            ):
                assigned = metadata.get("assigned_users") or []
                if not assigned or user_id in assigned:
                    documents.append(indexed)
                continue
        content = _read_text(path)
        if content is None:
            stale = True
            continue
        visible, _ = _family_visibility(path, relative, user_id, content)
        if unchanged:
            if visible:
                documents.append(indexed)
            continue
        stale = True
        if visible:
            visible_stale = True
            document = _build_document("family", FAMILY_DIR, path, "family", content)
            if document:
                documents.append(document)
    if set(indexed_by_path) != current_paths:
        stale = True
    return documents, stale, visible_stale


def _current_archive_documents(indexed_documents):
    root = _archive_root()
    indexed_by_path = {document.get("path"): document for document in indexed_documents}
    documents = []
    current_paths = set()
    stale = False
    for path in _iter_markdown(root):
        relative = _relative_path(root, path)
        current_paths.add(relative)
        indexed = indexed_by_path.get(relative)
        try:
            unchanged = indexed and indexed.get("source_signature") == _source_signature(path.stat())
        except OSError:
            stale = True
            continue
        if unchanged:
            documents.append(indexed)
            continue
        stale = True
        document = _build_document("archive", root, path, "archive")
        if document:
            documents.append(document)
    if set(indexed_by_path) != current_paths:
        stale = True
    return documents, stale


def _visible_documents(user_id):
    personal = _read_index_documents("personal", user_id)
    family = _read_index_documents("family")
    archive = _read_index_documents("archive")
    using_fallback = personal is None or family is None or archive is None
    personal_stale = False
    family_stale = False
    family_needs_rebuild = False
    archive_stale = False
    if personal is None:
        personal = _scan_personal_documents(user_id)
    else:
        personal, personal_stale = _current_personal_documents(personal, user_id)
    if family is None:
        family = [
            document for document in _scan_family_documents()
            if _family_document_is_visible(document, user_id)
        ]
    else:
        family, family_needs_rebuild, family_stale = _current_family_documents(family, user_id)
    if archive is None:
        archive = _scan_archive_documents()
    else:
        archive, archive_stale = _current_archive_documents(archive)
    needs_rebuild = using_fallback or personal_stale or family_needs_rebuild or archive_stale
    index_pending = using_fallback or personal_stale or family_stale or archive_stale
    if needs_rebuild:
        enqueue_rebuild(user_id)
    return _decorate_documents(user_id, [*personal, *family, *archive]), index_pending


def _resolve_document(user_id, doc_id):
    if not isinstance(doc_id, str) or ":" not in doc_id:
        return None, None, None, None
    source, relative = doc_id.split(":", 1)
    if source == "personal":
        root = _user_root(user_id)
        kind = _personal_kind(relative)
        if not kind:
            return None, None, None, None
    elif source == "family":
        root = FAMILY_DIR
        kind = "family"
    elif source == "archive":
        root = _archive_root()
        kind = "archive"
    else:
        return None, None, None, None
    path = root / relative
    if (
        _relative_path(root, path) != relative
        or path.suffix != ".md"
        or not path.is_file()
        or _is_technical_file(path, root)
    ):
        return None, None, None, None
    content = _read_text(path)
    if content is None:
        return None, None, None, None
    if source == "family":
        visible, _ = _family_visibility(path, relative, user_id, content)
        if not visible:
            return None, None, None, None
    document = _build_document(source, root, path, kind, content)
    return document, path, root, content


def _safe_knowledge_path(root, relative):
    """Resolve a selectable Markdown source without allowing path escapes.

    Knowledge sources intentionally exclude journals, drafts and implementation
    files.  They are limited to normal Notes and Projects and are read from the
    resolved path after the containment check, so a final symlink can never be
    used as an escape hatch.
    """
    if not isinstance(relative, str) or not relative or len(relative) > 240 or "\\" in relative:
        return None, None
    candidate_relative = PurePosixPath(relative)
    if (
        candidate_relative.is_absolute() or ".." in candidate_relative.parts
        or candidate_relative.suffix != ".md" or candidate_relative.as_posix() != relative
        or not candidate_relative.parts or candidate_relative.parts[0] not in {"notes", "projects"}
    ):
        return None, None
    candidate = root / relative
    try:
        if candidate.is_symlink() or not candidate.is_file() or _is_technical_file(candidate, root):
            return None, None
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        if not resolved.is_file() or resolved.suffix != ".md":
            return None, None
    except (OSError, ValueError):
        return None, None
    return candidate, resolved


def resolve_knowledge_source(user_id, scope, relative):
    """Read one current, permitted knowledge source for an explicit AI job.

    This is deliberately independent of the Brain index: file existence and
    Family visibility are rechecked at submit time, immediately before the
    immutable job snapshot is created.
    """
    if scope == "personal":
        root = _user_root(user_id)
    elif scope == "family":
        root = FAMILY_DIR
    else:
        raise ValueError("Unknown knowledge source scope")
    candidate, resolved = _safe_knowledge_path(root, relative)
    if not candidate:
        raise ValueError("Knowledge source is missing or uses an unsafe path")
    content = _read_text(resolved)
    if content is None:
        raise ValueError("Knowledge source is unavailable")
    if scope == "family":
        visible, _ = _family_visibility(resolved, relative, user_id, content)
        if not visible:
            raise ValueError("Knowledge source is no longer visible to this user")
    return {"scope": scope, "path": relative, "content": content}


def knowledge_source_options(user_id):
    """Return only existing Notes/Projects the caller can actually read."""
    options = []
    personal_root = _user_root(user_id)
    for path in _iter_markdown(personal_root):
        relative = _relative_path(personal_root, path)
        if not relative or _personal_kind(relative) not in {"note", "project"}:
            continue
        candidate, _ = _safe_knowledge_path(personal_root, relative)
        if candidate:
            options.append({"scope": "personal", "path": relative, "label": relative})
    for path in _iter_markdown(FAMILY_DIR):
        relative = _relative_path(FAMILY_DIR, path)
        if not relative or not (relative.startswith("notes/") or relative.startswith("projects/")):
            continue
        candidate, resolved = _safe_knowledge_path(FAMILY_DIR, relative)
        if not candidate:
            continue
        content = _read_text(resolved)
        visible, _ = _family_visibility(resolved, relative, user_id, content)
        if visible:
            options.append({"scope": "family", "path": relative, "label": relative})
    return sorted(options, key=lambda item: (item["scope"], item["path"].casefold()))


def _safe_write_target(root, relative):
    """Resolve an editable directory tree without accepting browser paths."""
    if not isinstance(relative, str) or not relative or len(relative) > 240 or "\\" in relative:
        return None, None
    candidate_relative = PurePosixPath(relative)
    if (candidate_relative.is_absolute() or ".." in candidate_relative.parts
            or candidate_relative.as_posix() != relative or not candidate_relative.parts
            or candidate_relative.parts[0] not in {"notes", "projects"}):
        return None, None
    candidate = root / relative
    try:
        if candidate.is_symlink() or not candidate.is_dir() or _is_technical_file(candidate, root):
            return None, None
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        if not resolved.is_dir():
            return None, None
    except (OSError, ValueError):
        return None, None
    return candidate, resolved


def resolve_write_target(user_id, scope, relative):
    root = _user_root(user_id) if scope == "personal" else FAMILY_DIR if scope == "family" else None
    if root is None:
        raise ValueError("Unknown write target scope")
    candidate, resolved = _safe_write_target(root, relative)
    if not candidate:
        raise ValueError("Schreibziel fehlt oder verwendet einen unsicheren Ordner")
    # Family targets are deliberately admin-managed.  Visibility of individual
    # files is checked again by the producer before any content reaches Pi.
    return {"scope": scope, "path": relative, "root": resolved}


def write_target_options(user_id):
    options = []
    for scope, root in (("personal", _user_root(user_id)), ("family", FAMILY_DIR)):
        for base in (root / "notes", root / "projects"):
            if not base.is_dir():
                continue
            for path in [base, *sorted((item for item in base.rglob("*") if item.is_dir()), key=lambda p: p.as_posix())]:
                relative = _relative_path(root, path)
                candidate, _ = _safe_write_target(root, relative)
                if candidate:
                    options.append({"scope": scope, "path": relative, "label": relative})
    return sorted(options, key=lambda item: (item["scope"], item["path"].casefold()))


def external_write_root_options():
    """Expose only configured host roots; the host worker remains authoritative."""
    try:
        value = json.loads(read_text_file(DATA_DIR / "host_worker.json"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    roots = value.get("external_write_roots", []) if isinstance(value, dict) else []
    options = []
    for item in roots if isinstance(roots, list) else []:
        root_id = item.get("id") if isinstance(item, dict) else None
        path = item.get("path") if isinstance(item, dict) else None
        label = item.get("label") if isinstance(item, dict) else None
        candidate = PurePosixPath(path) if isinstance(path, str) else None
        if (isinstance(root_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", root_id)
                and candidate and candidate.is_absolute() and ".." not in candidate.parts
                and candidate.as_posix() == path and isinstance(label, str) and 0 < len(label) <= 120):
            options.append({"id": root_id, "path": path, "label": label})
    return sorted(options, key=lambda item: item["label"].casefold())


def _query_terms(query):
    required = []
    excluded = []
    for quoted, plain in re.findall(r'"([^"]+)"|(\S+)', query or ""):
        term = _normalise(quoted or plain)
        if not term:
            continue
        if term.startswith("-") and len(term) > 1:
            excluded.append(term[1:])
        else:
            required.append(term)
    return required, excluded


def _date_range():
    """Return the inclusive ISO date range requested by the Brain client."""
    raw_start = (request.args.get("start_date") or "").strip()
    raw_end = (request.args.get("end_date") or "").strip()
    try:
        start = date.fromisoformat(raw_start) if raw_start else None
        end = date.fromisoformat(raw_end) if raw_end else None
    except ValueError:
        return None, None, "Invalid ISO date range"
    if start and end and start > end:
        return None, None, "Start date must not be after end date"
    return start, end, None


def _in_date_range(value, start, end):
    try:
        current = date.fromisoformat((value or "")[:10])
    except ValueError:
        return False
    return (start is None or current >= start) and (end is None or current <= end)


def _matches_query(block, document, required, excluded):
    haystack = _normalise("\n".join((document["path"], block.get("text", ""))))
    return all(term in haystack for term in required) and not any(term in haystack for term in excluded)


def _selected_tags():
    tags = []
    for value in request.args.getlist("tags"):
        tags.extend(value.split(","))
    return {_normalise(tag.strip()) for tag in tags if tag.strip()}


def _approved_document_tags(document, tags, catalog):
    catalogs = [catalog["family"]] if document.get("source") == "family" else [catalog["personal"], catalog["family"]]
    return {
        canonical for tag in tags
        if (canonical := tagging_module.canonical_tag(tag, catalogs))
    }


def _family_editor_content(content):
    """Keep Family access metadata out of the normal Markdown editor."""
    match = _FRONTMATTER_RE.match(content or "")
    return (content or "")[match.end():] if match else (content or "")


def _family_content_with_editor_body(current, body):
    """Replace only the visible Family body while preserving its front matter."""
    match = _FRONTMATTER_RE.match(current or "")
    if not match:
        return body
    return current[:match.end()] + ("\n" if body and not body.startswith("\n") else "") + body


def _document_tags_with_status(user_id, document, content):
    catalog = tagging_module.catalog_view(user_id)
    catalogs = [catalog["family"]] if document["source"] == "family" else [catalog["personal"], catalog["family"]]
    tags = []
    for raw in _extract_tags(content):
        normalised = tagging_module.normalise_tag(raw)
        if normalised:
            tags.append({
                "name": normalised,
                "approved": bool(tagging_module.canonical_tag(normalised, catalogs)),
                "type": "ai" if normalised in catalog.get("ai", {}) else "standard",
            })
    return tags


def _file_tags_with_status(user_id, source, content):
    return _document_tags_with_status(user_id, {"source": source}, content)


def _family_management_details(user_id, content):
    metadata = _parse_frontmatter(content)
    owner_id = metadata.get("created_by") or ""
    return {
        "can_manage": owner_id == user_id,
        "assigned_users": metadata.get("assigned_users") or [],
        "created_by": owner_id,
    }


def _tag_reference_block(user_id, reference):
    """Resolve an index reference to its current visible Markdown block."""
    source = reference.get("source")
    path = reference.get("path")
    if source not in {"personal", "family"} or not isinstance(path, str):
        return None, None
    document, _, _, _ = _resolve_document(user_id, f"{source}:{path}")
    if not document:
        return None, None
    if source == "personal":
        anchor = reference.get("anchor")
        stable_anchor = f"timestamp:{anchor.replace(' ', 'T')}" if isinstance(anchor, str) else ""
        block = next((item for item in document["blocks"] if item.get("stable_anchor") == stable_anchor), None)
    else:
        line = reference.get("start_line")
        block = next((
            item for item in document["blocks"]
            if isinstance(line, int) and item["start_line"] <= line <= item["end_line"]
        ), None)
    return document, block


def _serialise_result(document, block, user_id=None):
    text = block.get("text", "").strip()
    snippet = text if len(text) <= 360 else text[:357].rstrip() + "..."
    result = {
        "doc_id": document["doc_id"],
        "source": document["source"],
        "source_label": {"personal": "Privat", "family": "Familie", "archive": "Archiv"}[document["source"]],
        "path": document["path"],
        "kind": document["kind"],
        "date": document["date"],
        "sort_at": block.get("sort_at", document["mtime"]),
        "fingerprint": block["fingerprint"],
        "start_line": block["start_line"],
        "snippet": snippet,
        "tags": block.get("tags", []),
        "manual_tags": block.get("manual_tags", []),
        "priority": block.get("priority", "normal"),
        "project": block.get("project", ""),
        "read_only": document.get("read_only", False),
    }
    if document.get("source") == "family" and user_id:
        metadata = document.get("family_metadata") or {}
        result["management"] = {
            "can_manage": metadata.get("created_by") == user_id,
            "assigned_users": metadata.get("assigned_users") or [],
            "created_by": metadata.get("created_by") or "",
        }
    return result


def _sort_results(results, ordering):
    if ordering == "oldest":
        results.sort(key=lambda item: (item["sort_at"], item["path"], item["start_line"]))
    elif ordering == "path":
        results.sort(key=lambda item: (item["path"], item["start_line"]))
    else:
        results.sort(key=lambda item: (item["sort_at"], item["path"], item["start_line"]), reverse=True)


def _search_payload(documents, user_id, index_pending):
    required, excluded = _query_terms((request.args.get("q") or "").strip())
    selected_tags = _selected_tags()
    start, end, date_error = _date_range()
    if date_error:
        return None, (jsonify({"error": date_error}), 400)
    kind = request.args.get("kind", "all")
    if kind not in {"all", "journal"}:
        return None, (jsonify({"error": "Invalid content kind"}), 400)
    catalog = tagging_module.catalog_view(user_id) if selected_tags else None
    ordering = request.args.get("order", "newest")
    if ordering not in {"newest", "oldest", "path"}:
        ordering = "newest"
    results = []
    for document in documents:
        if kind != "all" and document.get("kind") != kind:
            continue
        if not _in_date_range(document.get("date"), start, end):
            continue
        for block in document.get("blocks", []):
            if not _matches_query(block, document, required, excluded):
                continue
            if selected_tags and not selected_tags.issubset(
                _approved_document_tags(document, block.get("tags", []), catalog)
            ):
                continue
            results.append(_serialise_result(document, block, user_id))
    _sort_results(results, ordering)
    total = len(results)
    raw_limit = request.args.get("limit")
    raw_offset = request.args.get("offset", "0")
    try:
        offset = int(raw_offset)
        limit = int(raw_limit) if raw_limit is not None else total
    except ValueError:
        return None, (jsonify({"error": "Invalid result pagination"}), 400)
    if offset < 0 or limit < 0:
        return None, (jsonify({"error": "Invalid result pagination"}), 400)
    if raw_limit is not None:
        limit = min(limit, 200)
    page = results[offset:offset + limit]
    next_offset = offset + len(page)
    return {
        "results": page,
        "order": ordering,
        "index_pending": index_pending,
        "total": total,
        "has_more": next_offset < total,
        "next_offset": next_offset,
    }, None


def _visible_tag_items(documents, catalog):
    counts = {}
    for document in documents:
        for block in document.get("blocks", []):
            for tag in _approved_document_tags(document, block.get("tags", []), catalog):
                counts[tag] = counts.get(tag, 0) + 1
    family = set(catalog["family"].get("canonical", []))
    return [
        {
            "name": tag,
            "count": counts.get(tag, 0),
            "scope": "family" if tag in family else "personal",
        }
        for tag in sorted(family | set(counts))
    ]


def _indexed_personal_projects(documents):
    projects = []
    for document in documents:
        if document.get("source") != "personal" or document.get("kind") != "project":
            continue
        text = "\n".join(block.get("text", "") for block in document.get("blocks", []))
        heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        project = {
            "doc_id": document["doc_id"],
            "kind": "project",
            "source": "personal",
            "source_label": "Privat",
            "path": document["path"],
            "title": heading.group(1).strip() if heading else Path(document["path"]).stem,
            "modified_at": document["mtime"],
            "tags": [],
        }
        if document.get("ai_session"):
            project["ai_session"] = document["ai_session"]
        projects.append(project)
    return sorted(projects, key=lambda item: (_normalise(item["title"]), item["path"]))


def _document_tag_paths(user_id, source, selected_tags):
    """Resolve the current documents matching selected canonical tags once per list."""
    if not selected_tags:
        return None
    documents, _ = _visible_documents(user_id)
    catalog = tagging_module.catalog_view(user_id)
    matching = set()
    for document in documents:
        if document["source"] != source:
            continue
        tags = {
            tag for block in document.get("blocks", [])
            for tag in block.get("tags", [])
        }
        if selected_tags.issubset(_approved_document_tags(document, tags, catalog)):
            matching.add(document["path"])
    return matching


def _personal_files(user_id, kind, query="", start=None, end=None, selected_tags=None):
    root = _user_root(user_id)
    required, excluded = _query_terms(query)
    tagged_paths = _document_tag_paths(user_id, "personal", selected_tags)
    files = []
    for path in _iter_markdown(root, lambda relative: _personal_kind(relative) == kind):
        relative = _relative_path(root, path)
        content, stat = _read_source_snapshot(path)
        if not relative or content is None or stat is None:
            continue
        heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = heading.group(1).strip() if heading else path.stem
        haystack = _normalise("\n".join((title, relative, content)))
        if not all(term in haystack for term in required) or any(term in haystack for term in excluded):
            continue
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        if not _in_date_range(modified_at, start, end):
            continue
        if tagged_paths is not None and relative not in tagged_paths:
            continue
        file_data = {
            "doc_id": f"personal:{relative}",
            "kind": kind,
            "source": "personal",
            "source_label": "Privat",
            "path": relative,
            "title": title,
            "modified_at": modified_at,
            "tags": _file_tags_with_status(user_id, "personal", content),
        }
        if kind == "project":
            session = ai_sessions_module.parse_session(content, relative)
            if session:
                file_data["ai_session"] = session
        files.append(file_data)
    return sorted(files, key=lambda item: (_normalise(item["title"]), item["path"]))


def _personal_projects(user_id, query="", start=None, end=None, selected_tags=None):
    return _personal_files(user_id, "project", query, start, end, selected_tags)


def _family_files(user_id, kind, query="", start=None, end=None, selected_tags=None):
    directory_name = {"note": "notes", "project": "projects"}[kind]
    root = FAMILY_DIR / directory_name
    required, excluded = _query_terms(query)
    tagged_paths = _document_tag_paths(user_id, "family", selected_tags)
    files = []
    for path in _iter_markdown(root):
        relative = _relative_path(FAMILY_DIR, path)
        content, stat = _read_source_snapshot(path)
        if not relative or content is None or stat is None:
            continue
        if not _family_visibility(path, relative, user_id, content)[0]:
            continue
        if kind == "project":
            project = family_module.parse_project_content(content, path.name)
            heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = (project.get("title") or "").strip() or (
                heading.group(1).strip() if heading else path.stem
            )
        else:
            heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = heading.group(1).strip() if heading else path.stem
        haystack = _normalise("\n".join((title, relative, content)))
        if not all(term in haystack for term in required) or any(term in haystack for term in excluded):
            continue
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        if not _in_date_range(modified_at, start, end):
            continue
        if tagged_paths is not None and relative not in tagged_paths:
            continue
        files.append({
            "doc_id": f"family:{relative}",
            "kind": kind,
            "source": "family",
            "source_label": "Familie",
            "path": relative,
            "title": title,
            "modified_at": modified_at,
            "tags": _file_tags_with_status(user_id, "family", content),
            "management": _family_management_details(user_id, content),
        })
    return sorted(files, key=lambda item: (_normalise(item["title"]), item["path"]))


def _create_personal_file(user_id, kind, title):
    directory_name = {"note": "notes", "project": "projects"}[kind]
    fallback_slug = {"note": "notiz", "project": "projekt"}[kind]
    slug = re.sub(r"[^a-z0-9]+", "-", _normalise(title)).strip("-") or fallback_slug
    if not isinstance(user_id, str) or Path(user_id).name != user_id or user_id in {".", ".."}:
        raise ValueError("Invalid personal user directory")
    content = f"# {title}\n\n".encode("utf-8")
    data_fd = None
    user_fd = None
    directory_fd = None
    file_fd = None
    file_name = None
    created_stat = None
    try:
        data_fd = os.open(DATA_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            user_fd = os.open(user_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
        except FileNotFoundError:
            try:
                os.mkdir(user_id, 0o770, dir_fd=data_fd)
            except FileExistsError:
                pass
            user_fd = os.open(user_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
        try:
            directory_fd = os.open(
                directory_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=user_fd,
            )
        except FileNotFoundError:
            try:
                os.mkdir(directory_name, 0o770, dir_fd=user_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(
                directory_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=user_fd,
            )
        counter = 1
        while True:
            suffix = "" if counter == 1 else f"-{counter}"
            file_name = f"{slug}{suffix}.md"
            if file_name.lower() in _INDEXED_TECHNICAL_NAMES:
                counter += 1
                continue
            try:
                file_fd = os.open(
                    file_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o660,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                counter += 1
        created_stat = os.fstat(file_fd)
        file_object = os.fdopen(file_fd, "wb")
        file_fd = None
        with file_object:
            file_object.write(content)
            file_object.flush()
            os.fsync(file_object.fileno())
        stat = os.stat(file_name, dir_fd=directory_fd, follow_symlinks=False)
        if (stat.st_dev, stat.st_ino) != (created_stat.st_dev, created_stat.st_ino):
            raise OSError("Created personal file was replaced while writing")
        os.fsync(directory_fd)
    except OSError as exc:
        if file_name is not None and created_stat is not None and directory_fd is not None:
            try:
                current_stat = os.stat(file_name, dir_fd=directory_fd, follow_symlinks=False)
                if (current_stat.st_dev, current_stat.st_ino) == (created_stat.st_dev, created_stat.st_ino):
                    os.unlink(file_name, dir_fd=directory_fd)
            except OSError:
                logger.warning("Brain could not roll back failed creation of %s/%s", directory_name, file_name)
        raise ValueError(f"Personal {directory_name} directory could not be opened safely") from exc
    finally:
        for descriptor in (file_fd, directory_fd, user_fd, data_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    logger.warning("Brain could not close a personal file directory descriptor")
    relative = f"{directory_name}/{file_name}"
    return {
        "doc_id": f"personal:{relative}",
        "kind": kind,
        "path": relative,
        "title": title,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _create_family_file(directory_name, candidate):
    """Create a Family Markdown file without following directory symlinks."""
    data_fd = None
    family_fd = None
    directory_fd = None
    file_fd = None
    file_name = None
    created_stat = None
    try:
        data_fd = os.open(DATA_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            family_fd = os.open("family", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
        except FileNotFoundError:
            try:
                os.mkdir("family", 0o770, dir_fd=data_fd)
            except FileExistsError:
                pass
            family_fd = os.open("family", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
        try:
            directory_fd = os.open(
                directory_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=family_fd,
            )
        except FileNotFoundError:
            try:
                os.mkdir(directory_name, 0o770, dir_fd=family_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(
                directory_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=family_fd,
            )
        attempt = 1
        while True:
            file_name, content = candidate(attempt)
            try:
                file_fd = os.open(
                    file_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o660,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                attempt += 1
        created_stat = os.fstat(file_fd)
        file_object = os.fdopen(file_fd, "wb")
        file_fd = None
        with file_object:
            file_object.write(content.encode("utf-8"))
            file_object.flush()
            os.fsync(file_object.fileno())
        stat = os.stat(file_name, dir_fd=directory_fd, follow_symlinks=False)
        if (stat.st_dev, stat.st_ino) != (created_stat.st_dev, created_stat.st_ino):
            raise OSError("Created Family file was replaced while writing")
        os.fsync(directory_fd)
    except OSError as exc:
        if file_name is not None and created_stat is not None and directory_fd is not None:
            try:
                current_stat = os.stat(file_name, dir_fd=directory_fd, follow_symlinks=False)
                if (current_stat.st_dev, current_stat.st_ino) == (created_stat.st_dev, created_stat.st_ino):
                    os.unlink(file_name, dir_fd=directory_fd)
            except OSError:
                logger.warning("Brain could not roll back failed Family file creation of %s/%s", directory_name, file_name)
        raise ValueError(f"Family {directory_name} directory could not be opened safely") from exc
    finally:
        for descriptor in (file_fd, directory_fd, family_fd, data_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    logger.warning("Brain could not close a Family file directory descriptor")
    return file_name, stat


def _create_family_note(user_id, title):
    slug = re.sub(r"[^a-z0-9]+", "-", _normalise(title)).strip("-") or "notiz"
    now = datetime.now(timezone.utc).isoformat()
    content = (
        "---\n"
        f"assigned_users: [{user_id}]\n"
        f"created_at: {now}\n"
        f"created_by: {user_id}\n"
        "---\n\n"
        f"# {title}\n\n"
    )

    def candidate(attempt):
        suffix = "" if attempt == 1 else f"-{attempt}"
        file_name = f"{slug}{suffix}.md"
        if file_name.lower() in _INDEXED_TECHNICAL_NAMES:
            return candidate(attempt + 1)
        return file_name, content

    file_name, stat = _create_family_file("notes", candidate)
    relative = f"notes/{file_name}"
    return {
        "doc_id": f"family:{relative}",
        "kind": "note",
        "path": relative,
        "title": title,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _create_family_project(user_id, title):
    now = datetime.now(timezone.utc).isoformat()

    def candidate(_attempt):
        project_id = str(uuid.uuid4())
        content = family_module.serialize_project({
            "id": project_id,
            "title": title,
            "template_id": "",
            "target_file": "",
            "assigned_users": [user_id],
            "created_at": now,
            "created_by": user_id,
            "tasks": [],
            "comments": [],
        })
        return f"{project_id}.md", content

    file_name, stat = _create_family_file("projects", candidate)
    relative = f"projects/{file_name}"
    return {
        "doc_id": f"family:{relative}",
        "kind": "project",
        "path": relative,
        "title": title,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _enqueue_file_rebuild(user_id):
    try:
        enqueue_rebuild(user_id)
    except Exception:
        logger.exception("Brain file was created, but its background rebuild could not be queued")


def _valid_project_reference(user_id, project):
    if not project:
        return ""
    if not isinstance(project, str) or not project.startswith("projects/") or not project.endswith(".md"):
        return None
    path = _user_root(user_id) / project
    if _relative_path(_user_root(user_id), path) != project or not path.is_file() or _is_technical_file(path, _user_root(user_id)):
        return None
    return project


def _find_reference(document, reference_type, fingerprint):
    for block in document.get("blocks", []):
        if reference_type == "block" and block.get("fingerprint") == fingerprint:
            return block, block
        if reference_type == "task":
            for task in block.get("tasks", []):
                if task.get("fingerprint") == fingerprint:
                    return task, block
    return None, None


def _toggle_markdown_task(path, task, completed, user_id=None, family_relative=None):
    """Change one checked line while holding the source file's stable lock."""
    def update(content):
        if family_relative and not _family_visibility(path, family_relative, user_id, content)[0]:
            return content, {"ok": False, "forbidden": True}
        lines = content.splitlines(keepends=True)
        index = task["line"] - 1
        if index < 0 or index >= len(lines):
            return content, {"ok": False, "conflict": True}
        match = _TASK_RE.match(lines[index].rstrip("\r\n"))
        if not match or match.group("text").strip() != task["text"]:
            return content, {"ok": False, "conflict": True}
        marker_index = lines[index].find("[") + 1
        lines[index] = lines[index][:marker_index] + ("x" if completed else " ") + lines[index][marker_index + 1:]
        return "".join(lines), {"ok": True}

    return update_text_file(path, update)


def _family_structured_task(document, path, task):
    """Return a Family planner task address, or None for ordinary Markdown."""
    match = _FAMILY_TASK_ID_RE.match(task.get("text", ""))
    if not match:
        return None
    if path == FAMILY_DIR / "Familien-Aufgaben.md":
        return family_module.RECURRING_PROJECT_ID, match.group("id").strip()
    if document.get("path", "").startswith("projects/"):
        return path.stem, match.group("id").strip()
    return None


def _save_annotation(user_id, document, reference_type, reference, block, values):
    metadata_ref = reference.get("metadata_ref") or reference["fingerprint"]
    def save(metadata):
        annotation = metadata["annotations"].get(metadata_ref, {})
        annotation.update({
            "kind": reference_type,
            "doc_id": document["doc_id"],
            "path": document["path"],
            "stable_anchor": block["stable_anchor"],
            "orphaned": False,
        })
        if reference_type == "task":
            annotation["task_text"] = reference.get("identity", _task_identity(reference.get("text", "")))
            annotation["task_occurrence"] = reference.get("occurrence")
        annotation.update(values)
        metadata["annotations"][metadata_ref] = annotation
        return annotation

    return _update_metadata(user_id, save)


def record_template_assignment(user_id, filepath, project, task_text=None, line_hint=None):
    """Persist a configured template's project selection without changing Markdown."""
    valid_project = _valid_project_reference(user_id, project)
    if valid_project is None:
        return False
    root = _user_root(user_id)
    document = _build_document("personal", root, filepath, "journal")
    if not document:
        return False
    _decorate_documents(user_id, [document])
    if task_text:
        candidates = [
            (task["line"], block, task)
            for block in document["blocks"]
            for task in block["tasks"]
            if task["text"] == task_text.strip()
            and (not isinstance(line_hint, int) or task["line"] >= line_hint)
        ]
        if candidates:
            _, block, task = min(candidates, key=lambda item: item[0])
            _save_annotation(user_id, document, "task", task, block, {"project": valid_project})
            return True
    if document["blocks"]:
        candidates = [
            block for block in document["blocks"]
            if not isinstance(line_hint, int) or block["end_line"] >= line_hint
        ]
        if not candidates:
            return False
        block = min(candidates, key=lambda item: item["start_line"])
        _save_annotation(user_id, document, "block", block, block, {"project": valid_project})
        return True
    return False


@brain_bp.route("/bootstrap", methods=["GET"])
def brain_bootstrap():
    """Return the complete initial Brain view from one visible index snapshot."""
    user, error = _require_user()
    if error:
        return error
    documents, pending = _visible_documents(user["id"])
    payload, payload_error = _search_payload(documents, user["id"], pending)
    if payload_error:
        return payload_error
    catalog = tagging_module.catalog_view(user["id"])
    payload.update({
        "catalog": catalog,
        "tags": _visible_tag_items(documents, catalog),
        "projects": _indexed_personal_projects(documents),
    })
    return jsonify(payload)


@brain_bp.route("/search", methods=["GET"])
def brain_search():
    user, error = _require_user()
    if error:
        return error
    documents, pending = _visible_documents(user["id"])
    payload, payload_error = _search_payload(documents, user["id"], pending)
    return payload_error or jsonify(payload)


@brain_bp.route("/tasks", methods=["GET"])
def brain_tasks():
    user, error = _require_user()
    if error:
        return error
    status = request.args.get("status", "all")
    if status not in {"all", "open", "done"}:
        return jsonify({"error": "Invalid task status"}), 400
    priority = request.args.get("priority", "all")
    if priority not in {"all", "low", "normal", "high"}:
        return jsonify({"error": "Invalid task priority"}), 400
    selected_tags = _selected_tags()
    start, end, date_error = _date_range()
    if date_error:
        return jsonify({"error": date_error}), 400
    catalog = tagging_module.catalog_view(user["id"]) if selected_tags else None
    required, excluded = _query_terms((request.args.get("q") or "").strip())
    documents, fallback = _visible_documents(user["id"])
    tasks = []
    for document in documents:
        if not _in_date_range(document.get("date"), start, end):
            continue
        for block in document.get("blocks", []):
            for task in block.get("tasks", []):
                task_haystack = _normalise("\n".join((document["path"], task.get("text", ""))))
                if not all(term in task_haystack for term in required) or any(term in task_haystack for term in excluded):
                    continue
                if status == "open" and task["completed"]:
                    continue
                if status == "done" and not task["completed"]:
                    continue
                if priority != "all" and task.get("priority", "normal") != priority:
                    continue
                if selected_tags and not selected_tags.issubset(_approved_document_tags(document, task.get("tags", []), catalog)):
                    continue
                tasks.append({
                    "fingerprint": task["fingerprint"],
                    "metadata_ref": task.get("metadata_ref"),
                    "doc_id": document["doc_id"],
                    "source": document["source"],
                    "source_label": {"personal": "Privat", "family": "Familie", "archive": "Archiv"}[document["source"]],
                    "path": document["path"],
                    "kind": document["kind"],
                    "text": task["text"],
                    "completed": task["completed"],
                    "created_at": task["created_at"],
                    "tags": task.get("tags", []),
                    "manual_tags": task.get("manual_tags", []),
                    "priority": task.get("priority", "normal"),
                    "project": task.get("project", ""),
                    "read_only": document.get("read_only", False),
                    "block_fingerprint": block["fingerprint"],
                    "start_line": task["line"],
                })
    tasks.sort(key=lambda item: (item["created_at"], item["path"], item["start_line"]), reverse=True)
    return jsonify({"tasks": tasks, "index_pending": fallback})


@brain_bp.route("/tags", methods=["GET"])
def brain_tags():
    user, error = _require_user()
    if error:
        return error
    documents, pending = _visible_documents(user["id"])
    catalog = tagging_module.catalog_view(user["id"])
    return jsonify({"tags": _visible_tag_items(documents, catalog), "index_pending": pending})


@brain_bp.route("/hashtag-search", methods=["GET"])
def brain_hashtag_search():
    """Resolve only the source blocks pointed to by the persistent hashtag index."""
    user, error = _require_user()
    if error:
        return error
    selected = _selected_tags()
    if not selected:
        return jsonify({"results": [], "error": "At least one hashtag is required"}), 400
    ordering = request.args.get("order", "newest")
    if ordering not in {"newest", "oldest", "path"}:
        ordering = "newest"
    results = []
    seen = set()
    for reference in tagging_module.references_for_tags(user["id"], selected):
        document, block = _tag_reference_block(user["id"], reference)
        if not block or block["fingerprint"] in seen:
            continue
        seen.add(block["fingerprint"])
        item = _serialise_result(document, block, user["id"])
        item["tags"] = reference.get("tags", [])
        results.append(item)
    _sort_results(results, ordering)
    return jsonify({"results": results, "order": ordering, "index_pending": tagging_module.get_index().get("rebuilt_at") is None})


@brain_bp.route("/tagging/run", methods=["POST"])
def brain_tagging_run():
    """Tag personal journal files with the explicitly selected local LLM."""
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    start, end = data.get("start_date", ""), data.get("end_date", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) or start > end:
        return jsonify({"error": "An inclusive ISO date range is required"}), 400
    main = _main_module()
    config = main.load_config() if main else {}
    provider = next((item for item in config.get("ai_providers", []) if item.get("id") == data.get("provider_id")), None)
    if not provider or not str(provider.get("id", "")).startswith("lm_"):
        return jsonify({"error": "Select a configured local LM Studio provider"}), 400
    try:
        report = historical_tagging.run_historical_tagging(
            user["id"], start, end, provider,
            config.get("historical_tagging_ai"), main._call_ai_api,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    enqueue_rebuild(user["id"])
    logger.info(
        "Historical tagging finished for %s: processed=%d skipped=%d errors=%d",
        user["id"], report["processed"], report["skipped"], len(report["errors"]),
    )
    return jsonify(report)


@brain_bp.route("/tag-catalog", methods=["GET", "POST"])
def brain_tag_catalog():
    user, error = _require_user()
    if error:
        return error
    if request.method == "GET":
        return jsonify({
            "catalog": tagging_module.catalog_view(user["id"]),
            "knowledge_source_options": knowledge_source_options(user["id"]),
            "write_target_options": write_target_options(user["id"]),
            "external_write_roots": external_write_root_options(),
            "can_manage": _is_admin(user),
            "can_manage_personal": True,
            "can_manage_family": _is_admin(user),
            "can_manage_ai": True,
        })
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    if data.get("scope") == "ai":
        try:
            catalog = tagging_module.update_ai_workflow(
                user["id"], data.get("action"), data.get("tag"), data.get("workflow"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        enqueue_rebuild(user["id"])
        return jsonify({"ok": True, "workflow": catalog})
    if data.get("scope") == "knowledge":
        family = (data.get("source") or {}).get("family") is True
        if family and not _is_admin(user):
            return jsonify({"error": "Admin access is required for Family knowledge sources"}), 403
        try:
            source = dict(data.get("source") or {})
            source.pop("family", None)
            if data.get("action") == "save":
                # Never trust a path selected in the browser.  This also makes
                # catalog entries fail closed when a file was deleted or lost
                # its Family permission between selection and save.
                resolve_knowledge_source(user["id"], "family" if family else "personal", source.get("path"))
            result = tagging_module.update_knowledge_source(user["id"], family, data.get("action"), data.get("tag"), source)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "source": result})
    if data.get("scope") == "write_target":
        family = (data.get("target") or {}).get("family") is True
        if family and not _is_admin(user):
            return jsonify({"error": "Admin access is required for Family write targets"}), 403
        try:
            target = dict(data.get("target") or {})
            target.pop("family", None)
            if data.get("action") == "save":
                if target.get("root_id"):
                    root = next((item for item in external_write_root_options() if item["id"] == target.get("root_id")), None)
                    path = str(target.get("path") or "")
                    if not root or path != root["path"] and not path.startswith(root["path"].rstrip("/") + "/"):
                        raise ValueError("Linux-Pfad liegt nicht unter einer freigegebenen Host-Wurzel")
                else:
                    resolve_write_target(user["id"], "family" if family else "personal", target.get("path"))
            result = tagging_module.update_write_target(user["id"], family, data.get("action"), data.get("tag"), target)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "target": result})
    if data.get("scope") not in {"personal", "family"}:
        return jsonify({"error": "Unknown hashtag scope"}), 400
    if data.get("scope") == "family" and not _is_admin(user):
        return jsonify({"error": "Admin access is required for Family hashtags"}), 403
    try:
        catalog = tagging_module.update_catalog(
            user["id"], data.get("scope") == "family", data.get("action"), data.get("tag"), data.get("target", ""),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    enqueue_rebuild("__all__")
    return jsonify({"ok": True, "catalog": catalog})


@brain_bp.route("/document", methods=["GET"])
def brain_document_get():
    user, error = _require_user()
    if error:
        return error
    document, _, _, content = _resolve_document(user["id"], request.args.get("doc_id"))
    if not document:
        return jsonify({"error": "Not found"}), 404
    requested_fingerprint = request.args.get("fingerprint", "")
    block = next((item for item in document["blocks"] if item["fingerprint"] == requested_fingerprint), None)
    response = {
        "doc_id": document["doc_id"],
        "source": document["source"],
        "source_label": {"personal": "Privat", "family": "Familie", "archive": "Archiv"}[document["source"]],
        "path": document["path"],
        "content": _family_editor_content(content) if document["source"] == "family" else content,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "read_only": document["read_only"],
        "block_start_line": block["start_line"] if block else None,
        "tags": _document_tags_with_status(user["id"], document, content),
    }
    if document["source"] == "family":
        response["management"] = _family_management_details(user["id"], content)
    response["agent_session"] = ai_sessions_module.document_session_status(content)
    return jsonify(response)


@brain_bp.route("/document", methods=["PUT"])
def brain_document_put():
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    document, path, root, _ = _resolve_document(user["id"], data.get("doc_id"))
    if not document:
        return jsonify({"error": "Not found"}), 404
    if document["read_only"]:
        return jsonify({"error": "Document is read-only"}), 403
    if document["source"] == "family" and data.get("confirm_family_edit") is not True:
        return jsonify({"error": "Family edits require explicit confirmation"}), 400
    content = data.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "content (string) required"}), 400
    expected_hash = data.get("content_hash")
    if not isinstance(expected_hash, str) or not _HASH_RE.fullmatch(expected_hash):
        return jsonify({"error": "content_hash (SHA-256) required"}), 400

    with _index_guard(document["source"], user["id"] if document["source"] == "personal" else None, exclusive=True):
        def update(current):
            if document["source"] == "family" and not _family_visibility(
                path, document["path"], user["id"], current
            )[0]:
                return current, {"forbidden": True}
            current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
            if expected_hash != current_hash:
                return current, {"conflict": True}
            updated_content = _family_content_with_editor_body(current, content) if document["source"] == "family" else content
            updated_content, session_state = ai_sessions_module.prepare_document_save(
                user["id"], updated_content, current,
                is_journal=document["source"] == "personal" and document["kind"] == "journal",
                actor_id=user["id"],
            )
            if document["source"] == "personal" and document["kind"] == "journal":
                date_match = re.fullmatch(r"Journal_(\d{4}-\d{2}-\d{2})\.md", path.name)
                journal_date = date_match.group(1) if date_match else None
                blocks = tagging_module.journal_blocks(updated_content, journal_date)
                tagging_module.propose_tags(user["id"], [tag for block in blocks for tag in block["raw_tags"]])
                content_with_footer = tagging_module.refresh_journal_footer(user["id"], updated_content, journal_date)
                return content_with_footer, {"ok": True, "agent_session": session_state}
            return updated_content, {"ok": True, "agent_session": session_state}

        result = update_text_file(path, update)
        if result.get("forbidden"):
            return jsonify({"error": "Not found"}), 404
        if result.get("conflict"):
            return jsonify({"error": "Source changed in the background", "conflict": True}), 409
        updated = _build_document(document["source"], root, path, document["kind"])
        if updated:
            _write_index_document(
                document["source"], updated,
                user["id"] if document["source"] == "personal" else None,
            )
        else:
            _delete_index_document(
                document["source"], document["doc_id"],
                user["id"] if document["source"] == "personal" else None,
            )
    enqueue_rebuild(user["id"])
    return jsonify({
        "ok": True,
        "content_hash": hashlib.sha256((_read_text(path) or "").encode("utf-8")).hexdigest(),
        "agent_session": result.get("agent_session"),
        "indexing": "periodic",
    })


@brain_bp.route("/document/agent-session", methods=["POST"])
def brain_document_agent_session_action():
    """Pause, resume or end an authorised source-document session."""
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    document, path, root, _ = _resolve_document(user["id"], data.get("doc_id"))
    if not document or document["read_only"]:
        return jsonify({"error": "Not found"}), 404
    if document["source"] == "family" and data.get("confirm_family_edit") is not True:
        return jsonify({"error": "Family edits require explicit confirmation"}), 400
    action = data.get("action")
    try:
        def update(current):
            if document["source"] == "family" and not _family_visibility(path, document["path"], user["id"], current)[0]:
                return current, {"forbidden": True}
            updated, status = ai_sessions_module.set_document_session_status(current, action, user["id"])
            if document["source"] == "personal" and document["kind"] == "journal":
                date_match = _JOURNAL_DATE_RE.fullmatch(path.name)
                updated = tagging_module.refresh_journal_footer(user["id"], updated, date_match.group(1) if date_match else None)
            return updated, {"status": status}
        result = update_text_file(path, update)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if result.get("forbidden"):
        return jsonify({"error": "Not found"}), 404
    enqueue_rebuild(user["id"])
    return jsonify({"ok": True, "agent_session": result.get("status")})


@brain_bp.route("/document/access", methods=["PUT"])
def brain_document_access_put():
    """Let only a Family-file creator manage its protected access header."""
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    document, path, root, _ = _resolve_document(user["id"], data.get("doc_id"))
    if not document or document["source"] != "family":
        return jsonify({"error": "Not found"}), 404
    assigned_users = data.get("assigned_users")
    if not isinstance(assigned_users, list) or any(not isinstance(item, str) for item in assigned_users):
        return jsonify({"error": "assigned_users must be a list of user IDs"}), 400
    main = _main_module()
    valid_users = {item.get("id") for item in main._read_users_file().get("users", [])} if main else set()
    requested = sorted({item.strip() for item in assigned_users if item.strip()})
    if any(item not in valid_users for item in requested):
        return jsonify({"error": "Unknown user"}), 400
    expected_hash = data.get("content_hash")
    if not isinstance(expected_hash, str) or not _HASH_RE.fullmatch(expected_hash):
        return jsonify({"error": "content_hash (SHA-256) required"}), 400

    with _index_guard("family", exclusive=True):
        def update(current):
            metadata = _parse_frontmatter(current)
            if metadata.get("created_by") != user["id"]:
                return current, {"forbidden": True}
            if hashlib.sha256(current.encode("utf-8")).hexdigest() != expected_hash:
                return current, {"conflict": True}
            updated_users = sorted(set(requested) | {user["id"]})
            lines = current.replace("\r\n", "\n").splitlines(keepends=True)
            for index, line in enumerate(lines):
                if line.startswith("assigned_users:"):
                    lines[index] = "assigned_users: [" + ", ".join(updated_users) + "]\n"
                    return "".join(lines), {"ok": True}
            return current, {"conflict": True}

        result = update_text_file(path, update)
        if result.get("forbidden"):
            return jsonify({"error": "Only the creator may manage access"}), 403
        if result.get("conflict"):
            return jsonify({"error": "Source changed in the background", "conflict": True}), 409
        updated = _build_document("family", root, path, document["kind"])
        if updated:
            _write_index_document("family", updated)
    enqueue_rebuild(user["id"])
    content = _read_text(path) or ""
    return jsonify({
        "ok": True,
        "assigned_users": _parse_frontmatter(content).get("assigned_users") or [],
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    })


@brain_bp.route("/task/toggle", methods=["POST"])
def brain_task_toggle():
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    completed = data.get("completed")
    if not isinstance(completed, bool):
        return jsonify({"error": "completed (boolean) required"}), 400
    document, path, root, _ = _resolve_document(user["id"], data.get("doc_id"))
    if not document:
        return jsonify({"error": "Not found"}), 404
    if document["read_only"]:
        return jsonify({"error": "Archive tasks are read-only"}), 403
    task, _ = _find_reference(document, "task", data.get("fingerprint"))
    if not task:
        return jsonify({"error": "Task was changed and cannot be resolved", "conflict": True}), 409
    with _index_guard(document["source"], user["id"] if document["source"] == "personal" else None, exclusive=True):
        structured_task = _family_structured_task(document, path, task) if document["source"] == "family" else None
        if structured_task:
            project_id, task_id = structured_task
            result = family_module.set_task_completion(
                project_id,
                task_id,
                user["id"],
                completed,
                can_access=lambda current: _family_visibility(path, document["path"], user["id"], current)[0],
            )
            if result.get("forbidden"):
                return jsonify({"error": "Not found"}), 404
            if not result.get("found"):
                return jsonify({"error": "Task was changed and cannot be resolved", "conflict": True}), 409
        else:
            result = _toggle_markdown_task(
                path,
                task,
                completed,
                user["id"] if document["source"] == "family" else None,
                document["path"] if document["source"] == "family" else None,
            )
            if result.get("forbidden"):
                return jsonify({"error": "Not found"}), 404
            if result.get("conflict"):
                return jsonify({"error": "Task was changed and cannot be resolved", "conflict": True}), 409

        # This guarded publication keeps the visible checkbox state ahead of the
        # periodic full rebuild without permitting an older scan to overwrite it.
        updated = _build_document(document["source"], root, path, document["kind"])
        if updated:
            _write_index_document(document["source"], updated, user["id"] if document["source"] == "personal" else None)
        else:
            _delete_index_document(
                document["source"], document["doc_id"],
                user["id"] if document["source"] == "personal" else None,
            )
    enqueue_rebuild(user["id"])
    return jsonify({"ok": True, "completed": completed})


@brain_bp.route("/metadata", methods=["POST"])
def brain_metadata():
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    reference_type = data.get("reference_type")
    if reference_type not in {"block", "task"}:
        return jsonify({"error": "reference_type must be block or task"}), 400
    document, _, _, _ = _resolve_document(user["id"], data.get("doc_id"))
    if not document:
        return jsonify({"error": "Not found"}), 404
    reference, block = _find_reference(document, reference_type, data.get("fingerprint"))
    if not reference:
        return jsonify({"error": "Reference was changed and cannot be resolved", "conflict": True}), 409
    values = {}
    if "tags" in data:
        tags = data["tags"]
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            return jsonify({"error": "tags must be a list of strings"}), 400
        values["tags"] = sorted({_normalise(tag.strip()) for tag in tags if tag.strip()})
    if "priority" in data:
        priority = data["priority"]
        if priority not in {"low", "normal", "high"}:
            return jsonify({"error": "Invalid priority"}), 400
        values["priority"] = priority
    if "project" in data:
        project = _valid_project_reference(user["id"], data["project"])
        if project is None:
            return jsonify({"error": "Unknown personal project"}), 400
        values["project"] = project
    if not values:
        return jsonify({"error": "At least one metadata field is required"}), 400
    try:
        annotation = _save_annotation(user["id"], document, reference_type, reference, block, values)
    except ValueError:
        return jsonify({"error": "Brain metadata is corrupt and was not changed"}), 409
    return jsonify({"ok": True, "metadata": annotation})


@brain_bp.route("/family", methods=["GET"])
def brain_family_get():
    user, error = _require_user()
    if error:
        return error
    query = (request.args.get("q") or "").strip()
    start, end, date_error = _date_range()
    if date_error:
        return jsonify({"error": date_error}), 400
    selected_tags = _selected_tags()
    return jsonify({
        "notes": _family_files(user["id"], "note", query, start, end, selected_tags),
        "projects": _family_files(user["id"], "project", query, start, end, selected_tags),
    })


def _family_file_title(data, kind):
    raw_title = data.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    label = "Note" if kind == "note" else "Project"
    if not title or len(title) > 120 or any(char in title for char in "\r\n"):
        raise ValueError(f"{label} title must be between 1 and 120 characters")
    try:
        title.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} title must be valid UTF-8") from exc
    return title


@brain_bp.route("/family/notes", methods=["POST"])
def brain_family_notes_post():
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    try:
        title = _family_file_title(request.get_json(silent=True) or {}, "note")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        note = _create_family_note(user["id"], title)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    _enqueue_file_rebuild(user["id"])
    return jsonify({"ok": True, "note": note}), 201


@brain_bp.route("/family/projects", methods=["POST"])
def brain_family_projects_post():
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    try:
        title = _family_file_title(request.get_json(silent=True) or {}, "project")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        project = _create_family_project(user["id"], title)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    _enqueue_file_rebuild(user["id"])
    return jsonify({"ok": True, "project": project}), 201


@brain_bp.route("/projects", methods=["GET"])
def brain_projects_get():
    user, error = _require_user()
    if error:
        return error
    query = (request.args.get("q") or "").strip()
    start, end, date_error = _date_range()
    if date_error:
        return jsonify({"error": date_error}), 400
    return jsonify({"projects": _personal_projects(user["id"], query, start, end, _selected_tags())})


@brain_bp.route("/projects", methods=["POST"])
def brain_projects_post():
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    raw_title = data.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title or len(title) > 120 or any(char in title for char in "\r\n"):
        return jsonify({"error": "Project title must be between 1 and 120 characters"}), 400
    try:
        title.encode("utf-8")
    except UnicodeEncodeError:
        return jsonify({"error": "Project title must be valid UTF-8"}), 400
    try:
        project = _create_personal_file(user["id"], "project", title)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    _enqueue_file_rebuild(user["id"])
    return jsonify({"ok": True, "project": project}), 201


@brain_bp.route("/notes", methods=["GET"])
def brain_notes_get():
    user, error = _require_user()
    if error:
        return error
    query = (request.args.get("q") or "").strip()
    start, end, date_error = _date_range()
    if date_error:
        return jsonify({"error": date_error}), 400
    return jsonify({"notes": _personal_files(user["id"], "note", query, start, end, _selected_tags())})


@brain_bp.route("/notes", methods=["POST"])
def brain_notes_post():
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    raw_title = data.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title or len(title) > 120 or any(char in title for char in "\r\n"):
        return jsonify({"error": "Note title must be between 1 and 120 characters"}), 400
    try:
        title.encode("utf-8")
    except UnicodeEncodeError:
        return jsonify({"error": "Note title must be valid UTF-8"}), 400
    try:
        note = _create_personal_file(user["id"], "note", title)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    _enqueue_file_rebuild(user["id"])
    return jsonify({"ok": True, "note": note}), 201


@brain_bp.route("/index/rebuild", methods=["POST"])
def brain_index_rebuild():
    user, error = _require_user()
    if error:
        return error
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    queued = enqueue_rebuild(user["id"])
    return jsonify({"ok": True, "queued": queued, "interval_seconds": INDEX_INTERVAL_SECONDS}), 202
