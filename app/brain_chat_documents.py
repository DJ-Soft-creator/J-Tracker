"""„Chat with your Notes": lesende Werkzeuge fuer Projekte und Notes.

AI maintenance contract: dieses Modul liest Projekt- und Notes-Dateien fuer die
Werkzeugschleife in brain_chat.py — und schreibt nie. Es besitzt keine eigene
Sichtbarkeitsregel: Familiendateien laufen durch dieselbe fail-closed-Pruefung
wie die Brain-Ansicht (``brain._family_visibility``), persoenliche Dateien kommen
nur aus dem eigenen Benutzerordner. Projektzeilen parst ``family_markdown``.

Ob ein Werkzeug ueberhaupt angeboten wird, entscheiden die Schalter in den
Chat-Einstellungen (brain_chat.py), nicht dieses Modul.

Die Ordner werden extern synchronisiert. ``*.lock`` und
``*.sync-conflict-*.md`` sind Artefakte dieser Synchronisation und werden
weder gelistet noch gelesen.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import PurePosixPath

import brain as brain_module
import family_markdown
from mcp_tools import ToolInputError

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before brain_chat_documents is imported")

DIRECTORIES = {"project": "projects", "note": "notes"}
SOURCE_LABELS = {"personal": "Privat", "family": "Familie"}
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 300
MAX_DOCUMENT_CHARS = 12000
MAX_TASKS = 300
MAX_COMMENTS = 100
EXCERPT_CHARS = 200

_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---(?:\n|$)", re.DOTALL)
_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[(?P<state>[ xX])\]\s*(?P<text>.*)$")


def is_sync_artifact(name):
    """Sperr- und Konfliktdateien der externen Synchronisation."""
    lowered = str(name).lower()
    return lowered.endswith(".lock") or ".sync-conflict-" in lowered


# ─── Dateien finden ──────────────────────────────────────────────────
def _roots(user_id):
    return {"personal": brain_module._user_root(user_id), "family": brain_module.FAMILY_DIR}


def _modified_at(stat):
    return datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()


def _visible(source, path, relative, user_id, content):
    if source == "personal":
        return True
    return bool(brain_module._family_visibility(path, relative, user_id, content)[0])


def _documents(user_id, kind):
    """Yield every readable, visible file of one kind: privat zuerst, dann Familie."""
    directory = DIRECTORIES[kind]
    for source, root in _roots(user_id).items():
        for path, inner, _stat in brain_module._iter_markdown_entries(root / directory):
            if is_sync_artifact(path.name):
                continue
            relative = "{}/{}".format(directory, inner)
            content, stat = brain_module._read_source_snapshot(path)
            if content is None or stat is None:
                continue
            if not _visible(source, path, relative, user_id, content):
                continue
            yield source, path, relative, content, stat


def _resolve(user_id, kind, document_id):
    """Map a model-supplied id onto one visible file, or refuse.

    Missing and invisible files fail with the same message, so the answer never
    tells the model that a hidden family file exists.
    """
    label = "Projekt" if kind == "project" else "Notiz"
    if not isinstance(document_id, str) or ":" not in document_id or len(document_id) > 260:
        raise ToolInputError("id muss eine id aus der Liste sein (z. B. 'family:{}/…')".format(DIRECTORIES[kind]))
    source, relative = document_id.split(":", 1)
    missing = ToolInputError("{} nicht gefunden: {}".format(label, document_id))
    roots = _roots(user_id)
    if source not in roots or "\\" in relative:
        raise missing
    parts = PurePosixPath(relative).parts
    if (
        len(parts) < 2 or parts[0] != DIRECTORIES[kind] or not relative.endswith(".md")
        or any(part in {"", ".", ".."} or part.startswith(".") for part in parts)
        or is_sync_artifact(parts[-1]) or brain_module._is_technical_relative(relative)
    ):
        raise missing
    root = roots[source]
    path = root / relative
    if path.is_symlink() or brain_module._relative_path(root, path) != relative or not path.is_file():
        raise missing
    content, stat = brain_module._read_source_snapshot(path)
    if content is None or stat is None or not _visible(source, path, relative, user_id, content):
        raise missing
    return source, path, relative, content, stat


# ─── Inhalte aufbereiten ─────────────────────────────────────────────
def _without_frontmatter(content):
    normalized = (content or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return _FRONTMATTER_RE.sub("", normalized, count=1)


def _heading(text):
    match = _HEADING_RE.search(text or "")
    return match.group(1).strip() if match else ""


def _excerpt(text):
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or _CHECKBOX_RE.match(stripped):
            continue
        lines.append(stripped)
        if sum(len(item) for item in lines) > EXCERPT_CHARS:
            break
    joined = " ".join(lines)
    return joined if len(joined) <= EXCERPT_CHARS else joined[:EXCERPT_CHARS].rstrip() + " …"


def _capped(text):
    text = (text or "").strip()
    return text[:MAX_DOCUMENT_CHARS], len(text) > MAX_DOCUMENT_CHARS


def _user_name(user_id):
    if not user_id:
        return ""
    user = _core.get_user_by_id(user_id)
    return (user or {}).get("username") or str(user_id)[:8]


def _structured_project(source, path, content):
    """A family project file in the machine format, or None for free Markdown."""
    if source != "family":
        return None
    project = family_markdown.parse_project_content(content, path.name)
    if not project.get("_frontmatter_valid") or not project.get("id"):
        return None
    return project


def _checkbox_tasks(text):
    tasks = []
    for line in (text or "").splitlines():
        match = _CHECKBOX_RE.match(line)
        if match and match.group("text").strip():
            tasks.append({"title": match.group("text").strip(), "done": match.group("state") in "xX"})
    return tasks


def _project_parts(source, path, content):
    """Title, free text and tasks of one project, whatever its format."""
    project = _structured_project(source, path, content)
    body = _without_frontmatter(content)
    if project is None:
        return {
            "title": _heading(body) or path.stem, "type": "",
            "text": body, "tasks": _checkbox_tasks(body), "comments": [], "project": None,
        }
    tasks = [
        {
            "title": task["title"],
            "done": bool(task["completed"]),
            "user": _user_name(task.get("user")),
            "target_date": task.get("target_date") or "",
            "group": task.get("gruppe") or "",
        }
        for task in project["tasks"]
    ]
    comments = [
        {"user": _user_name(comment.get("user")), "text": comment["text"],
         "created_at": comment.get("created_at") or ""}
        for comment in project["comments"]
    ]
    text = "\n\n".join(part for part in (project.get("notes"), project.get("notes_after")) if part)
    return {
        "title": (project.get("title") or "").strip() or _heading(body) or path.stem,
        "type": project.get("type") or "", "text": text,
        "tasks": tasks, "comments": comments, "project": project,
    }


def _matches(query, *texts):
    terms = [term for term in brain_module._normalise(query or "").split() if term]
    if not terms:
        return True
    haystack = brain_module._normalise("\n".join(texts))
    return all(term in haystack for term in terms)


def _bounded(limit):
    if limit is None:
        return DEFAULT_LIST_LIMIT
    try:
        return max(1, min(int(limit), MAX_LIST_LIMIT))
    except (TypeError, ValueError):
        raise ToolInputError("limit muss eine ganze Zahl sein") from None


def _head(source, relative, title, stat):
    return {
        "id": "{}:{}".format(source, relative),
        "source": SOURCE_LABELS[source],
        "path": relative,
        "title": title,
        "modified_at": _modified_at(stat),
    }


def _listing(field, items, limit):
    items.sort(key=lambda item: item["modified_at"], reverse=True)
    bounded = _bounded(limit)
    return {field: items[:bounded], "count": min(len(items), bounded),
            "total": len(items), "truncated": len(items) > bounded}


# ─── Runner ──────────────────────────────────────────────────────────
def run_list_projects(user_id, *, query=None, limit=None):
    """Eigene und sichtbare Familienprojekte mit Aufgabenstand und kurzem Auszug."""
    items = []
    for source, path, relative, content, stat in _documents(user_id, "project"):
        parts = _project_parts(source, path, content)
        if not _matches(query, parts["title"], relative, content):
            continue
        done = sum(1 for task in parts["tasks"] if task["done"])
        items.append({
            **_head(source, relative, parts["title"], stat),
            "type": parts["type"],
            "tasks_open": len(parts["tasks"]) - done,
            "tasks_done": done,
            "excerpt": _excerpt(parts["text"]),
        })
    return _listing("projects", items, limit)


def run_get_project(user_id, *, id):
    """Ein Projekt vollstaendig: Freitext, Aufgaben mit Status, Kommentare."""
    source, path, relative, content, stat = _resolve(user_id, "project", id)
    parts = _project_parts(source, path, content)
    text, truncated = _capped(parts["text"])
    tasks = parts["tasks"][:MAX_TASKS]
    comments = parts["comments"][:MAX_COMMENTS]
    result = {
        **_head(source, relative, parts["title"], stat),
        "type": parts["type"],
        "text": text,
        "tasks": tasks,
        "comments": comments,
        "truncated": truncated or len(parts["tasks"]) > MAX_TASKS or len(parts["comments"]) > MAX_COMMENTS,
    }
    project = parts["project"]
    if project:
        result["assigned_users"] = [_user_name(user) for user in project.get("assigned_users") or []]
    return result


def run_list_notes(user_id, *, query=None, limit=None):
    """Eigene und sichtbare Familiennotizen mit kurzem Auszug."""
    items = []
    for source, path, relative, content, stat in _documents(user_id, "note"):
        body = _without_frontmatter(content)
        title = _heading(body) or path.stem
        if not _matches(query, title, relative, body):
            continue
        items.append({**_head(source, relative, title, stat), "excerpt": _excerpt(body)})
    return _listing("notes", items, limit)


def run_get_note(user_id, *, id):
    """Eine Notiz im Volltext, gedeckelt auf MAX_DOCUMENT_CHARS."""
    source, path, relative, content, stat = _resolve(user_id, "note", id)
    body = _without_frontmatter(content)
    text, truncated = _capped(body)
    return {**_head(source, relative, _heading(body) or path.stem, stat), "text": text, "truncated": truncated}


# ─── Werkzeugbeschreibungen ──────────────────────────────────────────
_QUERY_SCHEMA = {"type": "string", "description": "Optionale Suchbegriffe; alle muessen in Titel oder Text vorkommen."}
_LIMIT_SCHEMA = {"type": "integer", "minimum": 1, "maximum": MAX_LIST_LIMIT,
                 "description": "Hoechstzahl der Eintraege (neueste zuerst)."}

TOOLS = {
    "project": (
        {
            "name": "list_projects",
            "description": (
                "Listet die Projekte des Nutzers: eigene (Quelle 'Privat') und fuer ihn sichtbare "
                "Familienprojekte (Quelle 'Familie'), neueste zuerst, mit offenen/erledigten "
                "Aufgaben und kurzem Auszug. Die 'id' eines Eintrags ist das Argument fuer get_project."
            ),
            "inputSchema": {"type": "object", "properties": {"query": _QUERY_SCHEMA, "limit": _LIMIT_SCHEMA}},
        },
        {
            "name": "get_project",
            "description": (
                "Liest ein Projekt vollstaendig: Freitext, Aufgaben mit Status (done), Zustaendigem "
                "und Zieldatum sowie Kommentare."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "id aus list_projects"}},
                "required": ["id"],
            },
        },
    ),
    "note": (
        {
            "name": "list_notes",
            "description": (
                "Listet die Notes des Nutzers: eigene (Quelle 'Privat') und fuer ihn sichtbare "
                "Familiennotizen (Quelle 'Familie'), neueste zuerst, mit kurzem Auszug. Die 'id' "
                "eines Eintrags ist das Argument fuer get_note."
            ),
            "inputSchema": {"type": "object", "properties": {"query": _QUERY_SCHEMA, "limit": _LIMIT_SCHEMA}},
        },
        {
            "name": "get_note",
            "description": "Liest eine Notiz im Volltext.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "id aus list_notes"}},
                "required": ["id"],
            },
        },
    ),
}

RUNNERS = {
    "list_projects": run_list_projects,
    "get_project": run_get_project,
    "list_notes": run_list_notes,
    "get_note": run_get_note,
}

# Welcher Schalter welches Werkzeug freigibt.
KIND_OF_TOOL = {tool["name"]: kind for kind, tools in TOOLS.items() for tool in tools}


__all__ = [
    "DIRECTORIES", "KIND_OF_TOOL", "MAX_DOCUMENT_CHARS", "RUNNERS", "TOOLS", "is_sync_artifact",
    "run_get_note", "run_get_project", "run_list_notes", "run_list_projects",
]
