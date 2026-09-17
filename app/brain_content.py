"""Brain search, catalogs, file creation and editor services.

AI maintenance contract:
Own query projection, knowledge/write-target resolution, metadata and guarded
content mutations. HTTP request handling belongs in brain_routes.py.
Public compatibility names are re-exported by brain.py. Always import the
facade from application code so test-replaceable DATA_DIR/FAMILY_DIR values are
synchronized before execution.
"""

import sys

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before domain modules are imported")
globals().update({name: getattr(_brain, name) for name in dir(_brain) if not name.startswith("__")})


def _sync_from_facade():
    for name in ("DATA_DIR", "FAMILY_DIR", "INDEX_INTERVAL_SECONDS", "enqueue_rebuild"):
        if hasattr(_brain, name):
            globals()[name] = getattr(_brain, name)


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


def external_read_root_options():
    """Expose configured read-only roots used by host-side Knowledge tools."""
    try:
        value = json.loads(read_text_file(DATA_DIR / "host_worker.json"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    roots = value.get("external_read_roots", []) if isinstance(value, dict) else []
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


def _personal_files(user_id, kind, query="", start=None, end=None, selected_tags=None, excluded_tags=None,
                    any_tags=None):
    root = _user_root(user_id)
    required, excluded, _ = _query_terms(query)
    tagged_paths = _document_tag_paths(user_id, "personal", selected_tags, excluded_tags, any_tags)
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
        if kind in {"project", "agent"}:
            session = ai_sessions_module.parse_session(content, relative)
            if session:
                file_data["ai_session"] = session
        files.append(file_data)
    return sorted(files, key=lambda item: (_normalise(item["title"]), item["path"]))


def _personal_projects(user_id, query="", start=None, end=None, selected_tags=None, excluded_tags=None,
                       any_tags=None):
    return _personal_files(user_id, "project", query, start, end, selected_tags, excluded_tags, any_tags)


def _personal_agent_sessions(user_id, query="", start=None, end=None, selected_tags=None, excluded_tags=None,
                             any_tags=None):
    return _personal_files(user_id, "agent", query, start, end, selected_tags, excluded_tags, any_tags)


def _family_files(user_id, kind, query="", start=None, end=None, selected_tags=None, excluded_tags=None,
                  any_tags=None):
    directory_name = {"note": "notes", "project": "projects"}[kind]
    root = FAMILY_DIR / directory_name
    required, excluded, _ = _query_terms(query)
    tagged_paths = _document_tag_paths(user_id, "family", selected_tags, excluded_tags, any_tags)
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
    _sync_from_facade()
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
    document = _decorate_documents(user_id, [document])[0]
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


__all__ = ['_resolve_document', '_safe_knowledge_path', 'resolve_knowledge_source', 'knowledge_source_options', '_safe_write_target', 'resolve_write_target', 'write_target_options', 'external_write_root_options', 'external_read_root_options', '_family_editor_content', '_family_content_with_editor_body', '_document_tags_with_status', '_file_tags_with_status', '_family_management_details', '_indexed_personal_projects', '_personal_files', '_personal_projects', '_family_files', '_create_personal_file', '_create_family_file', '_create_family_note', '_create_family_project', '_enqueue_file_rebuild', '_valid_project_reference', '_find_reference', '_toggle_markdown_task', '_family_structured_task', '_save_annotation', 'record_template_assignment']
