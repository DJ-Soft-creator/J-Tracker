"""Agent-Projekte: folder-scoped agent workspaces with live model discovery.

This module replaces the old ``#ai-…`` hashtag administration for the
„An Agent senden" flow.  A user picks

* a **project** — one existing Linux folder below a host-approved root, plus an
  explicit ``read`` / ``write`` / ``execute`` mode,
* a **connection** — an OpenAI-compatible endpoint (LM Studio, Ollama,
  llama.cpp, generic) whose model list is read live from that endpoint, and
* optional context — the whole current day plus individually picked files.

Only the immutable store, validation and discovery live here.  Job creation is
in ``agent_project_jobs``; the HTTP surface is ``agent_project_routes``.  The
Flask container never spawns an agent: it writes a job file that the host
worker claims (architecture decision D-013).
"""

import ipaddress
import json
import os
import re
import socket
import uuid
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlparse

from scheduling import read_text_file, update_text_file


DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))

STORE_NAME = "agent_projects.json"
KINDS = ("lmstudio", "ollama", "llamacpp", "openai")
RUNNERS = ("opencode", "pi", "claude")
MODES = ("read", "write", "execute")

MAX_CONNECTIONS = 20
MAX_PROJECTS = 40
MAX_CONTEXT_FILES = 20
MAX_CONTEXT_BYTES = 1_000_000
MAX_SINGLE_CONTEXT_BYTES = 200_000
MAX_INSTRUCTION = 20_000
MAX_FOLDER_ENTRIES = 400
MAX_FILE_ENTRIES = 200
DISCOVERY_TIMEOUT = 8
DISCOVERY_MAX_BYTES = 512_000

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_ROOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SESSION_RE = re.compile(r"^[0-9a-f-]{36}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:@/+-]{1,160}$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

# Folders that are never useful as agent context and slow every listing down.
_SKIPPED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
                 ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".cache"}
_CONTEXT_ROOTS = ("notes", "projects", "ai-agent")

_DEFAULT_PROVIDER = {"lmstudio": "lmstudio", "ollama": "ollama",
                     "llamacpp": "llamacpp", "openai": "openai-compatible"}


class AgentProjectError(ValueError):
    """A user-correctable problem with a project, connection or context."""


def new_id():
    return uuid.uuid4().hex[:12]


# ─── host-approved roots ──────────────────────────────────────────────
def _host_roots(key):
    """Read one host-only root allowlist; the host worker stays authoritative."""
    try:
        value = json.loads(read_text_file(DATA_DIR / "host_worker.json"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    entries = value.get(key, []) if isinstance(value, dict) else []
    roots = []
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        root_id, path, label = item.get("id"), item.get("path"), item.get("label")
        candidate = PurePosixPath(path) if isinstance(path, str) else None
        if (isinstance(root_id, str) and _ROOT_ID_RE.fullmatch(root_id)
                and candidate and candidate.is_absolute() and ".." not in candidate.parts
                and candidate.as_posix() == path
                and isinstance(label, str) and 0 < len(label) <= 120):
            roots.append({"id": root_id, "path": path, "label": label})
    return roots


def project_roots():
    """Merge read-only and writable host roots into one picker list."""
    writable = {item["id"]: item for item in _host_roots("external_write_roots")}
    roots = {}
    for item in _host_roots("external_read_roots"):
        roots[item["id"]] = {**item, "writable": False}
    for root_id, item in writable.items():
        roots[root_id] = {**item, "writable": True}
    return sorted(roots.values(), key=lambda item: item["label"].casefold())


def resolve_project_root(root_id, mode):
    """Return the host root a project may use for the requested mode."""
    root = next((item for item in project_roots() if item["id"] == root_id), None)
    if not root:
        raise AgentProjectError("Der Projekt-Ordner liegt nicht unter einer freigegebenen Host-Wurzel.")
    if mode in {"write", "execute"} and not root["writable"]:
        raise AgentProjectError(
            f"„{root['label']}“ ist nur lesend freigegeben — bitte Modus „Lesen“ wählen "
            "oder die Wurzel in host_worker.json als external_write_root freigeben."
        )
    return root


def _safe_subpath(value):
    if value in (None, "", "."):
        return ""
    if not isinstance(value, str) or len(value) > 400 or "\\" in value or "\0" in value:
        raise AgentProjectError("Der Unterordner ist ungültig.")
    candidate = PurePosixPath(value.strip("/"))
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise AgentProjectError("Der Unterordner ist ungültig.")
    return candidate.as_posix()


def resolve_project_dir(root_id, subpath, mode):
    """Resolve one concrete, symlink-free project directory inside a host root.

    Every path component is checked individually: a later symlink swap must not
    silently turn a stored project into an escape from its approved root.
    """
    root = resolve_project_root(root_id, mode)
    relative = _safe_subpath(subpath)
    base = Path(root["path"])
    candidate = base / relative if relative else base
    try:
        current = Path(base.anchor)
        for part in candidate.parts[1:]:
            current /= part
            if current.is_symlink():
                raise AgentProjectError("Der Projekt-Ordner enthält einen Symlink und wird abgelehnt.")
        resolved = candidate.resolve(strict=True)
        base_resolved = base.resolve(strict=True)
        if not resolved.is_dir() or (resolved != base_resolved and base_resolved not in resolved.parents):
            raise AgentProjectError("Der Projekt-Ordner liegt außerhalb der freigegebenen Host-Wurzel.")
    except (OSError, RuntimeError) as exc:
        raise AgentProjectError("Der Projekt-Ordner ist nicht verfügbar.") from exc
    return {"root": root, "subpath": relative, "path": resolved}


def list_folders(root_id, subpath):
    """List immediate, non-symlinked subdirectories for the folder picker."""
    resolved = resolve_project_dir(root_id, subpath, "read")
    entries = []
    try:
        for item in sorted(resolved["path"].iterdir(), key=lambda value: value.name.casefold()):
            if len(entries) >= MAX_FOLDER_ENTRIES:
                break
            if item.name.startswith(".") or item.name in _SKIPPED_DIRS or item.is_symlink():
                continue
            if not item.is_dir():
                continue
            child = f"{resolved['subpath']}/{item.name}" if resolved["subpath"] else item.name
            entries.append({"name": item.name, "subpath": child})
    except OSError as exc:
        raise AgentProjectError("Der Projekt-Ordner konnte nicht gelesen werden.") from exc
    parent = ""
    if resolved["subpath"]:
        parent = PurePosixPath(resolved["subpath"]).parent.as_posix()
        parent = "" if parent == "." else parent
    return {
        "root_id": resolved["root"]["id"], "root_label": resolved["root"]["label"],
        "writable": resolved["root"]["writable"], "subpath": resolved["subpath"],
        "parent": parent, "at_root": not resolved["subpath"], "folders": entries,
    }


# ─── connection endpoints ─────────────────────────────────────────────
def _is_local_address(host):
    """Allow only loopback and private networks for user-supplied endpoints.

    The container would otherwise become an open SSRF proxy for whoever can log
    in.  LAN LLM servers are exactly the intended use case, so a private-range
    allowlist is both sufficient and honest about the boundary.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return False
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            return False
        if address.is_link_local or address.is_multicast or address.is_reserved:
            return False
        if not (address.is_loopback or address.is_private):
            return False
    return True


def validated_base_url(value):
    """Normalise one endpoint URL to ``scheme://host[:port]`` or fail loudly."""
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise AgentProjectError("Bitte eine Basis-URL angeben (z. B. http://192.168.1.50:1234).")
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AgentProjectError("Die Basis-URL muss mit http:// oder https:// beginnen.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AgentProjectError("Die Basis-URL darf keine Zugangsdaten oder Parameter enthalten.")
    if parsed.path.rstrip("/") not in {"", "/v1"}:
        raise AgentProjectError("Die Basis-URL darf nur den Host (optional mit /v1) enthalten.")
    if not _is_local_address(parsed.hostname):
        raise AgentProjectError(
            "Es sind nur Endpunkte im lokalen Netz erlaubt (Loopback oder private IP-Bereiche)."
        )
    port = f":{parsed.port}" if parsed.port else ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}{port}"


def _fetch_json(url):
    request = urlrequest.Request(url, method="GET", headers={"Accept": "application/json"})
    # Bypass any proxy: these endpoints are explicitly local network targets.
    opener = urlrequest.build_opener(urlrequest.ProxyHandler({}))
    try:
        with opener.open(request, timeout=DISCOVERY_TIMEOUT) as response:
            raw = response.read(DISCOVERY_MAX_BYTES + 1)
    except (urlerror.URLError, OSError, ValueError) as exc:
        raise AgentProjectError(f"Der Endpunkt ist nicht erreichbar: {url}") from exc
    if len(raw) > DISCOVERY_MAX_BYTES:
        raise AgentProjectError("Die Modell-Liste des Endpunkts ist unerwartet groß.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentProjectError("Der Endpunkt hat keine gültige Modell-Liste geliefert.") from exc


def valid_model_name(value):
    """One rule for what a model identifier may look like, shared with the chat."""
    name = str(value or "").strip()
    return bool(name and _MODEL_RE.fullmatch(name))


def discover_models(kind, base_url):
    """Read the live model list from one endpoint; never cache it silently."""
    if kind not in KINDS:
        raise AgentProjectError("Unbekannte Verbindungsart.")
    base = validated_base_url(base_url)
    if kind == "ollama":
        payload = _fetch_json(f"{base}/api/tags")
        raw = payload.get("models") if isinstance(payload, dict) else None
        names = [item.get("model") or item.get("name") for item in raw or [] if isinstance(item, dict)]
    else:
        payload = _fetch_json(f"{base}/v1/models")
        raw = payload.get("data") if isinstance(payload, dict) else None
        names = [item.get("id") for item in raw or [] if isinstance(item, dict)]
    models = []
    for name in names:
        if isinstance(name, str) and _MODEL_RE.fullmatch(name) and name not in models:
            models.append(name)
    if not models:
        raise AgentProjectError("Der Endpunkt hat kein nutzbares Modell gemeldet.")
    return sorted(models, key=str.casefold)[:200]


# ─── store ────────────────────────────────────────────────────────────
def _store_path(user_id):
    return DATA_DIR / user_id / STORE_NAME


def _text(value, limit, *, field):
    text = str(value or "").strip()
    if not text or len(text) > limit or "\n" in text or "\r" in text:
        raise AgentProjectError(f"{field} ist ungültig oder zu lang.")
    return text


def normalise_connection(raw, *, existing=None):
    if not isinstance(raw, dict):
        raise AgentProjectError("Verbindung ist ungültig.")
    kind = str(raw.get("kind") or "").strip()
    if kind not in KINDS:
        raise AgentProjectError("Bitte LM Studio, Ollama, llama.cpp oder OpenAI-kompatibel wählen.")
    runner = str(raw.get("runner") or "opencode").strip()
    if runner not in RUNNERS:
        raise AgentProjectError("Als Agent sind nur opencode oder pi erlaubt.")
    provider_id = str(raw.get("provider_id") or _DEFAULT_PROVIDER[kind]).strip()
    if not _PROVIDER_RE.fullmatch(provider_id):
        raise AgentProjectError("Die Provider-Kennung darf nur a-z, 0-9, - und _ enthalten.")
    api_key_env = str(raw.get("api_key_env") or "").strip()
    if api_key_env and not _ENV_RE.fullmatch(api_key_env):
        raise AgentProjectError("Der API-Key-Umgebungsname muss GROSSBUCHSTABEN_MIT_UNTERSTRICH sein.")
    return {
        "id": (existing or {}).get("id") or new_id(),
        "label": _text(raw.get("label"), 80, field="Der Name der Verbindung"),
        "kind": kind,
        "base_url": validated_base_url(raw.get("base_url")),
        "runner": runner,
        "provider_id": provider_id,
        "api_key_env": api_key_env,
    }


def _normalise_context_files(raw):
    files = []
    for item in raw or []:
        if not isinstance(item, str):
            continue
        value = item.strip().lstrip("/")
        if not value or len(value) > 300 or "\\" in value or value in files:
            continue
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() != value:
            continue
        files.append(value)
    return files[:MAX_CONTEXT_FILES]


def normalise_project(raw, connections, *, existing=None):
    if not isinstance(raw, dict):
        raise AgentProjectError("Projekt ist ungültig.")
    mode = str(raw.get("mode") or "read").strip()
    if mode not in MODES:
        raise AgentProjectError("Als Modus sind nur Lesen, Schreiben oder Ausführen erlaubt.")
    connection_id = str(raw.get("connection_id") or "").strip()
    if not _ID_RE.fullmatch(connection_id):
        raise AgentProjectError("Bitte eine Verbindung wählen.")
    connection = next((item for item in connections if item["id"] == connection_id), None)
    if not connection:
        raise AgentProjectError("Die gewählte Verbindung existiert nicht mehr.")
    model = str(raw.get("model") or "").strip()
    if not valid_model_name(model):
        raise AgentProjectError("Bitte ein Modell der Verbindung wählen.")
    root_id = str(raw.get("root_id") or "").strip()
    if not _ROOT_ID_RE.fullmatch(root_id):
        raise AgentProjectError("Bitte einen Projekt-Ordner wählen.")
    resolved = resolve_project_dir(root_id, raw.get("subpath"), mode)
    session_id = str(raw.get("session_id") or "").strip()
    if session_id and not _SESSION_RE.fullmatch(session_id):
        raise AgentProjectError("Die Session-Kennung ist ungültig.")
    journal_date = str(raw.get("journal_date") or "").strip()
    if journal_date and not _DATE_RE.fullmatch(journal_date):
        raise AgentProjectError("Das Journaldatum ist ungültig.")
    previous = existing or {}
    label = str(raw.get("label") or "").strip()
    if not label:
        label = resolved["subpath"].rsplit("/", 1)[-1] or resolved["root"]["label"]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "id": previous.get("id") or new_id(),
        "label": _text(label, 80, field="Der Projektname"),
        "root_id": resolved["root"]["id"],
        "subpath": resolved["subpath"],
        "mode": mode,
        "connection_id": connection_id,
        "model": model,
        "include_today": bool(raw.get("include_today")),
        "journal_date": journal_date,
        "context_files": _normalise_context_files(raw.get("context_files")),
        "instruction": str(raw.get("instruction") or "")[:MAX_INSTRUCTION],
        "session_id": session_id or previous.get("session_id") or "",
        "session_path": str(previous.get("session_path") or "")[:400],
        "last_job_id": str(previous.get("last_job_id") or "")[:36],
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
    }


def _sanitize_store(value):
    if not isinstance(value, dict):
        return {"schema": 1, "connections": [], "projects": []}
    connections = []
    for item in value.get("connections") or []:
        try:
            candidate = normalise_connection(item, existing=item if isinstance(item, dict) else None)
        except AgentProjectError:
            continue
        if _ID_RE.fullmatch(str((item or {}).get("id") or "")):
            candidate["id"] = item["id"]
        connections.append(candidate)
    connections = connections[:MAX_CONNECTIONS]
    projects = []
    for item in value.get("projects") or []:
        if not isinstance(item, dict) or not _ID_RE.fullmatch(str(item.get("id") or "")):
            continue
        # A stored project is kept even when its root or endpoint is gone: the
        # UI must be able to show and repair it instead of losing the history.
        projects.append({
            "id": item["id"],
            "label": str(item.get("label") or "")[:80],
            "root_id": str(item.get("root_id") or "")[:80],
            "subpath": str(item.get("subpath") or "")[:400],
            "mode": item["mode"] if item.get("mode") in MODES else "read",
            "connection_id": str(item.get("connection_id") or "")[:12],
            "model": str(item.get("model") or "")[:160],
            "include_today": bool(item.get("include_today")),
            "journal_date": str(item.get("journal_date") or "")[:10],
            "context_files": _normalise_context_files(item.get("context_files")),
            "instruction": str(item.get("instruction") or "")[:MAX_INSTRUCTION],
            "session_id": str(item.get("session_id") or "")[:36],
            "session_path": str(item.get("session_path") or "")[:400],
            "last_job_id": str(item.get("last_job_id") or "")[:36],
            "created_at": str(item.get("created_at") or "")[:40],
            "updated_at": str(item.get("updated_at") or "")[:40],
        })
    return {"schema": 1, "connections": connections, "projects": projects[:MAX_PROJECTS]}


def load_store(user_id):
    path = _store_path(user_id)
    if not path.exists():
        return {"schema": 1, "connections": [], "projects": []}
    try:
        return _sanitize_store(json.loads(read_text_file(path)))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "connections": [], "projects": []}


def mutate_store(user_id, mutator):
    """Run one locked read-modify-write transaction over the project store."""
    result = {}

    def update(current):
        try:
            store = _sanitize_store(json.loads(current)) if current.strip() else None
        except json.JSONDecodeError:
            store = None
        if store is None:
            store = {"schema": 1, "connections": [], "projects": []}
        outcome = mutator(store)
        result["store"] = store
        result["outcome"] = outcome
        return json.dumps(store, ensure_ascii=False, indent=2) + "\n"

    update_text_file(_store_path(user_id), update)
    return result["store"], result.get("outcome")


def find_project(store, project_id):
    project = next((item for item in store["projects"] if item["id"] == project_id), None)
    if not project:
        raise AgentProjectError("Das Projekt existiert nicht mehr.")
    return project


def find_connection(store, connection_id):
    connection = next((item for item in store["connections"] if item["id"] == connection_id), None)
    if not connection:
        raise AgentProjectError("Die Verbindung des Projekts existiert nicht mehr.")
    return connection


# ─── filesystem date picker ───────────────────────────────────────────
def _journal_relative(day):
    return f"{day:%Y}/{day:%m}/{day:%d}/Journal_{day:%Y-%m-%d}.md"


def files_for_date(user_id, iso_date):
    """List the user's own Markdown files that belong to one calendar day.

    This is the „Datepicker für das Filesystem": the day's journal plus every
    note, project or agent session last changed on that day.  Nothing outside
    the caller's own data directory is ever offered.
    """
    if not _DATE_RE.fullmatch(str(iso_date or "")):
        raise AgentProjectError("Ungültiges Datum.")
    try:
        day = date.fromisoformat(iso_date)
    except ValueError as exc:
        raise AgentProjectError("Ungültiges Datum.") from exc
    root = (DATA_DIR / user_id).resolve()
    entries = []
    seen = set()

    def add(path, kind):
        if len(entries) >= MAX_FILE_ENTRIES:
            return
        try:
            if path.is_symlink() or not path.is_file() or path.suffix.casefold() != ".md":
                return
            relative = path.resolve(strict=True).relative_to(root).as_posix()
            size = path.stat().st_size
        except (OSError, ValueError):
            return
        if relative in seen:
            return
        seen.add(relative)
        entries.append({"path": relative, "kind": kind, "bytes": size,
                        "label": relative.rsplit("/", 1)[-1],
                        "oversized": size > MAX_SINGLE_CONTEXT_BYTES})

    add(root / _journal_relative(day), "journal")
    for name in _CONTEXT_ROOTS:
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            if len(entries) >= MAX_FILE_ENTRIES:
                break
            try:
                changed = datetime.fromtimestamp(path.stat().st_mtime).date()
            except OSError:
                continue
            if changed == day:
                add(path, name)
    entries.sort(key=lambda item: (item["kind"] != "journal", item["path"].casefold()))
    return {"date": iso_date, "files": entries, "truncated": len(entries) >= MAX_FILE_ENTRIES}


def read_context_files(user_id, relatives):
    """Freeze the explicitly picked files into immutable job inputs.

    Unlike the tolerant store normalisation this refuses an unusable entry loudly:
    a job must never run with silently fewer context files than the user chose.
    """
    root = (DATA_DIR / user_id).resolve()
    accepted = _normalise_context_files(relatives)
    for raw in relatives or []:
        if isinstance(raw, str) and raw.strip() and raw.strip().lstrip("/") not in accepted:
            raise AgentProjectError(f"Kontextdatei ist kein erlaubter Pfad: {raw}")
    snapshots = []
    total = 0
    for relative in accepted:
        candidate = root / relative
        try:
            if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.casefold() != ".md":
                raise AgentProjectError(f"Kontextdatei ist nicht verfügbar: {relative}")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            content = read_text_file(resolved)
        except (OSError, ValueError) as exc:
            raise AgentProjectError(f"Kontextdatei ist nicht verfügbar: {relative}") from exc
        size = len(content.encode("utf-8"))
        if size > MAX_SINGLE_CONTEXT_BYTES:
            raise AgentProjectError(f"Kontextdatei ist zu groß: {relative}")
        total += size
        if total > MAX_CONTEXT_BYTES:
            raise AgentProjectError("Der gewählte Datei-Kontext ist insgesamt zu groß.")
        snapshots.append({"path": relative, "content": content})
    return snapshots


__all__ = [
    "AgentProjectError", "DATA_DIR", "KINDS", "MAX_INSTRUCTION", "MODES", "RUNNERS",
    "discover_models", "files_for_date", "find_connection", "find_project",
    "list_folders", "load_store", "mutate_store", "new_id", "normalise_connection",
    "normalise_project", "project_roots", "read_context_files", "resolve_project_dir",
    "validated_base_url",
]
