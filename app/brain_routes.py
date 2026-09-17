"""Brain HTTP API routes.

AI maintenance contract: this module performs request validation and response
serialization only. Search, indexing, visibility and persistent editor rules
belong in the focused brain_* domain modules and are accessed through brain.py.
"""

import sys

import background_runs

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before routes are imported")
globals().update({name: getattr(_brain, name) for name in dir(_brain) if not name.startswith("__")})
_HELPER_NAMES = tuple(name for name, value in vars(_brain).items() if callable(value))


@brain_bp.before_request
def _sync_brain_route_runtime():
    for name in _HELPER_NAMES:
        if hasattr(_brain, name):
            globals()[name] = getattr(_brain, name)
    globals()["DATA_DIR"] = _brain.DATA_DIR
    globals()["FAMILY_DIR"] = _brain.FAMILY_DIR


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
    if payload_error:
        return payload_error
    # ``facets=block`` answers the hits and the hashtags beside them from one
    # snapshot. They describe the same result list, and the client used to ask
    # for them separately: two requests per keystroke, each walking the whole
    # index, competing for the GIL of a single-process server. The parameter is
    # opt-in so the file-list views keep their document-wide counts.
    if request.args.get("facets") == "block":
        payload["tags"] = _visible_tag_items(
            documents, tagging_module.catalog_view(user["id"]),
            _selected_tags(), _excluded_tags(), True,
            _query_terms((request.args.get("q") or "").strip()), _any_tags(),
        )
    return jsonify(payload)


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
    excluded_tags = _excluded_tags()
    any_tags = _any_tags()
    start, end, date_error = _date_range()
    if date_error:
        return jsonify({"error": date_error}), 400
    catalog = tagging_module.catalog_view(user["id"]) if (selected_tags or excluded_tags or any_tags) else None
    required, excluded, ai = _query_terms((request.args.get("q") or "").strip())
    documents, fallback = _visible_documents(user["id"])
    tasks = []
    for document in documents:
        if not _in_date_range(document.get("date"), start, end):
            continue
        for block in document.get("blocks", []):
            for task in block.get("tasks", []):
                if required or excluded:
                    task_haystack = search_haystack(task, task.get("manual_tags"))
                    if task_haystack is None:
                        task_haystack = _normalise("\n".join(
                            [document["path"], task.get("text", "")]
                            + [f"#{tag}" for tag in task.get("tags", [])]
                        ))
                    if not all(term in task_haystack for term in required) or any(
                        term in task_haystack for term in excluded
                    ):
                        continue
                if not _matches_ai_terms(block.get("ai_tags", []), ai):
                    continue
                if status == "open" and task["completed"]:
                    continue
                if status == "done" and not task["completed"]:
                    continue
                if priority != "all" and task.get("priority", "normal") != priority:
                    continue
                if catalog is not None:
                    approved = _filterable_document_tags(document, task.get("tags", []), catalog)
                    if not _tags_pass(approved, selected_tags, excluded_tags, any_tags):
                        continue
                tasks.append({
                    "fingerprint": task["fingerprint"],
                    "metadata_ref": task.get("metadata_ref"),
                    "doc_id": document["doc_id"],
                    "source": document["source"],
                    "source_label": {"personal": "Privat", "family": "Familie", "archive": "Archiv"}[document["source"]],
                    "path": document["path"],
                    "kind": document["kind"],
                    # Familienaufgaben stehen als Maschinenzeile in der Datei;
                    # auf der Karte hat nur die Formulierung etwas zu suchen.
                    "text": task.get("display_text") or task["text"],
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
    selected_tags = _selected_tags()
    excluded_tags = _excluded_tags()
    any_tags = _any_tags()
    # ``facet=block`` is what the search view asks for: its results are blocks,
    # so its tag counts have to be block counts. The file views stay document
    # wide because that is what their own filter narrows.
    block_scope = request.args.get("facet") == "block"
    # In that mode the free-text query narrows the facets as well, so typing
    # ``ki:`` leaves exactly the hashtags a model assigned in the remaining
    # blocks - the list one drills through stays the list one is looking at.
    query_terms = _query_terms((request.args.get("q") or "").strip()) if block_scope else None
    if not block_scope:
        documents = _documents_with_selected_tags(documents, catalog, selected_tags, excluded_tags, any_tags)
    return jsonify({
        "tags": _visible_tag_items(
            documents, catalog, selected_tags, excluded_tags, block_scope, query_terms, any_tags,
        ),
        "index_pending": pending,
    })


@brain_bp.route("/hashtag-replacement", methods=["GET", "POST"])
def brain_hashtag_replacement():
    """Preview or explicitly confirm an exact replacement in personal journals."""
    user, error = _require_user()
    if error:
        return error
    if request.method == "GET":
        try:
            return jsonify(hashtag_replacement_preview(user["id"], request.args.get("old_tag")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    if data.get("confirmed") is not True:
        return jsonify({"error": "Die Sammel-Ersetzung muss ausdrücklich bestätigt werden"}), 400
    try:
        result = apply_hashtag_replacement(
            user["id"], data.get("old_tag"), data.get("new_tag"), data.get("documents"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if result.get("conflict"):
        result["error"] = "Mindestens ein Journal wurde zwischenzeitlich geändert"
        return jsonify(result), 409
    result["ok"] = True
    return jsonify(result)


@brain_bp.route("/tagging/start", methods=["POST"])
def brain_tagging_start():
    """Start one server-side pass over a date range and return its run.

    The loop used to live in the browser: one request per journal, driven by
    JavaScript. A phone that locks itself ended the pass mid-range. The pass now
    runs in the server and this endpoint only hands back the run to watch, so
    the browser may leave and come back.
    """
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
    mode = data.get("mode", "missing")
    if mode not in {"missing", "refresh", "stale"}:
        return jsonify({"error": "mode must be missing, refresh or stale"}), 400
    stale_before = data.get("stale_before", "")
    if mode == "stale" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stale_before or ""):
        return jsonify({"error": "stale mode needs an ISO date in stale_before"}), 400
    main = _main_module()
    config = main.load_config() if main else {}
    if historical_tagging.resolve_provider(config, data.get("provider_id")) is None:
        return jsonify({"error": "Select a configured local LM Studio provider"}), 400
    preview = bool(data.get("preview"))
    try:
        record = background_runs.start(
            user["id"], historical_tagging.RUN_KIND,
            {
                "start_date": start, "end_date": end, "mode": mode,
                "stale_before": stale_before, "provider_id": data.get("provider_id"),
                "preview": preview,
            },
            label="{} {} bis {}".format(
                "Vorschau Verschlagwortung" if preview else "Verschlagwortung", start, end,
            ),
        )
    except background_runs.RunConflict as exc:
        running = background_runs.active(user["id"], historical_tagging.RUN_KIND)
        return jsonify({
            "error": str(exc),
            "run": background_runs.public_run(running) if running else None,
        }), 409
    except background_runs.RunError as exc:
        return jsonify({"error": str(exc)}), 400
    logger.info("Historical tagging run %s queued for %s (%s..%s)", record["id"], user["id"], start, end)
    return jsonify({"run": background_runs.public_run(record)}), 202


@brain_bp.route("/diagnostics", methods=["GET"])
def brain_diagnostics_report():
    """Zaehler, die stille Luecken sichtbar machen.

    Bewusst als eigener Abruf und nicht als Beiwerk eines Tagging-Laufs: die
    Frage "wie viel meines Journals ist ueberhaupt adressierbar und
    verschlagwortet" muss auch dann zu beantworten sein, wenn gerade kein Lauf
    stattfindet - sonst faellt eine Luecke erst auf, wenn jemand zufaellig
    danach sucht.
    """
    user, error = _require_user()
    if error:
        return error
    start, end = request.args.get("start", ""), request.args.get("end", "")
    for value in (start, end):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return jsonify({"error": "Datumsangaben brauchen das Format JJJJ-MM-TT"}), 400
    if start and end and start > end:
        return jsonify({"error": "Das Startdatum liegt nach dem Enddatum"}), 400
    return jsonify(journal_health(user["id"], start, end))


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
            "external_read_roots": external_read_root_options(),
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
                if source.get("root_id"):
                    root = next((item for item in external_read_root_options() if item["id"] == source.get("root_id")), None)
                    path = str(source.get("path") or "")
                    if not root or path != root["path"] and not path.startswith(root["path"].rstrip("/") + "/"):
                        raise ValueError("Linux-Pfad liegt nicht unter einer freigegebenen Lese-Wurzel")
                else:
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
    # Ignoring or re-offering a proposal changes no journal footer and no
    # reference, so it must not trigger a rebuild.  A personal catalog change
    # only affects this user's documents; only the shared Family catalog can
    # change what every other user sees.
    if data.get("action") not in {"block", "unblock"}:
        enqueue_rebuild("__all__" if data.get("scope") == "family" else user["id"])
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
        "block_end_line": block["end_line"] if block else None,
        "block_content": block["text"] if block else None,
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

    write_session_id = data.get("write_session_id")
    media_stage = None
    if write_session_id:
        # Attachments live below the uploading user's root, so a shared Family
        # file would carry markers nobody else could open.
        if document["source"] != "personal":
            return jsonify({"error": "Anhänge sind nur in privaten Quellen möglich."}), 400
        try:
            media_stage = write_sessions_module.stage_submission_media(
                user["id"], write_session_id, path,
                day_dir=write_sessions_module.attachment_day_dir(user["id"], path),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc), "write_session_conflict": True}), 409
    # An unresolved placeholder must never reach the file, so it is stripped
    # even when this edit carries no attachments at all.
    content = write_sessions_module.apply_media_placeholder(
        content, write_sessions_module.media_markdown(media_stage["items"]) if media_stage else "",
    )

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

        try:
            result = update_text_file(path, update)
        except Exception:
            if media_stage:
                write_sessions_module.rollback_staged_media(media_stage)
            raise
        if result.get("forbidden") or result.get("conflict"):
            if media_stage:
                write_sessions_module.rollback_staged_media(media_stage)
            if result.get("forbidden"):
                return jsonify({"error": "Not found"}), 404
            return jsonify({"error": "Source changed in the background", "conflict": True}), 409
        if media_stage:
            write_sessions_module.commit_submission_media(user["id"], media_stage)
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
    excluded_tags = _excluded_tags()
    any_tags = _any_tags()
    return jsonify({
        "notes": _family_files(user["id"], "note", query, start, end, selected_tags, excluded_tags, any_tags),
        "projects": _family_files(user["id"], "project", query, start, end, selected_tags, excluded_tags, any_tags),
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
    return jsonify({"projects": _personal_projects(user["id"], query, start, end, _selected_tags(), _excluded_tags(), _any_tags())})


@brain_bp.route("/ai-agent", methods=["GET"])
def brain_agent_sessions_get():
    user, error = _require_user()
    if error:
        return error
    query = (request.args.get("q") or "").strip()
    start, end, date_error = _date_range()
    if date_error:
        return jsonify({"error": date_error}), 400
    return jsonify({"sessions": _personal_agent_sessions(user["id"], query, start, end, _selected_tags(), _excluded_tags(), _any_tags())})


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
    return jsonify({"notes": _personal_files(user["id"], "note", query, start, end, _selected_tags(), _excluded_tags(), _any_tags())})


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


__all__ = ['brain_bootstrap', 'brain_search', 'brain_tasks', 'brain_tags', 'brain_hashtag_replacement', 'brain_tagging_start', 'brain_diagnostics_report', 'brain_tag_catalog', 'brain_document_get', 'brain_document_put', 'brain_document_agent_session_action', 'brain_document_access_put', 'brain_task_toggle', 'brain_metadata', 'brain_family_get', '_family_file_title', 'brain_family_notes_post', 'brain_family_projects_post', 'brain_projects_get', 'brain_agent_sessions_get', 'brain_projects_post', 'brain_notes_get', 'brain_notes_post', 'brain_index_rebuild']
