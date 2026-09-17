"""Immutable host jobs and durable sessions for Agent-Projekte.

One project turn produces exactly one job file below
``DATA_DIR/<user>/ai_jobs/`` plus one append-only Markdown session below
``DATA_DIR/<user>/ai-agent/``.  The session file is the durable transcript: it
is what Brain lists and what „Session fortsetzen" reopens, and it carries the
runner's own session id so ``opencode run --session`` / ``pi --session-id`` /
``claude --resume`` can
continue the same conversation instead of replaying a history blob.

The Flask process only writes these files.  Spawning ``opencode``/``pi`` is the
host worker's job (architecture decision D-013).
"""

import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone

import agent_projects
from agent_projects import AgentProjectError, DATA_DIR
from scheduling import read_text_file, update_text_file, write_text_file


_CONFIG_RE = re.compile(r"<!-- jt:agent-session-config\s*(.*?)\s*-->", re.DOTALL)
_ANSWER_RE = re.compile(
    r"<!-- jt:agent-session\s+(\{.*?\})\s*-->\s*\n(.*?)(?=\n___\s*(?:\n|$)|\Z)", re.DOTALL)
_REQUEST_RE = re.compile(
    r"<!-- jt:agent-session-event\s+(\{.*?\})\s*-->\s*\n(.*?)(?=\n___\s*(?:\n|$)|\Z)", re.DOTALL)
_SESSION_RE = re.compile(r"^[0-9a-f-]{36}$")

_MODE_RULES = {
    "read": (
        "Du arbeitest ausschließlich LESEND im Projektordner. Du darfst Dateien lesen, "
        "suchen und analysieren. Du darfst nichts anlegen, ändern, löschen oder ausführen."
    ),
    "write": (
        "Du arbeitest LESEND und SCHREIBEND im Projektordner. Du darfst Dateien lesen, "
        "anlegen und ändern. Du darfst keine Shell-Befehle ausführen. Ändere nur, was der "
        "Auftrag verlangt, und beschreibe am Ende jede geänderte Datei."
    ),
    "execute": (
        "Du arbeitest LESEND, SCHREIBEND und AUSFÜHREND im Projektordner. Du darfst Dateien "
        "lesen, anlegen, ändern und Befehle ausführen. Führe nur Befehle aus, die der Auftrag "
        "erfordert, und beschreibe am Ende jede Änderung und jeden ausgeführten Befehl."
    ),
}
_MODE_LABELS = {"read": "Lesen", "write": "Schreiben", "execute": "Ausführen"}


def _main():
    module = sys.modules.get("main") or sys.modules.get("__main__")
    if module is None:  # pragma: no cover - import order is an application invariant
        raise RuntimeError("main facade must exist before agent project jobs are used")
    return module


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def project_tag(label):
    """Derive one stable hashtag-shaped identity so Brain can group sessions."""
    slug = re.sub(r"[^\w-]+", "-", str(label or "").casefold(), flags=re.UNICODE).strip("-")
    return f"projekt-{slug[:60]}" if slug else "projekt"


def _session_config(content):
    match = _CONFIG_RE.search(content or "")
    if not match:
        return None
    try:
        config = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return config if isinstance(config, dict) else None


def session_path_for(user_id, relative):
    """Resolve one stored session path back into the user's own ai-agent folder."""
    if not isinstance(relative, str) or not relative:
        return None
    user_root = (DATA_DIR / user_id).resolve()
    try:
        path = (user_root / relative).resolve(strict=True)
        posix = path.relative_to(user_root).as_posix()
    except (OSError, ValueError):
        return None
    if not posix.startswith("ai-agent/") or path.suffix.casefold() != ".md" or not path.is_file():
        return None
    return path


def runner_session_for(user_id, project):
    """Read the runner's own session id from the durable session document."""
    path = session_path_for(user_id, project.get("session_path"))
    if not path:
        return ""
    try:
        config = _session_config(read_text_file(path)) or {}
    except OSError:
        return ""
    value = str(config.get("runner_session") or "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value) else ""


def _new_session(user_id, username, project, connection, tag):
    directory = DATA_DIR / user_id / "ai-agent"
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone()
    stem = f"Agent-Session_{now.strftime('%Y-%m-%d_%H-%M-%S')}"
    path = directory / f"{stem}.md"
    suffix = 2
    while path.exists():
        path = directory / f"{stem}_{suffix}.md"
        suffix += 1
    session_id = str(uuid.uuid4())
    config = {
        "schema": 1, "session_id": session_id, "status": "active",
        "workflow_tag": tag, "agent": connection["runner"], "model": project["model"],
        "kind": "agent-project", "project_id": project["id"], "project_label": project["label"],
        "project_root_id": project["root_id"], "project_subpath": project["subpath"],
        "project_mode": project["mode"], "connection_id": connection["id"],
        "connection_label": connection["label"], "connection_kind": connection["kind"],
        "actor_id": user_id, "actor_name": username,
        "created_at": now.isoformat(timespec="seconds"),
        "runner_session": "", "source_revision": "",
    }
    content = (
        "---\n"
        f"id: {session_id}\n"
        "type: ai-agent-session\n"
        "status: active\n"
        f"workflow_tag: {tag}\n"
        f"agent: {connection['runner']}\n"
        f"model: {project['model']}\n"
        f"user_id: {json.dumps(user_id, ensure_ascii=False)}\n"
        f"username: {json.dumps(username, ensure_ascii=False)}\n"
        f"created_at: {config['created_at']}\n"
        "---\n\n"
        f"# Agent-Projekt: {project['label']}\n\n"
        f"Ordner: `{project['root_id']}:{project['subpath'] or '.'}` · Modus: "
        f"{_MODE_LABELS[project['mode']]} · Verbindung: {connection['label']} "
        f"({connection['runner']} · {project['model']})\n\n"
        "<!-- jt:agent-session-config\n"
        + json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n-->\n"
    )
    write_text_file(path, content)
    return path, session_id


def _request_event(body, *, user_id, username, job_id):
    at = _now()
    metadata = {"schema": 1, "id": str(uuid.uuid4()), "type": "user-request", "at": at,
                "user_id": user_id, "username": username, "job_id": job_id}
    return (
        "\n\n___\n\n"
        f"## Benutzeranfrage | Datum & Uhrzeit: {at}\n"
        f"<!-- jt:agent-session-event {json.dumps(metadata, ensure_ascii=False, sort_keys=True)} -->\n\n"
        f"{body.strip()}\n\n___\n"
    )


def _workflow_prompt(project, connection, resolved):
    return "\n".join((
        f"Du bist der Journl-Agent für das Projekt „{project['label']}“.",
        f"Projektordner: {resolved['path']}",
        f"Zugriffsmodus: {_MODE_LABELS[project['mode']]}.",
        _MODE_RULES[project["mode"]],
        "",
        "Antworte anschließend ausschließlich mit dem Markdown-Bericht deiner Arbeit. "
        "Keine Präambel, keine Wiederholung dieser Regeln.",
        f"Ausführender Agent: {connection['runner']} · Modell: {project['model']}.",
    ))


def _document_context(user_id, project, snapshots):
    """Assemble the read-only reference context for one turn."""
    parts = []
    if project.get("include_today"):
        journal_date = project.get("journal_date") or datetime.now().astimezone().strftime("%Y-%m-%d")
        day = datetime.strptime(journal_date, "%Y-%m-%d")
        path = _main()._get_journal_path(day, user_id=user_id)
        content = read_text_file(path) if path.exists() else ""
        parts.append(f"## Journal {journal_date}\n" + (content.strip() or "(keine Einträge an diesem Tag)"))
    for snapshot in snapshots:
        parts.append(f"## Datei {snapshot['path']}\n{snapshot['content'].strip()}")
    return "\n\n".join(parts)


def context_label(project, snapshots):
    labels = []
    if project.get("include_today"):
        labels.append(f"Journal {project.get('journal_date') or 'heute'}")
    if snapshots:
        labels.append(f"{len(snapshots)} Datei(en)")
    return " + ".join(labels) or "kein Zusatzkontext"


def queue_project_job(user_id, username, project, connection, instruction):
    """Create one immutable, host-claimable turn for an Agent-Projekt."""
    if not isinstance(username, str) or not username or len(username) > 120 or "\n" in username or "\r" in username:
        raise AgentProjectError("Der angemeldete Benutzername ist für einen Agentenauftrag ungültig.")
    instruction = str(instruction or "").strip()
    if not instruction:
        raise AgentProjectError("Bitte einen Auftrag formulieren.")
    if len(instruction) > agent_projects.MAX_INSTRUCTION:
        raise AgentProjectError("Der Auftrag ist zu lang.")
    resolved = agent_projects.resolve_project_dir(project["root_id"], project["subpath"], project["mode"])
    snapshots = agent_projects.read_context_files(user_id, project.get("context_files"))
    tag = project_tag(project["label"])

    job_id = str(uuid.uuid4())
    root = DATA_DIR / user_id / "ai_jobs"
    root.mkdir(parents=True, exist_ok=True)

    session_path = session_path_for(user_id, project.get("session_path"))
    session_id = str(project.get("session_id") or "")
    if session_path and _SESSION_RE.fullmatch(session_id):
        config = _session_config(read_text_file(session_path)) or {}
        if config.get("session_id") != session_id:
            raise AgentProjectError("Die Session passt nicht mehr zum Projekt.")
        runner_session = str(config.get("runner_session") or "")
    else:
        session_path, session_id = _new_session(user_id, username, project, connection, tag)
        runner_session = ""

    update_text_file(session_path, lambda current: current.rstrip() + _request_event(
        instruction, user_id=user_id, username=username, job_id=job_id))
    source = read_text_file(session_path)
    revision_source = re.sub(r'"source_revision"\s*:\s*"[^"]*"', '"source_revision":""', source)
    expected_revision = hashlib.sha256(revision_source.encode("utf-8")).hexdigest()

    prompt = _workflow_prompt(project, connection, resolved)
    write_text_file(root / f"{job_id}.prompt.md", prompt)
    write_text_file(root / f"{job_id}.request.md", instruction)
    document_context = _document_context(user_id, project, snapshots)
    document_context_path = None
    if document_context:
        write_text_file(root / f"{job_id}.context.md", document_context)
        document_context_path = f"{user_id}/ai_jobs/{job_id}.context.md"
    connection_manifest = {
        "kind": connection["kind"], "base_url": connection["base_url"],
        "provider_id": connection["provider_id"], "model": project["model"],
        "api_key_env": connection["api_key_env"], "runner": connection["runner"],
    }
    write_text_file(root / f"{job_id}.connection.json",
                    json.dumps(connection_manifest, ensure_ascii=False, indent=2) + "\n")

    job = {
        "id": job_id, "session_id": session_id, "status": "queued", "created_at": _now(),
        "user_id": user_id, "username": username, "workflow_tag": tag,
        "agent": connection["runner"], "job_kind": "agent_project",
        "model": f"{connection['provider_id']}/{project['model']}",
        "context_type": "agent_project", "delivery": "agent_project",
        "prompt": prompt, "user_request": instruction, "context": document_context,
        "submitted_text": instruction,
        "project": {
            "id": project["id"], "label": project["label"], "root_id": project["root_id"],
            "subpath": project["subpath"], "mode": project["mode"], "path": str(resolved["path"]),
        },
        "connection": connection_manifest,
        "connection_manifest_path": f"{user_id}/ai_jobs/{job_id}.connection.json",
        "runner_session": runner_session,
        "knowledge_snapshots": [], "knowledge_snapshot_paths": [], "knowledge_manifest_path": None,
        "knowledge_references": [], "reference_manifest_path": None,
        "document_context_path": document_context_path,
        "document_context_label": context_label(project, snapshots)[:120],
        "source_path": session_path.relative_to(DATA_DIR).as_posix(),
        "request_path": f"{user_id}/ai_jobs/{job_id}.request.md",
        "prompt_path": f"{user_id}/ai_jobs/{job_id}.prompt.md",
        "section_path": session_path.relative_to(DATA_DIR).as_posix(),
        "expected_revision": expected_revision,
        "write_target": None, "write_target_manifest_path": None,
        "attachment_images": [], "attachment_context": "",
    }
    write_text_file(root / f"{job_id}.json", json.dumps(job, ensure_ascii=False, indent=2) + "\n")
    return {
        "job": job, "session_id": session_id,
        "session_path": session_path.relative_to(DATA_DIR / user_id).as_posix(),
    }


def session_turns(user_id, project, limit=40):
    """Read the durable transcript of one project session for the answer window."""
    path = session_path_for(user_id, project.get("session_path"))
    if not path:
        return {"turns": [], "session_id": project.get("session_id") or "", "runner_session": ""}
    content = read_text_file(path)
    config = _session_config(content) or {}
    turns = []
    for pattern, kind in ((_REQUEST_RE, "request"), (_ANSWER_RE, "answer")):
        for match in pattern.finditer(content):
            try:
                metadata = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if not isinstance(metadata, dict):
                continue
            turns.append({"kind": kind, "at": str(metadata.get("at") or ""),
                          "job_id": str(metadata.get("job_id") or ""),
                          "body": match.group(2).strip()[:60000], "_at": match.start()})
    # The session document is append-only, so its byte order is the true turn
    # order.  Timestamps come from two different clocks (app and host runner) and
    # must not be allowed to reorder a transcript.
    turns.sort(key=lambda item: item.pop("_at"))
    return {
        "turns": turns[-limit:],
        "session_id": str(config.get("session_id") or project.get("session_id") or ""),
        "runner_session": str(config.get("runner_session") or ""),
        "status": str(config.get("status") or "active"),
    }


def project_for_session(user_id, session_id):
    """Find the stored project that owns one session id (Brain „fortsetzen")."""
    if not _SESSION_RE.fullmatch(str(session_id or "")):
        return None
    store = agent_projects.load_store(user_id)
    for project in store["projects"]:
        if project.get("session_id") == session_id:
            return project
    directory = DATA_DIR / user_id / "ai-agent"
    for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
        try:
            config = _session_config(read_text_file(path)) or {}
        except OSError:
            # A concurrently archived or rotated session must not break the lookup.
            continue
        if config.get("session_id") != session_id:
            continue
        project_id = str(config.get("project_id") or "")
        match = next((item for item in store["projects"] if item["id"] == project_id), None)
        if match:
            return match
    return None


def active_job(user_id, session_id):
    """Return one still-running job of a session, so turns stay serialised."""
    directory = DATA_DIR / user_id / "ai_jobs"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        if not re.fullmatch(r"[0-9a-f-]{36}\.json", path.name):
            continue
        try:
            job = json.loads(read_text_file(path))
        except (OSError, json.JSONDecodeError):
            continue
        if job.get("session_id") == session_id and job.get("status") in {"queued", "running", "cancelling"}:
            return job
    return None


__all__ = ["active_job", "context_label", "project_for_session", "project_tag",
           "queue_project_job", "runner_session_for", "session_path_for", "session_turns"]
