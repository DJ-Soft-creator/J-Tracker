"""Journal submission, dashboard and revision routes.

AI maintenance contract:
Own normal submit, Today/dashboard, conflict, revision and search handlers.
All persistent writes must continue through the existing guarded helpers.
The module is imported only after main.py has created the Flask app and core
helpers. Runtime values that tests or DEV configuration may replace are synced
from that facade before each request.
"""

import sys

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before route modules are imported")
globals().update({name: getattr(_core, name) for name in dir(_core) if not name.startswith("__")})
_DYNAMIC_NAMES = ('DATA_DIR', 'IS_DEV', '_append_journal_entry', '_managed_document_for_journal', '_update_journal_file', 'database_url', 'is_enabled', 'get_tz_aware_now')


def _sync_runtime():
    for name in _DYNAMIC_NAMES:
        if hasattr(_core, name):
            globals()[name] = getattr(_core, name)


@app.before_request
def _sync_journal_routes_runtime():
    _sync_runtime()


@app.route("/api/submit", methods=["POST"])
@require_auth
@csrf_protect
def api_submit():
    """Handle form submission → write clean markdown (no IDs)."""
    data = request.get_json()
    template_id = data.get("template_id") or data.get("form_id")
    content = data.get("content", "")
    values = data.get("values", {})
    consume_draft_revision = data.get("consume_draft_revision")
    write_session_id = data.get("write_session_id")
    background_agent = data.get("background_agent", False)
    if not isinstance(background_agent, bool):
        return jsonify({"error": "background_agent muss true oder false sein"}), 400
    session_data = None
    session_media = []
    if consume_draft_revision is not None and (
        not isinstance(consume_draft_revision, str)
        or not re.fullmatch(r"[0-9a-f]{64}", consume_draft_revision)
    ):
        return jsonify({"error": "Ungültige Revision des KI-Schreibstands"}), 400

    logger.info(f"Submitting template {template_id}")

    config = load_config()
    template_def = None
    for t in config.get("templates", []):
        if t["id"] == template_id:
            template_def = t
            break

    if not template_def:
        logger.error(f"Unknown template: {template_id}")
        return jsonify({"error": "Unknown template"}), 400

    user = _current_user()
    uid = user["id"] if user else None
    if write_session_id is not None:
        try:
            session_data, _session_dir, session_media = write_sessions_module.submission_media(uid, write_session_id)
        except ValueError as exc:
            return jsonify({"error": str(exc), "write_session_conflict": True}), 409
        if template_def.get("type") != "simple" or session_data.get("content", "").strip() != content:
            return jsonify({
                "error": "Der Session-Stand stimmt nicht mit der Übermittlung überein. Bitte Seite neu laden und erneut senden.",
                "write_session_conflict": True,
            }), 409
    assigned_users = template_def.get("assigned_users") or []
    if assigned_users and uid not in assigned_users:
        return jsonify({"error": "Template is not assigned to this user"}), 403

    project_assignment = data.get("project_assignment", "")
    if project_assignment and not template_def.get("project_selector"):
        return jsonify({"error": "This template does not support project assignment"}), 400
    if project_assignment:
        project_assignment = brain_module._valid_project_reference(uid, project_assignment)
        if project_assignment is None:
            return jsonify({"error": "Unknown personal project"}), 400

    now, _ = get_tz_aware_now()
    time_str = now.strftime("%H:%M:%S")
    datetime_str = now.strftime("%Y-%m-%d %H:%M:%S")

    background_request = None
    is_hashtag_template = (
        template_def.get("type") == "simple"
        and template_def.get("hashtag_template") is True
        and isinstance(template_def.get("hashtags"), list)
    )
    is_quick_template = template_id == "schnell" or is_hashtag_template
    if background_agent:
        if not is_quick_template or not isinstance(content, str) or not content.strip():
            return jsonify({"error": "Die Hintergrund-KI ist nur für eine nichtleere Schnell-Notiz oder ein Hashtag-Template verfügbar."}), 400
        workflows = tagging_module.catalog_view(uid).get("ai", {})
        text_tags = {
            tagging_module.normalise_tag(match.group(1))
            for match in re.finditer(r"(?<![\w#])#([\w-]+)", content, re.UNICODE)
        }
        selected = [(tag, workflows[tag]) for tag in sorted(text_tags) if tag in workflows]
        if len(selected) != 1:
            return jsonify({"error": "Für den Background-Agent bitte genau ein konfiguriertes AI-Hashtag verwenden."}), 400
        background_tag, background_workflow = selected[0]
        if background_workflow.get("agent") != "pi":
            return jsonify({"error": "Background-Agent-Sitzungen benötigen einen lokalen Pi-Workflow."}), 400
        if background_workflow.get("target") == "daily_journal":
            return jsonify({"error": "Dieser AI-Workflow ist ausschließlich für die Share-API bestimmt."}), 400
        try:
            background_snapshots, background_references = _knowledge_snapshots_for_write(uid, content)
            background_write_target = _write_target_snapshot_for_write(uid, content)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        background_user_request = _write_ai_user_request(
            content, background_tag, background_snapshots, background_references,
        )
        if background_write_target:
            background_user_request = re.sub(
                r"(?<![\w#])#" + re.escape(background_write_target["tag"]) + r"\b",
                "", background_user_request, flags=re.UNICODE,
            ).strip()
        if not background_user_request:
            return jsonify({"error": "Bitte zusätzlich zu den Routing-Hashtags einen Auftrag formulieren."}), 400
        background_request = (
            background_tag, background_workflow, background_user_request,
            background_snapshots, background_references, background_write_target,
        )

    write_to_journal = template_def.get("write_to_journal", True)
    target_file = template_def.get("target_file")
    if session_data and not write_to_journal and session_media:
        return jsonify({
            "error": "Anhänge können mit diesem Template nicht übernommen werden.",
            "write_session_conflict": True,
        }), 409
    shopping_import = None
    # Family-Side-Effect: target_file → Task an Projekt-Datei anhaengen
    if target_file:
        title_val = values.get("titel") or values.get("item") or content or template_id
        user_uid = values.get("verantwortlich") or values.get("user") or uid
        target_date = values.get("target_date") or ""
        # Einkaufsliste: Zeilenaufbereitung und Zielwahl gehoeren fachlich in
        # die Family-Domaene, damit hier nur die HTTP-Fehlerabbildung bleibt.
        if target_file.endswith("einkaufsliste.md"):
            target_project = data.get("family_target_project")
            if target_project is not None and not isinstance(target_project, str):
                return jsonify({"error": "Ungültige Einkaufsliste"}), 400
            try:
                added_items = family_module.import_shopping_lines(
                    text=values.get("item") or content,
                    target_project=(target_project or "").strip(),
                    target_file=target_file,
                    user_uid=str(user_uid).strip() if user_uid else "",
                    created_by=uid,
                )
            except LookupError:
                return jsonify({"error": "Einkaufsliste nicht gefunden"}), 404
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except OSError as exc:
                logger.error("Family shopping import failed: %s", exc)
                return jsonify({"error": "Einkaufslistenpunkt konnte nicht gespeichert werden"}), 500
            shopping_import = {"added_shopping_items": added_items}
        else:
            try:
                family_module.append_task_to_target_file(
                    target_file=target_file,
                    title=str(title_val).strip() or template_id,
                    user_uid=str(user_uid).strip() if user_uid else "",
                    target_date=str(target_date).strip(),
                    created_by=uid,
                )
            except Exception as e:
                logger.error(f"Family append_task_to_target_file failed: {e}")

    planner_id = None

    # Aufgabenplaner → recurring-Eintrag im Planner
    if template_id == "aufgabenplaner":
        try:
            planner_id = family_module.add_recurring_to_planner(
                title=str(values.get("titel") or "").strip(),
                user_uid=str(values.get("user") or "").strip(),
                recurrence=str(values.get("recurrence") or "").strip(),
                target_date=str(values.get("target_date") or "").strip(),
                created_by=uid,
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Family add_recurring_to_planner failed: {e}")
            return jsonify({"error": "Plan konnte nicht gespeichert werden"}), 500

    # Projekt → eigenstaendige Projekt-Datei
    if template_id == "projekt":
        try:
            v_users_raw = str(values.get("verantwortlich") or "").strip()
            assigned = [u.strip() for u in v_users_raw.split(",") if u.strip()]
            family_module.create_standalone_project(
                title=str(values.get("titel") or "").strip() or template_id,
                template_id=template_id,
                assigned_users=assigned,
                created_by=uid,
            )
        except Exception as e:
            logger.error(f"Family create_standalone_project failed: {e}")

    # Kein Journal-Eintrag wenn write_to_journal false
    if not write_to_journal:
        logger.info(f"Template {template_id} write_to_journal=false – skipping journal write")
        if write_session_id:
            try:
                write_sessions_module.commit_submission_without_media(
                    uid, write_session_id, target_file or template_id,
                )
            except ValueError as exc:
                return jsonify({"error": str(exc), "write_session_conflict": True}), 409
        response = {"ok": True}
        if planner_id:
            response["planner_id"] = planner_id
        if shopping_import:
            response.update(shopping_import)
        consumed = _consume_write_draft(uid, consume_draft_revision)
        if consumed["requested"]:
            response.update(draft_consumed=consumed["consumed"], draft_revision=consumed["revision"])
        return jsonify(response)

    background_attachments = {"snapshot_id": None, "images": [], "context": "", "count": 0}
    background_attachment_error = None
    if background_request and write_session_id and session_media:
        try:
            background_attachments = ai_attachments_module.prepare_session_attachments(
                DATA_DIR, uid, write_session_id, content,
                whisper_model_name=data.get("whisper_model"),
            )
        except ai_attachments_module.AiAttachmentError as exc:
            background_attachment_error = str(exc)

    filepath = _get_journal_path(now)
    media_stage = None
    try:
        # Final files are moved before their immutable journal marker is written.
        # If writing the journal fails, the except block moves every file back.
        if write_session_id:
            media_stage = write_sessions_module.stage_submission_media(uid, write_session_id, filepath)
            session_media = media_stage["items"]

        # Build clean markdown (no UUID, no headers/separators)
        if template_def.get("type") == "simple":
            combined_content = content.strip()
            media_text = write_sessions_module.media_markdown(session_media)
            if media_text:
                combined_content = (combined_content + "\n\n" + media_text).strip()
            quick_started_at = None
            if is_quick_template and session_data:
                try:
                    created_at = datetime.fromisoformat(str(session_data.get("created_at")))
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=now.tzinfo)
                    quick_started_at = created_at.astimezone(now.tzinfo).strftime("%d.%m. %H:%M:%S")
                except (TypeError, ValueError):
                    quick_started_at = None
            md_content = _write_markdown_entry(
                template_id, combined_content, time_str, datetime_str, quick_started_at,
                quick_style=is_hashtag_template, heading=template_def.get("heading"),
            )
        else:
            label = template_def.get("label", template_id)
            fields = template_def.get("fields", [])
            md_content = _write_form_markdown(label, time_str, fields, values, datetime_str)

        append_result = _append_journal_entry(uid, filepath, md_content) or {}
    except Exception:
        if media_stage:
            write_sessions_module.rollback_staged_media(media_stage)
        if background_attachments["snapshot_id"]:
            ai_attachments_module.cleanup_snapshot(DATA_DIR, uid, background_attachments["snapshot_id"])
        raise
    if media_stage:
        write_sessions_module.commit_submission_media(uid, media_stage)
    ai_sessions = []
    background_error = None
    if background_request:
        background_tag, background_workflow, background_user_request, background_snapshots, background_references, background_write_target = background_request
        journal_context = read_text_file(filepath) if filepath.exists() else ""
        try:
            if background_attachment_error:
                raise ValueError(background_attachment_error)
            if agent_jobs_enabled():
                if background_references or background_write_target:
                    raise ValueError("DB-Background-Jobs unterstützen noch keine Host-Referenzen oder Schreibziele.")
                snapshot_parts = ["Heutiges Journal:\n" + journal_context]
                snapshot_parts.extend(
                    f"Knowledge #{item['tag']} (Referenzmaterial):\n{item['content']}"
                    for item in background_snapshots
                )
                if background_attachments["context"]:
                    snapshot_parts.append(background_attachments["context"])
                session_id = background_jobs.create_agent_session(
                    uid, background_tag, "pi", str(background_workflow.get("prompt") or ""),
                    model_name=str(background_workflow.get("model") or ""),
                )
                queued = background_jobs.queue_agent_request(
                    session_id, uid, background_user_request, str(uuid.uuid4()), submitted_text=content,
                    context_snapshot="\n\n".join(snapshot_parts),
                    attachment_images=background_attachments["images"],
                )
                ai_sessions.append({"session_id": session_id, "job_id": queued["job_id"], "status": "queued", "db_native": True})
            else:
                job = _queue_pi_write_job(
                    uid, user["username"], background_tag, background_workflow, "today_journal",
                    content, background_user_request, journal_context, background_snapshots,
                    background_references, background_write_target,
                    background_attachments["images"], background_attachments["context"],
                    delivery="background",
                )
                ai_sessions.append({
                    "session_id": job["session_id"], "job_id": job["id"],
                    "path": job["source_path"], "status": job["status"],
                })
        except (OSError, ValueError) as exc:
            logger.error("Journal was saved, but background Pi job could not be queued: %s", exc)
            if background_attachments["snapshot_id"]:
                ai_attachments_module.cleanup_snapshot(DATA_DIR, uid, background_attachments["snapshot_id"])
            background_error = (
                "Journal gespeichert, Background-Agent konnte die Anhänge jedoch nicht übernehmen: " + str(exc)
                if background_attachment_error
                else "Journal gespeichert, Background-Agent konnte jedoch nicht gestartet werden."
            )

    if project_assignment:
        brain_module.record_template_assignment(
            uid,
            filepath,
            project_assignment,
            content.strip().splitlines()[0] if template_id == "aufgabe" and content.strip() else None,
            append_result.get("line_hint"),
        )

    logger.info("File written successfully")
    response = {"ok": True, "ai_sessions": ai_sessions}
    if background_error:
        response["background_error"] = background_error
    if shopping_import:
        response.update(shopping_import)
    consumed = _consume_write_draft(uid, consume_draft_revision)
    if consumed["requested"]:
        response.update(draft_consumed=consumed["consumed"], draft_revision=consumed["revision"])
    return jsonify(response)


@app.route("/api/write/template-preview", methods=["POST"])
@require_auth
@csrf_protect
def api_write_template_preview():
    """Render a selected journal template without creating a new entry."""
    data = request.get_json(silent=True) or {}
    template_id = data.get("template_id")
    template_def = next(
        (item for item in load_config().get("templates", []) if item.get("id") == template_id),
        None,
    )
    if not template_def:
        return jsonify({"error": "Unknown template"}), 400
    user = _current_user()
    assigned_users = template_def.get("assigned_users") or []
    if assigned_users and user["id"] not in assigned_users:
        return jsonify({"error": "Template is not assigned to this user"}), 403
    if template_def.get("write_to_journal", True) is False:
        return jsonify({"error": "Dieses Template bearbeitet eine andere Quelle und kann hier nicht angewendet werden."}), 400

    now, _ = get_tz_aware_now()
    time_str = now.strftime("%H:%M:%S")
    datetime_str = now.strftime("%Y-%m-%d %H:%M:%S")
    if template_def.get("type") == "simple":
        content = data.get("content")
        if not isinstance(content, str):
            return jsonify({"error": "content (string) required"}), 400
        is_hashtag_template = (
            template_def.get("hashtag_template") is True
            and isinstance(template_def.get("hashtags"), list)
        )
        rendered = _write_markdown_entry(
            template_id, content, time_str, datetime_str, quick_style=is_hashtag_template,
            heading=template_def.get("heading"),
        )
    else:
        values = data.get("values")
        if not isinstance(values, dict):
            return jsonify({"error": "values (object) required"}), 400
        fields = template_def.get("fields", [])
        for field in fields:
            value = values.get(field.get("id"), "")
            if field.get("required") and not str(value).strip():
                return jsonify({"error": f"Bitte „{field.get('label', field.get('id'))}“ ausfüllen"}), 400
        rendered = _write_form_markdown(
            template_def.get("label", template_id), time_str, fields, values, datetime_str,
        )
    prefix, suffix = "___\n\n", "\n\n___\n\n"
    if not rendered.startswith(prefix) or not rendered.endswith(suffix):
        return jsonify({"error": "Template konnte nicht als Eintrag gerendert werden."}), 500
    return jsonify({"content": rendered[len(prefix):-len(suffix)]})


@app.route("/api/dashboard/init", methods=["POST"])
@require_auth
@csrf_protect
def api_dashboard_init():
    """Initialize today's journal file if it doesn't exist."""
    now, _ = get_tz_aware_now()
    filepath = _get_journal_path(now)
    user = _current_user()

    def initialize(current):
        body = current or f"# Journal {now.strftime('%Y-%m-%d')}\n\n"
        return tagging_module.refresh_journal_footer(
            user["id"], body, tagging_module.journal_date_from_path(filepath),
        )

    _update_journal_file(filepath, initialize)

    return jsonify({"ok": True, "file": filepath.name, "path": str(filepath)})


def _get_today_entries(user_id=None, journal_date=None):
    """Read and parse one requested personal journal day from disk."""
    if user_id is None:
        user_id = session.get("user_id")
    now, _ = get_tz_aware_now()
    if journal_date:
        try:
            requested = datetime.strptime(journal_date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Ungültiges Journaldatum")
        now = now.replace(year=requested.year, month=requested.month, day=requested.day)
    day_dir = _get_day_dir(now, user_id=user_id)
    all_entries = []

    if day_dir.is_dir():
        for f in sorted(day_dir.iterdir()):
            if f.name.startswith("Journal_") and f.suffix == ".md":
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        content = fh.read()
                    entries = _parse_entries_from_markdown(content)
                    for entry_text in entries:
                        all_entries.append({"content": entry_text, "file": f.name, "media": write_sessions_module.media_from_text(user_id, entry_text)})
                except (IOError, OSError) as e:
                    logger.error(f"Failed to read journal file {f}: {e}")

    all_entries.reverse()
    _attach_current_managed_media_urls(user_id, now.strftime("%Y-%m-%d"), all_entries)
    return all_entries


def _attach_current_managed_media_urls(user_id, journal_date, entries):
    """Use verified revision-bound URLs for a managed journal's current media."""
    if not IS_DEV or not is_enabled() or not entries:
        return
    # Ohne ein einziges Medium gibt es keine URL zu ersetzen. Der Aufbau einer
    # eigenen Engine je Anfrage kostet rund 20 ms und lohnt sich dafuer nicht --
    # der Heute-Tab holt diesen Pfad bei jedem Tabwechsel erneut.
    if not any(entry["media"] for entry in entries):
        return
    relative_path = "/".join((journal_date[:4], journal_date[5:7], journal_date[8:10],
                                "Journal_" + journal_date + ".md"))
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(database_url(), pool_pre_ping=True, pool_size=2, max_overflow=0)
        try:
            with engine.connect() as connection:
                document = connection.execute(text("""
                    SELECT current_revision_id FROM documents
                    WHERE owner_id = :owner AND scope = 'personal' AND document_type = 'journal'
                      AND storage_root_key = :root AND relative_path = :path
                      AND storage_mode = 'db_managed' AND state = 'active'
                """), {"owner": user_id, "root": "dev-user-" + user_id, "path": relative_path}).mappings().one_or_none()
                if document is None:
                    return
                references = connection.execute(text("""
                    SELECT media_id FROM document_media_references WHERE document_revision_id = :revision
                """), {"revision": document["current_revision_id"]}).scalars().all()
        finally:
            engine.dispose()
    except Exception as error:
        logger.warning("Managed media URLs unavailable for %s: %s", journal_date, error)
        return
    verified_ids = {str(media_id) for media_id in references}
    for entry in entries:
        for media in entry["media"]:
            if str(media.get("id")) in verified_ids:
                media["media_url"] = "/api/journals/{}/history/{}/media/{}".format(
                    journal_date, document["current_revision_id"], media["id"])
                media["integrity_status"] = "verified"
            else:
                media["integrity_status"] = "unverified"


@app.route("/api/dashboard")
@app.route("/api/dashboard/refresh")
@require_auth
def api_dashboard():
    """Read one personal journal day; default is today."""
    try:
        return jsonify(_get_today_entries(journal_date=request.args.get("date")))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/dashboard/edit", methods=["POST"])
@require_auth
@csrf_protect
def api_dashboard_edit():
    """Edit a dashboard entry by replacing its content in the markdown file."""
    data = request.get_json()
    old_content = data.get("content", "")  # Original full text of entry (without ___)
    new_content = data.get("new_content", "")  # Edited version
    filename = data.get("filename")  # Optional: specific journal filename
    journal_date = data.get("journal_date")
    write_session_id = data.get("write_session_id")
    uid = (_current_user() or {}).get("id")

    now, _ = get_tz_aware_now()

    if journal_date:
        try:
            date_value = datetime.strptime(journal_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            return jsonify({"error": "Ungültiges Journaldatum"}), 400
        filepath = _get_day_dir(date_value) / (filename or f"Journal_{journal_date}.md")
    elif filename:
        filepath = _get_day_dir(now) / filename
    else:
        filepath = _get_journal_path(now)

    if not filepath.exists():
        return jsonify({"error": "No journal file"}), 404

    media_stage = None
    if write_session_id:
        try:
            media_stage = write_sessions_module.stage_submission_media(uid, write_session_id, filepath)
        except ValueError as exc:
            return jsonify({"error": str(exc), "write_session_conflict": True}), 409
    # An unresolved placeholder must never reach the file, so it is stripped
    # even when this edit carries no attachments at all.
    new_content = write_sessions_module.apply_media_placeholder(
        new_content, write_sessions_module.media_markdown(media_stage["items"]) if media_stage else "",
    )

    escaped_old = re.escape(old_content.strip()) + r'\s*'
    pattern = re.compile(r'(___\n\n)' + escaped_old + r'(\n\s*___)', re.DOTALL)
    stripped_new = new_content.strip()

    def replace_entry(current):
        updated = pattern.sub(lambda match: match.group(1) + stripped_new + match.group(2), current, count=1)
        if updated == current:
            return current, {"conflict": True}
        _write_journal_backup(filepath, current)
        # Without the day, journal_blocks cannot anchor a quick note, and the
        # footer rewrite would silently drop everything recorded for it.
        return tagging_module.refresh_journal_footer(
            _current_user()["id"], updated, tagging_module.journal_date_from_path(filepath),
        ), {"ok": True}

    try:
        result = _update_journal_file(filepath, replace_entry)
    except Exception:
        if media_stage:
            write_sessions_module.rollback_staged_media(media_stage)
        raise
    if result.get("conflict"):
        if media_stage:
            write_sessions_module.rollback_staged_media(media_stage)
        return jsonify({"error": "Entry not found or modified in background", "conflict": True}), 409
    if media_stage:
        write_sessions_module.commit_submission_media(uid, media_stage)
    return jsonify({"ok": True})


@app.route("/api/dashboard/delete", methods=["POST"])
@require_auth
@csrf_protect
def api_dashboard_delete():
    """Delete a dashboard entry by removing its content from the markdown file."""
    data = request.get_json()
    content_to_delete = data.get("content", "")
    filename = data.get("filename")
    journal_date = data.get("journal_date")

    now, _ = get_tz_aware_now()

    if journal_date:
        try:
            date_value = datetime.strptime(journal_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            return jsonify({"error": "Ungültiges Journaldatum"}), 400
        filepath = _get_day_dir(date_value) / (filename or f"Journal_{journal_date}.md")
    elif filename:
        filepath = _get_day_dir(now) / filename
    else:
        filepath = _get_journal_path(now)

    if not filepath.exists():
        return jsonify({"error": "No journal file"}), 404

    escaped_content = re.escape(content_to_delete.strip()) + r'\s*'
    pattern = re.compile(r'(___\n\n)' + escaped_content + r'(\n\s*___)', re.DOTALL)

    def delete_entry(current):
        updated = pattern.sub('', current, count=1)
        if updated == current:
            return current, {"conflict": True}
        _write_journal_backup(filepath, current)
        return tagging_module.refresh_journal_footer(
            _current_user()["id"], updated, tagging_module.journal_date_from_path(filepath),
        ), {"ok": True}

    result = _update_journal_file(filepath, delete_entry)
    if result.get("conflict"):
        return jsonify({"error": "Entry not found or modified in background", "conflict": True}), 409
    return jsonify({"ok": True})


@app.route("/api/journal-conflicts/<conflict_id>")
@require_auth
def api_journal_conflict(conflict_id):
    """Return the explicitly saved conflict sides for the current user only."""
    loaded = _owned_managed_conflict(conflict_id)
    if loaded is None:
        return jsonify({"error": "Konflikt nicht gefunden"}), 404
    engine, conflict = loaded
    try:
        from sqlalchemy import text
        with engine.connect() as connection:
            versions = dict(connection.execute(text("""
                SELECT kind, markdown_content FROM sync_conflict_versions WHERE conflict_id = :id
            """), {"id": conflict["id"]}).all())
        return jsonify({"id": str(conflict["id"]), "status": conflict["status"],
                        "resolution": conflict["resolution"], "external": versions.get("external", ""),
                        "jtracker": versions.get("jtracker", "")})
    finally:
        engine.dispose()


@app.route("/api/journal-conflicts/<conflict_id>/resolve", methods=["POST"])
@require_auth
@csrf_protect
def api_resolve_journal_conflict(conflict_id):
    """Materialize an explicitly selected saved conflict version."""
    data = request.get_json() or {}
    choice = data.get("choice")
    merged_content = data.get("merged_content")
    if choice not in {"external", "jtracker", "both", "manual"}:
        return jsonify({"error": "Ungültige Konfliktentscheidung"}), 400
    loaded = _owned_managed_conflict(conflict_id)
    if loaded is None:
        return jsonify({"error": "Konflikt nicht gefunden"}), 404
    engine, conflict = loaded
    try:
        from conflicts import materialize_resolution
        from document_store import configured_document_store
        revision = materialize_resolution(
            configured_document_store(engine), conflict_id=conflict["id"],
            storage_root_key=conflict["storage_root_key"], root=(DATA_DIR / session["user_id"]).resolve(),
            choice=choice, merged_content=merged_content, idempotency_key=str(uuid.uuid4()),
            now=datetime.now(timezone.utc),
        )
        return jsonify({"ok": True, "revision_id": str(revision)})
    except ValueError as error:
        return jsonify({"error": str(error)}), 409
    finally:
        engine.dispose()


@app.route("/api/journals/<journal_date>")
@require_auth
def api_managed_journal(journal_date):
    """Read the current materialized content and sync metadata of one managed journal."""
    loaded = _owned_managed_journal(journal_date)
    if loaded is None:
        return jsonify({"error": "Verwaltetes Journal nicht gefunden"}), 404
    engine, document, path = loaded
    try:
        content = read_text_file(path)
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return jsonify({"date": journal_date, "document_id": str(document["id"]),
                        "content": content, "revision_id": str(document["current_revision_id"]),
                        "materialized_file_hash": document["materialized_file_hash"],
                        "hash_matches": actual_hash == document["materialized_file_hash"],
                        "sync_status": document["state"]})
    except OSError:
        return jsonify({"error": "Journaldatei kann nicht gelesen werden"}), 409
    finally:
        engine.dispose()


@app.route("/api/journals/<journal_date>/history")
@require_auth
def api_managed_journal_history(journal_date):
    """List immutable revision metadata without exposing another user's history."""
    loaded = _owned_managed_journal(journal_date)
    if loaded is None:
        return jsonify({"error": "Verwaltetes Journal nicht gefunden"}), 404
    engine, document, _path = loaded
    try:
        from sqlalchemy import text
        with engine.connect() as connection:
            revisions = connection.execute(text("""
                SELECT revision.id, revision.parent_revision_id, revision.origin, revision.reason,
                       revision.file_hash, revision.created_at, revision.summary,
                       count(media.media_id) AS media_count
                FROM document_revisions revision
                LEFT JOIN document_media_references media ON media.document_revision_id = revision.id
                WHERE revision.document_id = :document
                GROUP BY revision.id
                ORDER BY revision.created_at DESC, revision.id DESC
            """), {"document": document["id"]}).mappings().all()
            media_rows = connection.execute(text("""
                SELECT reference.document_revision_id, reference.media_id, reference.media_path,
                       reference.media_type, reference.mime_type, reference.file_hash, reference.byte_size
                FROM document_media_references reference
                JOIN document_revisions revision ON revision.id = reference.document_revision_id
                WHERE revision.document_id = :document
            """), {"document": document["id"]}).mappings().all()
        from media_references import verify_persisted_reference
        root = (DATA_DIR / session["user_id"]).resolve()
        integrity = {}
        for media in media_rows:
            revision_id = str(media["document_revision_id"])
            check = verify_persisted_reference(root, **{key: media[key] for key in (
                "media_id", "media_path", "media_type", "mime_type", "file_hash", "byte_size")})
            integrity.setdefault(revision_id, []).append(check.status)
        revision_payload = []
        for item in revisions:
            result = {**dict(item), "id": str(item["id"]),
                      "parent_revision_id": str(item["parent_revision_id"]) if item["parent_revision_id"] else None,
                      "created_at": item["created_at"].isoformat()}
            statuses = integrity.get(result["id"], [])
            result["media_integrity"] = "verified" if not statuses or all(status == "verified" for status in statuses) else "attention"
            revision_payload.append(result)
        return jsonify({"document_id": str(document["id"]), "current_revision_id": str(document["current_revision_id"]),
                        "revisions": revision_payload})
    finally:
        engine.dispose()


@app.route("/api/journals/<journal_date>/history/<revision_id>")
@require_auth
def api_managed_journal_revision(journal_date, revision_id):
    """Read one reconstructible historical revision of the current user's journal."""
    loaded = _owned_managed_journal(journal_date)
    if loaded is None:
        return jsonify({"error": "Verwaltetes Journal nicht gefunden"}), 404
    try:
        revision_uuid = uuid.UUID(revision_id)
    except ValueError:
        return jsonify({"error": "Ungültige Revision"}), 404
    engine, document, _path = loaded
    try:
        from sqlalchemy import text
        with engine.connect() as connection:
            content = connection.scalar(text("""
                SELECT snapshot.markdown_content FROM document_snapshots snapshot
                JOIN document_revisions revision ON revision.id = snapshot.document_revision_id
                WHERE snapshot.document_revision_id = :revision AND revision.document_id = :document
            """), {"revision": revision_uuid, "document": document["id"]})
            if content is None:
                blocks = connection.execute(text("""
                    SELECT block_revision.markdown_content FROM document_revision_blocks manifest
                    JOIN block_revisions block_revision ON block_revision.id = manifest.block_revision_id
                    JOIN document_revisions revision ON revision.id = manifest.document_revision_id
                    WHERE manifest.document_revision_id = :revision AND revision.document_id = :document
                    ORDER BY manifest.position
                """), {"revision": revision_uuid, "document": document["id"]}).scalars().all()
                content = "".join(blocks) if blocks else None
            media = connection.execute(text("""
                SELECT media_id, media_path, media_type, mime_type, file_hash, byte_size, verified_at
                FROM document_media_references
                WHERE document_revision_id = :revision
                ORDER BY media_id
            """), {"revision": revision_uuid}).mappings().all()
        if content is None:
            return jsonify({"error": "Revision nicht gefunden oder nicht rekonstruierbar"}), 404
        return jsonify({"document_id": str(document["id"]), "revision_id": str(revision_uuid), "content": content,
                        "media": [{"id": str(item["media_id"]), "media_path": item["media_path"],
                                   "type": item["media_type"], "mime_type": item["mime_type"],
                                   "sha256": item["file_hash"], "byte_size": item["byte_size"],
                                   "verified_at": item["verified_at"].isoformat(),
                                   "media_url": "/api/journals/{}/history/{}/media/{}".format(
                                       journal_date, revision_uuid, item["media_id"])} for item in media]})
    finally:
        engine.dispose()


@app.route("/api/journals/<journal_date>/history/<revision_id>/media/<media_id>")
@require_auth
def api_managed_journal_revision_media(journal_date, revision_id, media_id):
    """Serve one owned historical attachment only while its stored hash still matches."""
    loaded = _owned_managed_journal(journal_date)
    if loaded is None:
        return jsonify({"error": "Verwaltetes Journal nicht gefunden"}), 404
    try:
        revision_uuid, media_uuid = uuid.UUID(revision_id), uuid.UUID(media_id)
    except ValueError:
        return jsonify({"error": "Medium nicht gefunden"}), 404
    engine, document, _path = loaded
    try:
        from sqlalchemy import text
        from media_references import verify_persisted_reference
        with engine.connect() as connection:
            reference = connection.execute(text("""
                SELECT media_id, media_path, media_type, mime_type, file_hash, byte_size
                FROM document_media_references
                WHERE document_revision_id = :revision AND media_id = :media
                  AND EXISTS (SELECT 1 FROM document_revisions
                              WHERE id = :revision AND document_id = :document)
            """), {"revision": revision_uuid, "media": media_uuid, "document": document["id"]}).mappings().one_or_none()
        if reference is None:
            return jsonify({"error": "Medium nicht gefunden"}), 404
        root = (DATA_DIR / session["user_id"]).resolve()
        check = verify_persisted_reference(root, **dict(reference))
        if check.status != "verified":
            return jsonify({"error": "Historischer Medienanhang ist nicht mehr verifizierbar"}), 409
        attachment = write_sessions_module._final_media_file(session["user_id"], reference["media_path"])
        if attachment is None:
            return jsonify({"error": "Medium nicht gefunden"}), 404
        response = write_sessions_module.send_file(
            attachment, mimetype=write_sessions_module._final_media_mimetype(attachment), conditional=True)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    finally:
        engine.dispose()


@app.route("/api/journals/<journal_date>/history/<revision_id>/restore", methods=["POST"])
@require_auth
@csrf_protect
def api_restore_managed_journal_revision(journal_date, revision_id):
    """Explicitly materialize one owned historical revision through DocumentStore."""
    loaded = _owned_managed_journal(journal_date)
    if loaded is None:
        return jsonify({"error": "Verwaltetes Journal nicht gefunden"}), 404
    try:
        revision_uuid = uuid.UUID(revision_id)
    except ValueError:
        return jsonify({"error": "Ungültige Revision"}), 404
    engine, document, path = loaded
    try:
        from document_store import DocumentConflict, configured_document_store
        from sqlalchemy import text
        with engine.connect() as connection:
            content = connection.scalar(text("""
                SELECT snapshot.markdown_content FROM document_snapshots snapshot
                JOIN document_revisions revision ON revision.id = snapshot.document_revision_id
                WHERE snapshot.document_revision_id = :revision AND revision.document_id = :document
            """), {"revision": revision_uuid, "document": document["id"]})
            if content is None:
                blocks = connection.execute(text("""
                    SELECT block_revision.markdown_content FROM document_revision_blocks manifest
                    JOIN block_revisions block_revision ON block_revision.id = manifest.block_revision_id
                    JOIN document_revisions revision ON revision.id = manifest.document_revision_id
                    WHERE manifest.document_revision_id = :revision AND revision.document_id = :document
                    ORDER BY manifest.position
                """), {"revision": revision_uuid, "document": document["id"]}).scalars().all()
                content = "".join(blocks) if blocks else None
        if content is None:
            return jsonify({"error": "Revision nicht gefunden oder nicht rekonstruierbar"}), 404
        try:
            revision = configured_document_store(engine).write(
                document_id=document["id"], storage_root_key="dev-user-" + session["user_id"],
                root=(DATA_DIR / session["user_id"]).resolve(), content=content,
                idempotency_key=str(uuid.uuid4()), summary="Wiederherstellung einer Revision",
            )
        except DocumentConflict:
            return jsonify({"error": "Journal wurde parallel geändert; bitte neu laden", "conflict": True}), 409
        return jsonify({"ok": True, "revision_id": str(revision)})
    finally:
        engine.dispose()


def _journal_search_tag_paths(user_id, tags):
    """Resolve hashtags to managed journal paths via the rebuildable file index."""
    return sorted({
        reference["path"] for reference in tagging_module.references_for_tags(user_id, tags)
        if reference.get("source") == "personal"
    })


@app.route("/api/journal-search")
@require_auth
def api_managed_journal_search():
    """Search only the current user's confirmed db-managed DEV journals."""
    if not IS_DEV or not is_enabled():
        return jsonify({"error": "Journalsuche ist nicht aktiv"}), 404
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", "30"))
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return jsonify({"error": "Ungültiges Limit oder Offset"}), 400
    from db_search import parse_date_range, search_personal
    path_from, path_to, range_error = parse_date_range(
        (request.args.get("start_date") or "").strip() or None,
        (request.args.get("end_date") or "").strip() or None,
    )
    if range_error:
        return jsonify({"error": range_error}), 400
    tags = []
    for value in request.args.getlist("tags"):
        tags.extend(tag.strip() for tag in value.split(",") if tag.strip())
    paths = _journal_search_tag_paths(session["user_id"], tags) if tags else None
    try:
        from sqlalchemy import create_engine
        engine = create_engine(database_url(), pool_pre_ping=True, pool_size=2, max_overflow=0)
        try:
            with engine.connect() as connection:
                found = search_personal(
                    connection, owner_id=session["user_id"], query=query, limit=limit,
                    offset=offset, path_from=path_from, path_to=path_to, paths=paths,
                    order=request.args.get("order"),
                )
        finally:
            engine.dispose()
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    next_offset = offset + len(found["results"])
    return jsonify({
        "results": [{**dict(row), "revision_id": str(row["revision_id"])} for row in found["results"]],
        "total": found["total"],
        "has_more": next_offset < found["total"],
        "next_offset": next_offset,
    })


# ─── main ────────────────────────────────────────────────────────────


__all__ = ['api_submit', 'api_write_template_preview', 'api_dashboard_init', '_get_today_entries', '_attach_current_managed_media_urls', 'api_dashboard', 'api_dashboard_edit', 'api_dashboard_delete', 'api_journal_conflict', 'api_resolve_journal_conflict', 'api_managed_journal', 'api_managed_journal_history', 'api_managed_journal_revision', 'api_managed_journal_revision_media', 'api_restore_managed_journal_revision', '_journal_search_tag_paths', 'api_managed_journal_search']
