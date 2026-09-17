"""Writing AI HTTP routes.

AI maintenance contract:
Own only /api/write-ai and /api/ai-submit handlers. Reuse helpers exposed
by main.py; do not duplicate draft or job invariants.
The module is imported only after main.py has created the Flask app and core
helpers. Runtime values that tests or DEV configuration may replace are synced
from that facade before each request.
"""

import sys

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before route modules are imported")
globals().update({name: getattr(_core, name) for name in dir(_core) if not name.startswith("__")})
_DYNAMIC_NAMES = ('DATA_DIR', 'IS_DEV', 'load_config', '_call_ai_api', '_append_journal_entry', 'get_tz_aware_now')


def _sync_runtime():
    for name in _DYNAMIC_NAMES:
        if hasattr(_core, name):
            globals()[name] = getattr(_core, name)


@app.before_request
def _sync_writing_ai_routes_runtime():
    _sync_runtime()


@app.route("/api/write-ai/draft", methods=["GET", "POST"])
@require_auth
def api_write_ai_draft():
    """Load/save the current user's temporary writing-tab document only."""
    user = _current_user()
    path = _write_draft_path(user["id"])
    if request.method == "GET":
        content = read_text_file(path) if path.exists() else ""
        cleaned = _strip_write_ai_session_metadata(content)
        if cleaned != content:
            # Repair drafts produced by the old runner without exposing the
            # hidden marker again after a refresh or device switch.
            update_text_file(path, lambda _current: _strip_write_ai_session_metadata(_current))
            content = cleaned
        response = jsonify({"content": content, "revision": _draft_revision(content)})
        response.headers["Cache-Control"] = "no-store"
        return response
    csrf_error = csrf_protect(lambda: None)()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if not isinstance(content, str) or len(content) > 200000:
        return jsonify({"error": "Draft content is invalid or too large"}), 400
    content = _strip_write_ai_session_metadata(content)
    expected = data.get("revision")
    result = {}
    def save(current):
        if expected and expected != _draft_revision(current):
            result["conflict"] = current
            return current
        result["content"] = content
        return content
    update_text_file(path, save)
    if "conflict" in result:
        return jsonify({"error": "Der Schreibstand wurde auf einem anderen Gerät geändert.", "content": result["conflict"], "revision": _draft_revision(result["conflict"])}), 409
    return jsonify({"ok": True, "content": content, "revision": _draft_revision(content)})


@app.route("/api/write-ai/submit", methods=["POST"])
@require_auth
@csrf_protect
def api_write_ai_submit():
    """Run an explicit writing-tab hashtag request against the configured provider.

    The handler neither appends to a journal nor uses project/document context.
    Any configured AI hashtag may be invoked here; it accepts exactly today's
    private journal or the private temporary draft.
    """
    data = request.get_json(silent=True) or {}
    user = _current_user()
    uid = user["id"]
    tag = tagging_module.normalise_tag(data.get("workflow_tag", ""))
    workflow = tagging_module.catalog_view(uid).get("ai", {}).get(tag)
    if not workflow:
        return jsonify({"error": "Unbekanntes AI-Hashtag"}), 400
    if workflow.get("target") == "daily_journal":
        return jsonify({"error": "Dieser AI-Workflow ist ausschließlich für die Share-API bestimmt."}), 400
    context_type = data.get("context_type")
    if context_type not in {"draft", "today_journal"}:
        return jsonify({"error": "Bitte Kontext Aktuelles Textfeld oder Heutiges Journal wählen"}), 400
    submitted = data.get("text")
    if not isinstance(submitted, str) or not submitted.strip() or len(submitted) > 200000:
        return jsonify({"error": "Bitte einen gültigen Text eingeben"}), 400
    submitted = _strip_write_ai_session_metadata(submitted)
    write_session_id = data.get("write_session_id")
    if write_session_id is not None and not isinstance(write_session_id, str):
        return jsonify({"error": "Ungültige Schreibsession für KI-Anhänge"}), 400
    try:
        knowledge_snapshots, knowledge_references = _knowledge_snapshots_for_write(uid, submitted)
        write_target = _write_target_snapshot_for_write(uid, submitted)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    user_request = _write_ai_user_request(submitted, tag, knowledge_snapshots, knowledge_references)
    if write_target:
        user_request = re.sub(r"(?<![\w#])#" + re.escape(write_target["tag"]) + r"\b", "", user_request, flags=re.UNICODE)
        user_request = "\n".join(line.strip() for line in user_request.splitlines()).strip()
    if not user_request:
        return jsonify({"error": "Bitte formuliere einen Auftrag zusätzlich zu den Routing-Hashtags."}), 400
    provider_id = str(data.get("provider_id") or workflow.get("provider_id") or "").strip()
    is_host_pi = workflow.get("agent") == "pi" or provider_id == "__host_worker__"
    if knowledge_references and not is_host_pi:
        return jsonify({"error": "Externe Knowledge-Ordner stehen ausschließlich lokalen Pi-Workflows als sichere Suchreferenz zur Verfügung."}), 400
    if write_target and not is_host_pi:
        return jsonify({"error": "Schreibziel-Vorschläge werden ausschließlich über den lokalen Pi-Host-Worker erstellt."}), 400
    provider = None
    if is_host_pi:
        if workflow.get("agent") != "pi":
            return jsonify({"error": "Der Host-Worker unterstützt derzeit nur Pi-Workflows."}), 400
        if agent_jobs_enabled() and (knowledge_references or write_target):
            return jsonify({"error": "DB-Agentenjobs unterstützen noch keine Host-Referenzen oder Schreibziele."}), 409
        model = str(workflow.get("model") or "").strip()
        if not model:
            return jsonify({"error": "Für den Pi-Workflow fehlt das Modell."}), 400
    else:
        config = load_config()
        provider = next((item for item in config.get("ai_providers", []) if item.get("id") == provider_id), None)
        if not provider:
            return jsonify({"error": "Unbekannter AI-Anbieter"}), 400
        model = str(data.get("model") or provider.get("model") or "").strip()
        # Models are selected from the existing provider configuration, not supplied
        # freely by the browser.
        if not model or model != str(provider.get("model") or ""):
            return jsonify({"error": "Unbekanntes Modell für diesen Anbieter"}), 400

    draft_path = _write_draft_path(uid)
    expected_revision = data.get("revision")
    state = {}
    def begin(current):
        if expected_revision and expected_revision != _draft_revision(current):
            state["conflict"] = current
            return current
        state["base"] = submitted
        return submitted
    update_text_file(draft_path, begin)
    if "conflict" in state:
        current = state["conflict"]
        return jsonify({"error": "Der Schreibstand wurde auf einem anderen Gerät geändert.", "content": current, "revision": _draft_revision(current)}), 409

    try:
        attachment_bundle = ai_attachments_module.prepare_session_attachments(
            DATA_DIR, uid, write_session_id, submitted,
            whisper_model_name=data.get("whisper_model"),
        )
    except ai_attachments_module.AiAttachmentError as exc:
        return jsonify({"error": str(exc), "attachment_error": True}), 409

    if context_type == "today_journal":
        now, _ = get_tz_aware_now()
        journal_path = _get_journal_path(now, user_id=uid)
        context = read_text_file(journal_path) if journal_path.exists() else ""
    else:
        context = submitted
    prompt = _render_write_ai_request(
        workflow.get("prompt", ""), user_request, context_type, context,
        knowledge_snapshots, attachment_bundle["context"],
    )
    if is_host_pi:
        if agent_jobs_enabled():
            snapshot_parts = []
            if context_type == "today_journal":
                snapshot_parts.append("Heutiges Journal:\n" + context)
            for item in knowledge_snapshots:
                snapshot_parts.append(f"Knowledge #{item['tag']} (Referenzmaterial):\n{item['content']}")
            try:
                session_id = background_jobs.create_agent_session(
                    uid, tag, "pi", str(workflow.get("prompt") or ""), model_name=model,
                )
                queued = background_jobs.queue_agent_request(
                    session_id, uid, user_request, str(uuid.uuid4()), submitted_text=submitted,
                    context_snapshot="\n\n".join([*snapshot_parts, attachment_bundle["context"]]).strip(),
                    attachment_images=attachment_bundle["images"],
                )
            except (RuntimeError, ValueError) as exc:
                if attachment_bundle["snapshot_id"]:
                    ai_attachments_module.cleanup_snapshot(DATA_DIR, uid, attachment_bundle["snapshot_id"])
                return jsonify({"error": str(exc)}), 503
            return jsonify({"ok": True, "queued": True, "db_native": True, "job_id": queued["job_id"],
                            "session_id": session_id, "status": "queued", "revision": _draft_revision(submitted)}), 202
        try:
            job = _queue_pi_write_job(
                uid, user["username"], tag, workflow, context_type, submitted, user_request,
                context, knowledge_snapshots, knowledge_references, write_target,
                attachment_bundle["images"], attachment_bundle["context"],
            )
        except ValueError as exc:
            if attachment_bundle["snapshot_id"]:
                ai_attachments_module.cleanup_snapshot(DATA_DIR, uid, attachment_bundle["snapshot_id"])
            return jsonify({"error": str(exc)}), 400
        event = {"id": job["id"], "at": job["created_at"], "workflow_tag": tag,
                 "agent": "pi", "model": job["model"], "context_type": context_type,
                 "status": "queued"}
        _append_write_ai_history(uid, event)
        return jsonify({"ok": True, "queued": True, "job_id": job["id"], "session_id": job["session_id"],
                        "session_path": job["source_path"], "status": "queued",
                        "revision": _draft_revision(submitted)}), 202
    event = {"id": str(uuid.uuid4()), "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "workflow_tag": tag, "provider_id": provider_id, "model": model, "context_type": context_type, "status": "running"}
    _append_write_ai_history(uid, event)
    try:
        call_args = (provider, {"system_prompt": workflow.get("prompt", "")}, prompt)
        response = (
            _call_ai_api(*call_args, image_attachments=attachment_bundle["images"])
            if attachment_bundle["images"] else _call_ai_api(*call_args)
        )
    except (ConnectionError, ValueError) as exc:
        event.update({"status": "error", "error": str(exc)[:500]})
        _append_write_ai_history(uid, event)
        return jsonify({"error": str(exc)}), 502
    finally:
        if attachment_bundle["snapshot_id"]:
            ai_attachments_module.cleanup_snapshot(DATA_DIR, uid, attachment_bundle["snapshot_id"])

    result = {}
    def apply_response(current):
        final = _merge_write_ai_response(submitted, current, response)
        if final is None:
            result["conflict"] = current
            return current
        result["content"] = final
        return final
    update_text_file(draft_path, apply_response)
    if "conflict" in result:
        current = result["conflict"]
        event.update({"status": "conflict", "error": "draft changed independently"})
        _append_write_ai_history(uid, event)
        return jsonify({"error": "Der KI-Schreibstand wurde während der Anfrage unabhängig geändert oder bereits übernommen.",
                        "content": current, "revision": _draft_revision(current)}), 409
    event.update({"status": "completed"})
    _append_write_ai_history(uid, event)
    return jsonify({"ok": True, "response": response, "content": result["content"], "revision": _draft_revision(result["content"]), "provider_id": provider_id, "model": model, "context_type": context_type})


@app.route("/api/write-ai/jobs/<job_id>", methods=["GET"])
@require_auth
def api_write_ai_job(job_id):
    job = _pi_job_response(_current_user()["id"], job_id)
    if not job and agent_jobs_enabled():
        try:
            db_job = background_jobs.agent_job_for_owner(job_id, _current_user()["id"])
        except (RuntimeError, ValueError):
            db_job = None
        if db_job:
            public_status = db_job["status"]
            if db_job.get("response_status") == "proposed":
                public_status = "completed"
            elif public_status in {"failed", "timed_out"}:
                public_status = "error"
            job = {
                "id": str(db_job["id"]), "session_id": str(db_job["session_id"]),
                "status": public_status,
                "created_at": db_job["created_at"], "completed_at": db_job["completed_at"],
                "error": background_jobs.agent_error_message(db_job["error_code"]),
                "response": db_job["response_text"],
                "submitted_text": db_job["submitted_text"], "draft_commit_revision": db_job["draft_commit_revision"], "db_native": True,
            }
    if not job:
        return jsonify({"error": "KI-Job nicht gefunden"}), 404
    response = {key: job.get(key) for key in (
        "id", "session_id", "source_path", "status", "created_at", "started_at", "completed_at", "cancelled_at",
        "cancel_requested_at", "error", "response", "apply_summary",
    )}
    proposal = job.get("proposal") if job.get("status") == "proposed" else None
    if isinstance(proposal, dict):
        response["proposal"] = {"summary": proposal.get("summary", ""), "paths": [item.get("path") for item in proposal.get("edits", []) if isinstance(item, dict)]}
    response["can_apply"] = job.get("status") == "proposed"
    response["can_undo"] = job.get("status") == "cancelled" and not job.get("started_at")
    return jsonify(response)


@app.route("/api/write-ai/jobs/<job_id>/commit-draft", methods=["POST"])
@require_auth
@csrf_protect
def api_commit_write_ai_job_draft(job_id):
    """Commit one completed host result to the shared draft exactly once.

    A consumed or independently replaced draft makes the result obsolete.  It
    must never repopulate the normal writing field after a template switch.
    """
    user_id = _current_user()["id"]
    job = _pi_job_response(user_id, job_id)
    if not job and agent_jobs_enabled():
        try:
            db_job = background_jobs.agent_job_for_owner(job_id, user_id)
        except (RuntimeError, ValueError):
            db_job = None
        if db_job and db_job.get("response_status") == "proposed":
            job = {"id": str(db_job["id"]), "status": "completed", "response": db_job["response_text"],
                   "submitted_text": db_job["submitted_text"], "draft_commit_revision": db_job["draft_commit_revision"], "db_native": True}
    if not job:
        return jsonify({"error": "KI-Job nicht gefunden"}), 404
    status = job.get("status")
    if status == "completed":
        response_text = str(job.get("response") or "").strip()
    elif status == "applied":
        response_text = str(job.get("apply_summary") or "Schreibziel angewendet.").strip()
    else:
        return jsonify({"error": "KI-Ergebnis ist noch nicht übernahmebereit"}), 409
    if not response_text:
        return jsonify({"error": "KI-Ergebnis ist leer"}), 409

    submitted = str(job.get("submitted_text") or "")
    draft_path = _write_draft_path(user_id)
    result = {}

    def commit(current):
        committed_revision = job.get("draft_commit_revision")
        if committed_revision and _draft_revision(current) == committed_revision:
            result.update(content=current, revision=committed_revision, already=True)
            return current
        final = _merge_write_ai_response(submitted, current, response_text)
        if final is None:
            result.update(conflict=True, revision=_draft_revision(current))
            return current
        result.update(content=final, revision=_draft_revision(final), already=False)
        return final

    update_text_file(draft_path, commit)
    if result.get("conflict"):
        return jsonify({"error": "Der KI-Schreibstand wurde bereits übernommen oder unabhängig geändert.",
                        "revision": result["revision"], "obsolete": True}), 409

    def remember_committed(committed_job, _result):
        committed_job["draft_commit_revision"] = result["revision"]
        committed_job["draft_committed_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    if not job.get("db_native"):
        _update_write_ai_job(user_id, job_id, remember_committed)
    else:
        background_jobs.remember_agent_draft_commit(job_id, user_id, result["revision"])
    return jsonify({"ok": True, "content": result["content"], "revision": result["revision"],
                    "already": result["already"]})


@app.route("/api/write-ai/jobs/<job_id>/apply", methods=["POST"])
@require_auth
@csrf_protect
def api_apply_write_ai_proposal(job_id):
    """Apply an already validated proposal only after a second explicit action."""
    user_id = _current_user()["id"]
    applied = []
    def apply(job, result):
        if job.get("status") != "proposed" or not isinstance(job.get("proposal"), dict):
            raise ValueError("Für diesen KI-Job liegt kein anwendbarer Vorschlag vor")
        target = job.get("write_target")
        if not isinstance(target, dict):
            raise ValueError("Schreibziel des KI-Jobs fehlt")
        current = tagging_module.catalog_view(user_id).get("write_targets", {}).get(target.get("scope"), {}).get(target.get("tag"))
        if not isinstance(current, dict) or current.get("path") != target.get("path") or current.get("file_policy") != target.get("file_policy"):
            raise ValueError("Schreibziel wurde geändert oder entzogen; Vorschlag wird nicht angewendet")
        if target.get("scope") == "host":
            if current.get("root_id") != target.get("root_id"):
                raise ValueError("Externe Schreibziel-Wurzel wurde geändert oder entzogen")
            job.update(status="apply_requested", apply_requested_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
            result["summary"] = "Anwendung des externen Schreibziels wurde an den Host-Worker übergeben."
            result["queued"] = True
            return
        root = brain_module.resolve_write_target(user_id, target["scope"], target["path"])["root"]
        edits = job["proposal"].get("edits", [])
        if not edits:
            job.update(status="applied", applied_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), apply_summary="Keine Dateiänderungen vorgeschlagen.")
            result["summary"] = job["apply_summary"]
            return
        prepared = []
        for edit in edits:
            if not isinstance(edit, dict) or not isinstance(edit.get("path"), str) or not isinstance(edit.get("content"), str):
                raise ValueError("Vorschlag ist beschädigt")
            if "\0" in edit["content"]:
                raise ValueError("Vorschlag enthält keinen gültigen Textinhalt")
            relative = PurePosixPath(edit["path"])
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != edit["path"]:
                raise ValueError("Vorschlag enthält einen unsicheren Dateipfad")
            path = root / relative
            if path.is_symlink() or not path.is_file() or path.resolve().parent != path.parent.resolve():
                raise ValueError("Eine Vorschlagsdatei ist nicht mehr sicher verfügbar")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != edit.get("expected_sha256"):
                raise ValueError(f"Datei wurde seit dem Vorschlag geändert: {edit['path']}")
            if target.get("file_policy") == "markdown_only" and path.suffix.casefold() != ".md":
                raise ValueError("Vorschlag verletzt die Markdown-Beschränkung")
            prepared.append((path, raw, edit["content"]))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for path, raw, content in prepared:
            backup = path.with_name(f"{path.name}.write-ai-backup-{stamp}")
            write_text_file(backup, raw.decode("utf-8"))
            write_text_file(path, content)
            applied.append(path.relative_to(root).as_posix())
        summary = f"Schreibziel angewendet: {', '.join(applied)}. Backups wurden neben den Dateien angelegt."
        job.update(status="applied", applied_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), apply_summary=summary)
        result["summary"] = summary
    result = _update_write_ai_job(user_id, job_id, apply)
    if not result or result.get("missing"):
        return jsonify({"error": "KI-Job nicht gefunden"}), 404
    if result.get("error"):
        return jsonify({"error": result["error"]}), 409
    return jsonify({"ok": True, "status": "apply_requested" if result.get("queued") else "applied", "summary": result.get("summary", "Schreibziel angewendet.")}), 202 if result.get("queued") else 200


def _update_write_ai_job(user_id, job_id, updater):
    if not re.fullmatch(r"[0-9a-f-]{36}", job_id or ""):
        return None
    _, job_path, _, _ = _write_ai_job_paths(user_id, job_id)
    if not job_path.exists():
        return None
    result = {}

    def update(current):
        try:
            job = json.loads(current)
        except json.JSONDecodeError:
            result["error"] = "KI-Job ist beschädigt"
            return current
        if job.get("user_id") != user_id:
            result["missing"] = True
            return current
        try:
            updater(job, result)
        except ValueError as exc:
            result["error"] = str(exc)
            return current
        result["job"] = job
        return json.dumps(job, ensure_ascii=False, indent=2) + "\n"

    update_text_file(job_path, update)
    return result


@app.route("/api/write-ai/jobs/<job_id>/cancel", methods=["POST"])
@require_auth
@csrf_protect
def api_cancel_write_ai_job(job_id):
    def cancel(job, _result):
        status = job.get("status")
        if status == "queued":
            job.update(status="cancelled", cancelled_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                       cancel_reason="user")
        elif status == "running":
            # The host worker polls this state and terminates only its own Pi
            # process group.  The browser may close immediately afterwards.
            job.update(status="cancelling", cancel_requested_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                       cancel_reason="user")
        elif status == "cancelling":
            return
        else:
            raise ValueError("Dieser KI-Job kann nicht mehr abgebrochen werden")

    result = _update_write_ai_job(_current_user()["id"], job_id, cancel)
    if not result or result.get("missing"):
        return jsonify({"error": "KI-Job nicht gefunden"}), 404
    if result.get("error"):
        return jsonify({"error": result["error"]}), 409
    job = result["job"]
    return jsonify({"ok": True, "status": job["status"], "can_undo": job["status"] == "cancelled" and not job.get("started_at")})


@app.route("/api/write-ai/jobs/<job_id>/undo-cancel", methods=["POST"])
@require_auth
@csrf_protect
def api_undo_cancel_write_ai_job(job_id):
    def undo(job, _result):
        if job.get("status") != "cancelled" or job.get("started_at"):
            raise ValueError("Nur ein noch nicht gestarteter Abbruch kann rückgängig gemacht werden")
        job.update(status="queued", requeued_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
        job.pop("cancelled_at", None)
        job.pop("cancel_requested_at", None)
        job.pop("cancel_reason", None)

    result = _update_write_ai_job(_current_user()["id"], job_id, undo)
    if not result or result.get("missing"):
        return jsonify({"error": "KI-Job nicht gefunden"}), 404
    if result.get("error"):
        return jsonify({"error": result["error"]}), 409
    return jsonify({"ok": True, "status": "queued"})


def _write_ai_markdown(content, ai_response, time_str, mode="append", datetime_str=None):
    """Write AI-generated entry to markdown.

    mode="replace": the AI response becomes the entry body (original text is replaced).
    mode="append": original text is kept, AI response appended as a quoted block.
    Legacy (mode=None or unknown): original "## KI-Antwort" block format.
    """
    timestamp = datetime_str or time_str
    if mode == "replace":
        return f"___\n\n## KI | Datum & Uhrzeit: {timestamp}\n{ai_response.strip()}\n\n___\n\n"
    if mode == "append":
        return f"___\n\n## KI | Datum & Uhrzeit: {timestamp}\n{content.strip()}\n\n---\n> {ai_response}\n\n___\n\n"
    return f"___\n\n## KI-Antwort | Datum & Uhrzeit: {timestamp}\n{content.strip()}\n\n---\n> {ai_response}\n\n___\n\n"


@app.route("/api/ai-submit", methods=["POST"])
@require_auth
@csrf_protect
def api_ai_submit():
    """Handle AI submission → call AI API → write response to markdown.

    New format:  { ai_provider_id, ai_function_id, text }
    Legacy:      { ai_template_id, message }
    """
    data = request.get_json()
    now, _ = get_tz_aware_now()
    time_str = now.strftime("%H:%M:%S")
    datetime_str = now.strftime("%Y-%m-%d %H:%M:%S")

    config = load_config()

    ai_provider_id = data.get("ai_provider_id")
    ai_function_id = data.get("ai_function_id")
    text = (data.get("text") or data.get("message") or "").strip()

    provider = None
    ai_function = None
    mode = "append"

    if ai_provider_id and ai_function_id:
        for p in config.get("ai_providers", []):
            if p["id"] == ai_provider_id:
                provider = p
                break
        if not provider:
            return jsonify({"error": "Unknown AI provider"}), 400
        for f in config.get("ai_functions", []):
            if f["id"] == ai_function_id:
                ai_function = f
                break
        if not ai_function:
            return jsonify({"error": "Unknown AI function"}), 400
        mode = ai_function.get("mode", "append")
    else:
        ai_template_id = data.get("ai_template_id")
        if not ai_template_id or not text:
            return jsonify({"error": "Missing ai_provider_id/ai_function_id or ai_template_id and text"}), 400
        ai_template = None
        for t in config.get("ai_templates", []):
            if t["id"] == ai_template_id:
                ai_template = t
                break
        if not ai_template:
            return jsonify({"error": "Unknown AI template"}), 400
        provider = ai_template
        ai_function = ai_template
        mode = "legacy"

    if not text:
        return jsonify({"error": "Missing text"}), 400

    # write_to_journal respektieren (default true fuer ai_templates)
    write_to_journal = True
    if ai_function and "write_to_journal" in ai_function:
        write_to_journal = bool(ai_function.get("write_to_journal"))
    elif provider and "write_to_journal" in provider:
        write_to_journal = bool(provider.get("write_to_journal"))

    try:
        ai_response = _call_ai_api(provider, ai_function, text)
    except (ConnectionError, ValueError) as e:
        logger.error(f"AI API error: {e}")
        return jsonify({"error": str(e)}), 500

    if not write_to_journal:
        logger.info("AI template write_to_journal=false – skipping journal write")
        return jsonify({"ok": True, "response": ai_response, "mode": mode})

    filepath = _get_journal_path(now)
    md_entry = _write_ai_markdown(text, ai_response, time_str, mode=mode, datetime_str=datetime_str)
    _append_journal_entry(_current_user()["id"], filepath, md_entry)

    logger.info("AI response written successfully")
    return jsonify({"ok": True, "response": ai_response, "mode": mode})


__all__ = ['api_write_ai_draft', 'api_write_ai_submit', 'api_write_ai_job', 'api_commit_write_ai_job_draft', 'api_apply_write_ai_proposal', '_update_write_ai_job', 'api_cancel_write_ai_job', 'api_undo_cancel_write_ai_job', '_write_ai_markdown', 'api_ai_submit']
