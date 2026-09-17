"""Brain Markdown document parsing and normalization.

AI maintenance contract:
Own frontmatter, blocks, tags, task identities and indexed document construction.
Do not add HTTP, worker scheduling or editor mutation here.
Public compatibility names are re-exported by brain.py. Always import the
facade from application code so test-replaceable DATA_DIR/FAMILY_DIR values are
synchronized before execution.
"""

import sys

import brain_chat_sessions

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before domain modules are imported")
globals().update({name: getattr(_brain, name) for name in dir(_brain) if not name.startswith("__")})


def _sync_from_facade():
    for name in ("DATA_DIR", "FAMILY_DIR", "INDEX_INTERVAL_SECONDS"):
        if hasattr(_brain, name):
            globals()[name] = getattr(_brain, name)


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


def prepare_search_text(document):
    """Precompute the normalised haystack that blocks and tasks are matched against.

    ``_matches_query`` used to rebuild this string for every block on every
    request - an NFKD decomposition with a per-character Python loop over the
    whole journal text, measured at 178 ms for 2503 blocks, once per keystroke
    and a second time for the tag facets. Text and the hashtags standing in the
    file are fixed the moment the index knows the document, so the work belongs
    here: once per index generation instead of once per request.

    Only the signed-in user's manual annotations are missing at this point -
    they live in ``brain_metadata.json``, not in the file. ``_matches_query``
    appends those itself, which stays cheap because a block rarely carries any.
    """
    path = document.get("path", "")
    for block in document.get("blocks", []):
        # The same sorted union the decoration needs as ``tags``: computing it
        # here means an unannotated block - the normal case - reuses the list
        # instead of rebuilding two sets and sorting them on every request.
        file_tags = sorted(set(block.get("tags_auto", [])) | set(block.get("ai_tags", [])))
        block["tags_base"] = file_tags
        tag_lines = [f"#{tag}" for tag in file_tags]
        block["search_base"] = _normalise("\n".join([path, block.get("text", "")] + tag_lines))
        for task in block.get("tasks", []):
            task["search_base"] = _normalise("\n".join([path, task.get("text", "")] + tag_lines))
    return document


def without_search_text(document):
    """Return the document as it belongs on disk, without the derived haystack.

    ``search_base`` and ``tags_base`` are derived from fields the file already
    carries. Persisting
    it would double the index on disk and, worse, let a derived value go stale
    against the text it was derived from. Rebuilding it on load is the cheaper
    and the safer half of that trade.
    """
    blocks = []
    for block in document.get("blocks", []):
        stripped = {
            key: value for key, value in block.items()
            if key not in {"search_base", "tags_base"}
        }
        if stripped.get("tasks"):
            stripped["tasks"] = [
                {key: value for key, value in task.items() if key != "search_base"}
                for task in stripped["tasks"]
            ]
        blocks.append(stripped)
    return {**document, "blocks": blocks}


def search_haystack(item, manual_tags):
    """Return the text one block or task is matched against, manual tags included.

    Falls back to no haystack at all when the item never passed through
    ``prepare_search_text`` - callers then keep their own construction, which is
    what the test fixtures with hand-built skeleton documents rely on.
    """
    base = item.get("search_base")
    if base is None:
        return None
    if not manual_tags:
        return base
    return base + "\n" + _normalise("\n".join(f"#{tag}" for tag in manual_tags))


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


def _is_technical_relative(relative, name=None):
    """Decide from the relative path alone whether a file stays out of the index."""
    if not relative:
        return True
    parts = PurePosixPath(relative).parts
    lower_parts = [part.lower() for part in parts]
    if any(part.startswith(".") for part in parts):
        return True
    if any(part in _TECHNICAL_DIRS for part in lower_parts[:-1]):
        return True
    name = (name or parts[-1]).lower()
    if name in _INDEXED_TECHNICAL_NAMES:
        return True
    return name.endswith((".bak", ".backup", ".tmp", ".lock"))


def _is_technical_file(path, root):
    return _is_technical_relative(_relative_path(root, path), path.name)


def _iter_markdown_entries(root, allowed=None):
    """Yield ``(path, relative, stat)`` for every indexable Markdown file below root.

    Callers used to get bare paths and then compute the relative path a second
    time and stat the file themselves. Both values are already known here, and
    recomputing them was the single most expensive part of a Brain request:
    ``_relative_path`` resolves the file *and* the root on every call, and
    ``_is_technical_file`` resolved both a second time - measured at 181 ms per
    request for one root of 759 files, where the bare stat costs 4 ms.

    ``os.walk(followlinks=False)`` never descends into a linked directory, so a
    path cannot leave its root the way it could through ``rglob`` - which is why
    the resolve was needed there in the first place. The file-level symlink
    check stays, and ``_relative_path`` keeps its ``resolve()`` for callers that
    hand in an arbitrary path: there it is the security check, here it was
    redundant work.
    """
    if not root.is_dir():
        return []
    root_str = str(root)
    entries = []
    for current, directories, files in os.walk(root_str, followlinks=False):
        prefix = os.path.relpath(current, root_str)
        prefix = "" if prefix == "." else prefix.replace(os.sep, "/")
        # Technische und versteckte Ordner gar nicht erst betreten - dieselbe
        # Regel, die _is_technical_relative auf den Pfadteilen anwendet.
        directories[:] = [
            name for name in directories
            if not name.startswith(".") and name.lower() not in _TECHNICAL_DIRS
        ]
        for name in files:
            if not name.endswith(".md"):
                continue
            relative = f"{prefix}/{name}" if prefix else name
            if _is_technical_relative(relative, name):
                continue
            if allowed and not allowed(relative):
                continue
            path = Path(current) / name
            if path.is_symlink():
                continue
            try:
                entries.append((path, relative, path.stat()))
            except OSError:
                continue
    entries.sort(key=lambda entry: entry[1])
    return entries


def _iter_markdown(root, allowed=None):
    return [path for path, _, _ in _iter_markdown_entries(root, allowed)]


def _personal_kind(relative):
    if relative.startswith("projects/"):
        return "project"
    if relative.startswith("notes/"):
        return "note"
    if relative.startswith("ai-agent/"):
        return "agent"
    if _JOURNAL_PATH_RE.match(relative):
        return "journal"
    # Chatverlaeufe liegen im selben Tagesordner wie das Journal. Ohne diese
    # Zeile waeren sie fuer den Index unsichtbar — genau das Loch, das die
    # Journal-Diagnose als "uebergangene Datei" meldet.
    if brain_chat_sessions.is_session_path(relative):
        return "chat"
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

    # A completed project keeps its own front matter under archive/projects/.
    # It carries a `type`, so `assigned_users: []` genuinely means "visible to
    # everyone" and must not be routed through the legacy task-archive rules
    # below, which would deny it for lack of a matching template.
    is_family_document = str(metadata.get("type") or "").strip().casefold() in \
        family_module.family_views.VALID_TYPES

    # Archived tasks do not retain a full project front matter in old files. Use
    # their still-existing project as the authority; unknown legacy archives are
    # denied rather than revealing an old restricted task.
    if relative.startswith("archive/") and not assigned and not is_family_document:
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


_FULL_ANCHOR_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _machine_tag_lookup(footer):
    """Map every anchor spelling a block can carry to the hashtags a model gave it.

    The footer normalises each anchor to a full timestamp, while a block keeps
    the spelling of its own source line — a quick note has no year and a legacy
    quick note only a clock. Comparing the full form alone would leave exactly
    those blocks without their machine origin.
    """
    lookup = {}
    for anchor, tags in tagging_module.footer_ai_tags(footer).items():
        cleaned = sorted({_normalise(tag) for tag in tags} - {""})
        full = anchor.replace(" ", "T")
        if not cleaned or not _FULL_ANCHOR_RE.fullmatch(full):
            continue
        for candidate in (full, full[5:], full[11:]):
            lookup[f"timestamp:{candidate}"] = cleaned
    return lookup


def _block_anchor(text, start_line):
    datetime_match = _DATETIME_RE.search(text)
    if datetime_match:
        return f"timestamp:{datetime_match.group(1).replace(' ', 'T')}", True
    quick_match = _QUICK_DATE_TIME_RE.search(text)
    if quick_match:
        day, month, clock = quick_match.groups()
        return f"timestamp:{month}-{day}T{clock}", True
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


def _quick_end_time(block_text):
    """Return a quick note's submit marker in the anchor's time format.

    A note started before midnight is submitted into the next day's journal.
    Sorting it by its start marker would throw it back among the previous day's
    entries, so the submit time — the one that decided the journal file — wins.
    """
    matches = _QUICK_END_TIME_RE.findall(str(block_text or ""))
    if len(matches) != 1:
        return ""
    day, month, clock = matches[0]
    return f"{month}-{day}T{clock}" if day and month else clock


def _sort_time(journal_date, anchor, mtime, block_text=""):
    if journal_date:
        time_value = anchor.removeprefix("timestamp:")
        if re.fullmatch(r"(?:\d{2}-\d{2}T)?\d{2}:\d{2}:\d{2}", time_value):
            time_value = _quick_end_time(block_text) or time_value
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}", time_value):
            value = f"{journal_date}T{time_value}"
        elif re.fullmatch(r"\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", time_value):
            value = f"{journal_date[:4]}-{time_value}"
            try:
                if date.fromisoformat(value[:10]) > date.fromisoformat(journal_date):
                    value = f"{int(journal_date[:4]) - 1}-{time_value}"
            except ValueError:
                value = f"{journal_date}T00:00:00"
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


# ─── Family-Planerzeilen lesbar machen ────────────────────────────────
# family.serialize_project schreibt Aufgaben und Kommentare als Maschinenzeile
# ("id: … | title: … | user: … | completed_at: …"). Im Familie-Tab sieht man
# davon nie etwas, in Brain stand bisher die Rohzeile auf der Karte. Hier wird
# sie in genau das zurueckuebersetzt, was ein Mensch geschrieben hat.
_FAMILY_TITLE_MARKER_RE = re.compile(r"^\[[ xX]\]\s*")
_FAMILY_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
_FAMILY_BULLET_RE = re.compile(r"^\s*[-*+]\s+")
# Nur diese Felder haben eine Bedeutung fuer den Leser. Alles andere sind IDs,
# Benutzer-UUIDs, Sortierindizes und Zeitstempel.
_FAMILY_DISPLAY_FIELDS = (("target-date", "fällig "), ("recurrence", ""), ("gruppe", ""))


def _family_metadata_fields(body):
    """Split a Family planner line into its ``key: value | …`` fields.

    Returns None for ordinary Markdown so a hand-written family note keeps its
    own wording: only a line that carries an id plus a title/text is one of the
    generated ones.
    """
    if "|" not in body:
        return None
    fields = {}
    for part in body.split("|"):
        key, separator, value = part.partition(":")
        if not separator:
            return None
        fields[key.strip().lower()] = value.strip()
    if not fields.get("id") or not (fields.get("title") or fields.get("text")):
        return None
    return fields


def _family_line_fields(line):
    """Fields of a generated Family list line, or None for ordinary Markdown."""
    task = _TASK_RE.match(line)
    if task:
        return _family_metadata_fields(task.group("text").strip())
    bullet = _FAMILY_BULLET_RE.match(line)
    if not bullet:
        return None
    return _family_metadata_fields(line[bullet.end():].strip())


def _family_entry_label(line):
    """The human wording of a generated Family line, without list markers."""
    fields = _family_line_fields(line)
    if fields is None:
        return None
    # Alte Einkaufsartikel tragen den Haken noch einmal im Titel selbst.
    label = _FAMILY_TITLE_MARKER_RE.sub("", fields.get("title") or fields.get("text") or "").strip()
    if not label:
        return None
    extras = [prefix + fields[key] for key, prefix in _FAMILY_DISPLAY_FIELDS if fields.get(key)]
    return f"{label} ({' · '.join(extras)})" if extras else label


def _family_line_display(line):
    """Readable form of one Family planner line, or None if it is not one."""
    label = _family_entry_label(line)
    if label is None:
        return None
    indent = line[:len(line) - len(line.lstrip())]
    task = _TASK_RE.match(line)
    if not task:
        return f"{indent}- {label}"
    return f"{indent}- [{'x' if task.group('state').lower() == 'x' else ' '}] {label}"


def _family_display_text(text):
    """Rewrite every generated Family line in a block, leave the rest alone."""
    rendered = []
    changed = False
    for line in (text or "").splitlines():
        display = _family_line_display(line)
        if display is None:
            rendered.append(line)
        else:
            rendered.append(display)
            changed = True
    return "\n".join(rendered) if changed else text


def _family_body_offset(content):
    """Index of the first body line after the Family front matter."""
    lines = (content or "").splitlines()
    if not lines or lines[0].lstrip("﻿") != "---":
        return 0
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    return 0 if closing is None else closing + 1


def _is_family_planner_content(source, content):
    """True for a project file written by family.serialize_project."""
    if source != "family" or not content:
        return False
    metadata = _parse_frontmatter(content)
    if not metadata.get("id") or not metadata.get("_frontmatter_valid"):
        return False
    for line in content.splitlines():
        heading = _FAMILY_HEADING_RE.match(line.strip())
        if heading and heading.group(1).strip().lower() == "aufgaben":
            return True
    return False


def _family_planner_display(content):
    """One readable card text for a whole Family project file.

    The file's two headings used to become two Brain blocks — an "Aufgaben" and
    an almost always empty "Kommentare" card for the same list. One document is
    one card here, and an empty section drops out entirely.
    """
    metadata = _parse_frontmatter(content)
    lines = (content or "").splitlines()[_family_body_offset(content):]
    sections = []
    current = None
    for line in lines:
        stripped = line.strip()
        heading = _FAMILY_HEADING_RE.match(stripped)
        if heading:
            current = (heading.group(1).strip(), [])
            sections.append(current)
            continue
        if not stripped:
            continue
        if current is None:
            current = ("", [])
            sections.append(current)
        current[1].append(_family_line_display(line) or line.rstrip())
    output = []
    title = (metadata.get("title") or "").strip()
    if title:
        output.append(f"## {title}")
    for index, (heading, entries) in enumerate([item for item in sections if item[1]]):
        # Die erste Sektion ist immer "Aufgaben" — unter der Listenueberschrift
        # waere das eine Zeile ohne Aussage.
        if heading and not (index == 0 and title):
            output.append(f"### {heading}")
        output.extend(entries)
    return "\n".join(output)


def _family_planner_raw_body(content):
    """The planner file's body, still line-for-line identical to the source.

    Task line numbers are taken from this text, so it must keep the original
    line offsets even though the card shows the rewritten version.
    """
    return "\n".join((content or "").splitlines()[_family_body_offset(content):])


def _family_planner_sort_time(content, mtime_timestamp):
    """Sort a Family list by its newest entry, not by its file time.

    Checking an item off rewrites the file, so the mtime would push the list to
    the top of the Brain timeline on every tick. Only a new item or comment
    should do that.
    """
    def moment(value):
        try:
            parsed = datetime.fromisoformat((value or "").strip())
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_local_timezone())
        return parsed.timestamp()

    newest = 0.0
    for line in (content or "").splitlines():
        fields = _family_line_fields(line)
        if fields:
            newest = max(newest, moment(fields.get("created_at")))
    return newest or moment(_parse_frontmatter(content).get("created_at")) or mtime_timestamp


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
    machine_tags = {}
    if kind == "journal":
        content, footer = tagging_module.strip_footer(content)
        # A model's hashtags live in the footer, never in the block text. Without
        # this the Brain card would show no trace of them at all.
        machine_tags = _machine_tag_lookup(footer)
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
    # Eine Familienliste ist ein Dokument, kein Stapel Ueberschriften: sonst
    # stuenden "Aufgaben" und das meist leere "Kommentare" als zwei Karten fuer
    # dieselbe Liste in der Trefferliste.
    planner = _is_family_planner_content(source, content)
    block_ranges = (
        [(_family_planner_raw_body(content), _family_body_offset(content) + 1, len(content.splitlines()))]
        if planner else _split_blocks(content, kind == "journal")
    )
    for block_text, start_line, end_line in block_ranges:
        # Der Rohtext bestimmt Zeilennummern und Aufgabenzustand, der
        # Anzeigetext alles, was ein Mensch zu sehen bekommt.
        display_text = (
            _family_planner_display(content) if planner
            else (_family_display_text(block_text) if source == "family" else block_text)
        )
        # Die Zeilennummer taugt hier nicht als Anker: family._save_project
        # schreibt die Datei komplett neu und kann die Frontmatter dabei um
        # Felder verlaengern. Die Projekt-ID ueberlebt das.
        anchor, stable = (
            (f"family-project:{_parse_frontmatter(content).get('id')}", True) if planner
            else _block_anchor(block_text, start_line)
        )
        legacy_block_fingerprint = _fingerprint(doc_id, anchor, _normalise(block_text))
        # Die Identitaet kommt aus dem Anzeigetext: in der Rohzeile steht
        # `completed_at`, ein Haken wuerde sonst den Fingerprint wechseln und
        # jede Blockmarkierung ins Leere laufen lassen.
        block_base = _fingerprint(doc_id, anchor, _block_identity(display_text))
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
            task = {
                "fingerprint": task_fingerprint,
                "legacy_fingerprint": legacy_task_fingerprint,
                "line": start_line + offset,
                "text": task_text,
                "identity": task_identity,
                "completed": task_match.group("state").lower() == "x",
                "occurrence": occurrence,
            }
            # Der Rohtext bleibt die Adresse der Zeile, die Anzeige daneben ist
            # das, was jemand tatsaechlich eingetippt hat.
            display_label = _family_entry_label(line) if source == "family" else None
            if display_label:
                task["display_text"] = display_label
            tasks.append(task)
        blocks.append({
            "fingerprint": block_fingerprint,
            "legacy_fingerprint": legacy_block_fingerprint,
            "stable_anchor": anchor,
            "has_stable_anchor": stable,
            "start_line": start_line,
            "end_line": end_line,
            "text": display_text,
            "tags_auto": _extract_tags(display_text),
            "ai_tags": machine_tags.get(anchor, []),
            "tasks": tasks,
            "sort_at": (
                _family_planner_sort_time(content, mtime_timestamp) if planner
                else _sort_time(
                    journal_date, anchor, datetime.fromtimestamp(mtime_timestamp, timezone.utc), block_text,
                )
            ),
        })
    if kind == "journal" and blocks and journal_date:
        # A journal too old to carry a usable time anywhere is classified as one
        # block for the whole day (tagging.journal_blocks), so its footer anchor
        # is the day at midnight and matches no block spelling here. Without this
        # the day's hashtags would exist in the index but on no card at all.
        day_tags = machine_tags.get(f"timestamp:{journal_date}T00:00:00", [])
        spellings = {
            f"timestamp:{journal_date}T00:00:00",
            f"timestamp:{journal_date[5:]}T00:00:00",
            "timestamp:00:00:00",
        }
        if day_tags and not spellings & {block["stable_anchor"] for block in blocks}:
            blocks[0]["ai_tags"] = sorted(set(blocks[0]["ai_tags"]) | set(day_tags))
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
    if source == "personal" and kind == "chat":
        # Ein Chatverlauf ist ein Protokoll: er wird angehaengt, nie im Editor
        # ueberschrieben.
        document["read_only"] = True
    if source == "personal" and kind in {"project", "agent"}:
        session = ai_sessions_module.parse_session(content, relative)
        if session:
            document["read_only"] = True
            document["ai_session"] = session
    if source == "family":
        document["family_metadata"] = _parse_frontmatter(content)
    return prepare_search_text(document)


__all__ = ['_main_module', '_current_user', '_csrf_error', '_require_user', '_is_admin', '_atomic_write', '_read_text', '_read_json', '_write_json', '_user_root', '_archive_root', '_index_dir', '_metadata_path', '_normalise', 'prepare_search_text', 'search_haystack', 'without_search_text', '_fingerprint', '_source_signature', '_read_source_snapshot', '_relative_path', '_is_technical_relative', '_is_technical_file', '_iter_markdown_entries', '_iter_markdown', '_personal_kind', '_parse_frontmatter', '_template_assignments', '_template_membership', '_family_visibility', '_extract_tags', '_block_anchor', '_split_blocks', '_journal_date', '_local_timezone', '_sort_time', '_task_identity', '_block_identity', '_family_metadata_fields', '_family_line_fields', '_family_entry_label', '_family_line_display', '_family_display_text', '_is_family_planner_content', '_family_planner_display', '_family_planner_sort_time', '_build_document']
