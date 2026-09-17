"""Brain index, rebuild worker and visibility resolution.

AI maintenance contract:
Own index persistence, rebuild scheduling and current visible-document selection.
Never weaken fail-closed family or archive visibility checks.
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
    for name in ("DATA_DIR", "FAMILY_DIR", "INDEX_INTERVAL_SECONDS"):
        if hasattr(_brain, name):
            globals()[name] = getattr(_brain, name)


def _scan_personal_documents(user_id):
    root = _user_root(user_id)
    documents = []
    for path, relative, _ in _iter_markdown_entries(root, _personal_kind):
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
        indexed_document = without_search_text(document)
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
    indexed_document = without_search_text(document)
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
            # No copy: the snapshot is immutable by contract and
            # ``_decorate_documents`` hands out copies of what it changes.
            return cached_documents
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
            documents.append(prepare_search_text(document))
        if len(documents) != expected_count or len({document["doc_id"] for document in documents}) != len(documents):
            return None
        with _index_snapshot_lock:
            _index_snapshots[snapshot_key] = (snapshot_signature, documents)
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


def _annotation_lookup(metadata):
    """Group an account's annotations by the anchor they were filed under.

    ``_annotation_candidates`` scanned every annotation of the account anew for
    every block that carried none - and most blocks carry none. Grouping them
    once per request turns that scan into a lookup.
    """
    lookup = {}
    for reference, annotation in (metadata.get("annotations") or {}).items():
        key = (annotation.get("kind"), annotation.get("doc_id"), annotation.get("stable_anchor"))
        lookup.setdefault(key, []).append((reference, annotation))
    return lookup


def _annotation_candidates(metadata, document, block, kind, task=None,
                           lookup=None, anchor_counts=None):
    """Annotations that can only mean this block, matched through its anchor.

    ``lookup`` and ``anchor_counts`` are the precomputed forms of the two scans
    this used to run per block: over every annotation of the account, and over
    every block of the document. Both are optional so single-block callers keep
    working unchanged.
    """
    anchor = block.get("stable_anchor")
    if anchor_counts is None:
        occurrences = sum(
            candidate.get("stable_anchor") == anchor
            for candidate in document.get("blocks", [])
        )
    else:
        occurrences = anchor_counts.get(anchor, 0)
    if occurrences != 1 or not block.get("has_stable_anchor"):
        return []
    if lookup is None:
        lookup = _annotation_lookup(metadata)
    candidates = []
    for reference, annotation in lookup.get((kind, document["doc_id"], anchor), ()):
        if kind == "task" and (
            annotation.get("task_text") != task.get("identity", _task_identity(task.get("text", "")))
            or annotation.get("task_occurrence") != task.get("occurrence")
        ):
            continue
        candidates.append((reference, annotation))
    return candidates


def _decorate_documents(user_id, documents):
    """Return copies of the documents carrying the signed-in user's annotations.

    The index snapshot is shared across the process: one family or archive
    document object serves every account, while the annotations come from each
    user's own ``brain_metadata.json``. Decorating in place would therefore
    show one user's priority or project on another user's card - which is why
    ``_read_index_documents`` used to hand out a ``copy.deepcopy`` of the whole
    index on every single request, measured at 40 ms per source and three
    sources per request.

    Copying only what is decorated costs one flat dict per block and task
    instead, and leaves the snapshot itself untouched. That is the contract now:
    the snapshot is immutable, whoever decorates copies.
    """
    metadata = _read_metadata(user_id)
    annotations = metadata["annotations"]
    # Most accounts annotate a handful of blocks, not thousands. Grouping the
    # annotations once - and skipping the anchor search entirely when there are
    # none - keeps the decoration linear in the number of blocks.
    lookup = _annotation_lookup(metadata) if annotations else {}
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

    def resolve(item, document, block, kind, anchor_counts, task=None):
        """Find the annotation for one block or task, and the key it is filed under."""
        reference = item["fingerprint"]
        annotation = annotations.get(reference)
        metadata_reference = reference
        legacy = item.get("legacy_fingerprint")
        if not annotation and legacy and legacy_counts.get(legacy) == 1:
            metadata_reference = legacy
            annotation = annotations.get(metadata_reference)
        if not annotation and lookup:
            candidates = _annotation_candidates(
                metadata, document, block, kind, task, lookup, anchor_counts,
            )
            if len(candidates) == 1:
                metadata_reference, annotation = candidates[0]
        return metadata_reference, annotation or {}

    decorated = []
    for document in documents:
        blocks = []
        anchor_counts = {}
        if lookup:
            for candidate in document.get("blocks", []):
                anchor = candidate.get("stable_anchor")
                anchor_counts[anchor] = anchor_counts.get(anchor, 0) + 1
        for block in document.get("blocks", []):
            reference, annotation = resolve(block, document, block, "block", anchor_counts)
            manual_tags = sorted(set(annotation.get("tags", [])))
            # ``tags_base`` is the sorted union of the file's own and the
            # model's hashtags, already computed while preparing the search
            # text. Only an annotation makes it differ.
            file_tags = block.get("tags_base")
            if file_tags is None:
                file_tags = sorted(set(block.get("tags_auto", [])) | set(block.get("ai_tags", [])))
            updated = {
                **block,
                "metadata_ref": reference if annotation else None,
                "manual_tags": manual_tags,
                # Machine-assigned hashtags count as tags of the block: they are
                # what a filter or a tag count is expected to find, even though
                # they live in the footer rather than in the text.
                "tags": sorted(set(file_tags) | set(manual_tags)) if manual_tags else file_tags,
                "priority": annotation.get("priority", "normal"),
                "project": annotation.get("project") or "",
            }
            if "tasks" in block:
                tasks = []
                for task in block["tasks"]:
                    task_reference, task_annotation = resolve(
                        task, document, block, "task", anchor_counts, task,
                    )
                    task_manual = sorted(set(task_annotation.get("tags", [])))
                    tasks.append({
                        **task,
                        "metadata_ref": task_reference if task_annotation else None,
                        "manual_tags": task_manual,
                        "tags": sorted(set(file_tags) | set(task_manual)) if task_manual else file_tags,
                        "priority": task_annotation.get("priority", "normal"),
                        "project": task_annotation.get("project") or "",
                        "created_at": document["date"],
                    })
                updated["tasks"] = tasks
            blocks.append(updated)
        decorated.append({**document, "blocks": blocks})
    return decorated


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
            if not linked and annotation.get("stable_anchor", "").startswith(("timestamp:", "family-project:")):
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
    # Resolve through the facade so tests and alternate process owners can
    # deliberately replace the worker-start decision without stale bindings.
    if not _brain.start_index_worker():
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
    # Pfad, relativer Pfad und stat kommen aus einem Durchlauf: sie hier noch
    # einmal auszurechnen war der teuerste Posten einer Brain-Anfrage.
    for path, relative, stat in _iter_markdown_entries(root, _personal_kind):
        indexed = indexed_by_path.get(relative)
        unchanged = indexed and indexed.get("source_signature") == _source_signature(stat)
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
    for path, relative, stat in _iter_markdown_entries(FAMILY_DIR):
        current_paths.add(relative)
        indexed = indexed_by_path.get(relative)
        unchanged = indexed and indexed.get("source_signature") == _source_signature(stat)
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
    for path, relative, stat in _iter_markdown_entries(root):
        current_paths.add(relative)
        indexed = indexed_by_path.get(relative)
        unchanged = indexed and indexed.get("source_signature") == _source_signature(stat)
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
        # Queue through the public facade. Test fixtures and non-owner workers
        # intentionally replace this boundary to avoid spawning a stale worker.
        _brain.enqueue_rebuild(user_id)
    return _decorate_documents(user_id, [*personal, *family, *archive]), index_pending


__all__ = ['_scan_personal_documents', '_scan_family_documents', '_scan_archive_documents', '_index_file', '_index_guard', '_write_index_documents', '_write_index_document', '_delete_index_document', '_read_index_documents', '_metadata_default', '_metadata_is_valid', '_read_metadata', '_update_metadata', '_annotation_lookup', '_annotation_candidates', '_decorate_documents', '_mark_orphaned_metadata', 'rebuild_user_index', 'rebuild_family_index', 'rebuild_archive_index', '_rebuild_request_dir', '_persist_rebuild_request', '_pop_rebuild_request', '_rebuild_for_user', '_rebuild_all', '_index_worker_loop', 'start_index_worker', 'enqueue_rebuild', '_current_family_document', '_family_document_is_visible', '_current_personal_documents', '_current_family_documents', '_current_archive_documents', '_visible_documents']
