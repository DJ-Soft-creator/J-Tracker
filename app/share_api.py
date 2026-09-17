"""Token-protected Share API with direct and AI-to-journal delivery.

External callers submit exactly one source (text, URL, or file).  A personal
AI workflow tag opts into durable AI processing; without one the source is
stored directly as a new journal entry.  The browser writing-session path is
deliberately not involved.
"""

import hashlib
import hmac
from html.parser import HTMLParser
import ipaddress
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from flask import Blueprint, jsonify, request
from PIL import Image

import ai_attachments
import background_jobs
import tagging
from scheduling import path_lock, read_text_file, write_text_file


share_api_bp = Blueprint("share_api", __name__, url_prefix="/api/share")
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
MAX_URL_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_CHARS = 180_000
MAX_EXTRA_TEXT_CHARS = 10_000
MAX_EXTRACT_BYTES = 24 * 1024 * 1024
TOKEN_SCOPES = frozenset({"upload", "run_workflow", "write_journal", "mcp", "mcp_family"})
MAX_TOKENS_PER_USER = 10
_JOB_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm"}
_AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".ogg", ".webm", ".aac"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_DOCUMENT_SUFFIXES = _TEXT_SUFFIXES | {".pdf", ".docx", ".odt", ".xlsx"}
_ALLOWED_SUFFIXES = _AUDIO_SUFFIXES | _IMAGE_SUFFIXES | _DOCUMENT_SUFFIXES
_worker_lock = threading.Lock()
_worker_thread = None
_worker_wakeup = threading.Event()
logger = logging.getLogger(__name__)


def _main():
    import sys
    return sys.modules.get("main") or sys.modules.get("__main__")


def _now():
    main = _main()
    return main.get_tz_aware_now()[0] if main else datetime.now().astimezone()


def _iso(value):
    return value.isoformat(timespec="seconds")


def _user_root(user_id):
    return _main().DATA_DIR / user_id


def _token_path(user_id):
    return _user_root(user_id) / ".config" / "share-api.json"


def _jobs_root(user_id):
    return _user_root(user_id) / ".share-jobs"


def _job_dir(user_id, job_id):
    if not _JOB_ID_RE.fullmatch(job_id or ""):
        return None
    root = _jobs_root(user_id).resolve()
    candidate = root / job_id
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        return None
    return candidate


def _read_json(path, default=None):
    if not path or not path.is_file() or path.is_symlink():
        return default
    try:
        return json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path, value):
    write_text_file(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _browser_user():
    user = _main()._current_user()
    return (user, None) if user else (None, (jsonify({"error": "Unauthorized"}), 401))


def _csrf_error():
    return _main().csrf_protect(lambda: None)()


def _bearer_token():
    scheme, separator, value = request.headers.get("Authorization", "").partition(" ")
    if not separator or scheme.casefold() != "bearer" or not value.strip() or len(value) > 256:
        return None
    return value.strip()


def _key_records(config):
    """Return the stored API keys, reading a pre-multi-key file as one key.

    Accounts used to hold exactly one key, which forced every purpose — an
    iPhone shortcut, an MCP client — onto the same secret: revoking one revoked
    all of them. Schema 2 keeps a list instead; a schema-1 file is still read
    here so an existing key keeps working until it is replaced.
    """
    if not isinstance(config, dict):
        return []
    keys = config.get("keys")
    if isinstance(keys, list):
        return [
            key for key in keys
            if isinstance(key, dict) and isinstance(key.get("token_sha256"), str)
        ]
    if isinstance(config.get("token_sha256"), str):
        return [{
            "id": "legacy",
            "label": "Bestehender Schlüssel",
            "token_sha256": config["token_sha256"],
            "scopes": config.get("scopes") or [],
            "created_at": config.get("created_at"),
            "last_used_at": config.get("last_used_at"),
        }]
    return []


def _write_keys(path, keys):
    if keys:
        _write_json(path, {"schema": 2, "keys": keys})
    else:
        path.unlink(missing_ok=True)


def _public_key(key):
    return {field: key.get(field) for field in ("id", "label", "scopes", "created_at", "last_used_at")}


def _token_user(required_scopes=()):
    token = _bearer_token()
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    for user in _main()._read_users_file().get("users", []):
        user_id = user.get("id")
        if not user_id:
            continue
        path = _token_path(user_id)
        keys = _key_records(_read_json(path, {}))
        for index, key in enumerate(keys):
            if not hmac.compare_digest(key["token_sha256"], digest):
                continue
            if not set(required_scopes).issubset(set(key.get("scopes") or [])):
                return None
            keys[index] = {**key, "last_used_at": _iso(_now())}
            _write_keys(path, keys)
            return user
    return None


def _public_job(job):
    result = {key: job.get(key) for key in (
        "id", "status", "workflow_tag", "source_type", "title", "entry_at",
        "created_at", "updated_at", "queued_at", "completed_at", "journal_path",
        "agent_job_id", "error",
    )}
    if result["status"] in {"prepared", "queueing"}:
        result["status"] = "preparing"
    return result


@share_api_bp.route("/token", methods=["GET", "POST", "DELETE"])
def share_token():
    """List, add, or revoke one of the account's API keys.

    ``POST`` adds a key instead of replacing the account's only one, so a phone
    shortcut and an MCP client can hold separate secrets with separate rights.
    ``DELETE`` therefore needs the id of the key to revoke.
    """
    user, error = _browser_user()
    if error:
        return error
    path = _token_path(user["id"])
    keys = _key_records(_read_json(path, {}))
    if request.method == "GET":
        response = jsonify({"keys": [_public_key(key) for key in keys], "enabled": bool(keys)})
        response.headers["Cache-Control"] = "no-store"
        return response
    csrf = _csrf_error()
    if csrf:
        return csrf
    payload = request.get_json(silent=True) or {}
    if request.method == "DELETE":
        key_id = str(payload.get("id") or request.args.get("id") or "")
        remaining = [key for key in keys if key.get("id") != key_id]
        if not key_id or len(remaining) == len(keys):
            return jsonify({"error": "Unbekannter Schlüssel"}), 404
        _write_keys(path, remaining)
        return jsonify({"ok": True, "keys": [_public_key(key) for key in remaining]})
    if len(keys) >= MAX_TOKENS_PER_USER:
        return jsonify({"error": "Höchstens {} Schlüssel pro Konto".format(MAX_TOKENS_PER_USER)}), 400
    scopes = payload.get("scopes", sorted(TOKEN_SCOPES))
    if not isinstance(scopes, list) or not scopes or any(scope not in TOKEN_SCOPES for scope in scopes):
        return jsonify({"error": "Ungültige API-Rechte"}), 400
    scopes = sorted(set(scopes))
    token = "jts_" + secrets.token_urlsafe(32)
    now = _iso(_now())
    record = {
        "id": secrets.token_hex(8),
        "label": _clean_single_line(payload.get("label"), 40) or "Schlüssel {}".format(len(keys) + 1),
        "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "scopes": scopes,
        "created_at": now,
        "last_used_at": None,
    }
    _write_keys(path, keys + [record])
    response = jsonify({
        "ok": True, "enabled": True, "token": token,
        "key": _public_key(record),
        "keys": [_public_key(key) for key in keys + [record]],
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 201


def _clean_single_line(value, limit):
    return " ".join(str(value or "").replace("<", "‹").replace(">", "›").split())[:limit]


def _entry_at(value):
    now = _now()
    raw = str(value or "").strip()
    if not raw:
        return now
    if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", raw):
        parsed_time = datetime.strptime(raw, "%H:%M:%S" if raw.count(":") == 2 else "%H:%M").time()
        return now.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=parsed_time.second, microsecond=0)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("entry_at muss HH:MM, HH:MM:SS oder ein ISO-8601-Zeitpunkt sein") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    parsed = parsed.astimezone(now.tzinfo)
    if not 1900 <= parsed.year <= 9999:
        raise ValueError("entry_at liegt außerhalb des unterstützten Journalzeitraums")
    return parsed


def _hashtags(value, defaults=()):
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.findall(r"#?([\w-]+)", str(value or ""), re.UNICODE)
    result = []
    for raw in [*defaults, *raw_items]:
        tag = tagging.normalise_tag(str(raw).lstrip("#"))
        if tag and tag not in result:
            result.append(tag)
    if len(result) > 30:
        raise ValueError("Höchstens 30 Hashtags sind erlaubt")
    return result


def _payload():
    data = request.get_json(silent=True) if request.is_json else request.form
    data = data if isinstance(data, dict) or hasattr(data, "get") else {}
    upload = request.files.get("file")
    source_text = data.get("source_text")
    source_url = data.get("url")
    supplied = [bool(str(source_text or "").strip()), bool(str(source_url or "").strip()), bool(upload)]
    if sum(supplied) != 1:
        raise ValueError("Genau eine Quelle aus source_text, url oder file ist erforderlich")
    return data, upload, source_text, source_url


def _save_upload(upload, directory):
    filename = Path(upload.filename or "Datei").name[:255]
    suffix = Path(filename).suffix.casefold()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError("Nicht unterstützter Dateityp")
    raw = upload.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise ValueError("Die Datei ist leer")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise OverflowError("Die Datei ist größer als 80 MB")
    if suffix in _IMAGE_SUFFIXES:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.verify()
        except Exception as exc:
            raise ValueError("Die Bilddatei ist beschädigt oder nicht unterstützt") from exc
    stored = directory / ("source" + suffix)
    stored.write_bytes(raw)
    os.chmod(stored, 0o660)
    return stored, filename, len(raw), hashlib.sha256(raw).hexdigest()


def _source_mime(suffix):
    return {
        ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".ogg": "audio/ogg", ".webm": "audio/webm", ".aac": "audio/aac",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
        ".csv": "text/csv", ".json": "application/json", ".html": "text/html", ".htm": "text/html",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".odt": "application/vnd.oasis.opendocument.text",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(suffix, "application/octet-stream")


def _storable_url(url):
    """Validate a link which is stored only and never fetched by Journl."""
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Nur vollständige HTTP- oder HTTPS-URLs sind erlaubt")
    if parsed.username or parsed.password:
        raise ValueError("URLs mit Zugangsdaten sind nicht erlaubt")
    return parsed.geturl()


@share_api_bp.route("", methods=["POST"])
def submit_share():
    # ``run_workflow`` grants only the optional AI action.  A plain journal
    # archive needs upload + journal-write rights, but cannot accidentally
    # invoke Pi with such a deliberately reduced key.
    user = _token_user({"upload", "write_journal"})
    if not user:
        response = jsonify({"error": "Ungültiger Share-API-Schlüssel oder fehlende Rechte"})
        response.headers["WWW-Authenticate"] = 'Bearer realm="Journl Share API"'
        return response, 401
    if request.content_length and request.content_length > MAX_UPLOAD_BYTES + (2 * 1024 * 1024):
        return jsonify({"error": "Upload ist zu groß"}), 413
    try:
        data, upload, source_text, source_url = _payload()
        workflow_tag = tagging.normalise_tag(data.get("workflow_tag")) if data.get("workflow_tag") else None
        workflow = None
        if workflow_tag:
            if not _token_user({"run_workflow"}):
                raise ValueError("Der Share-API-Schlüssel darf keine AI-Workflows ausführen")
            workflow = tagging.catalog_view(user["id"]).get("ai", {}).get(workflow_tag)
            if not workflow:
                raise ValueError("Unbekanntes AI-Workflow-Tag")
            if workflow.get("agent") != "pi" or workflow.get("target") != "daily_journal":
                raise ValueError("Der Workflow muss Pi und das Ziel Tagesjournal verwenden")
        when = _entry_at(data.get("entry_at"))
        extra_text = str(data.get("text") or "").strip()
        if len(extra_text) > MAX_EXTRA_TEXT_CHARS:
            raise ValueError("Zusätzlicher Text ist zu lang")
        title = _clean_single_line(data.get("title"), 160)
        tags = _hashtags(data.get("hashtags"), (workflow or {}).get("default_hashtags") or [])
    except OverflowError as exc:
        return jsonify({"error": str(exc)}), 413
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    job_id = str(uuid.uuid4())
    directory = _job_dir(user["id"], job_id)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    now = _iso(_now())
    try:
        source_path = None
        original_filename = None
        byte_size = None
        sha256 = None
        if upload:
            source_path, original_filename, byte_size, sha256 = _save_upload(upload, directory)
            suffix = source_path.suffix.casefold()
            source_type = "audio" if suffix in _AUDIO_SUFFIXES else "image" if suffix in _IMAGE_SUFFIXES else "file"
        elif str(source_url or "").strip():
            source_url = _storable_url(source_url) if not workflow else str(source_url).strip()
            if workflow:
                parsed = urllib.parse.urlsplit(source_url)
                if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("Nur öffentliche HTTP- oder HTTPS-URLs ohne Zugangsdaten sind erlaubt")
            source_type = "url"
        else:
            if len(str(source_text)) > MAX_CONTEXT_CHARS:
                raise ValueError("Der geteilte Text ist zu lang")
            source_type = "text"
        if workflow and source_type not in set(workflow.get("share_inputs") or ["text", "url", "audio", "image", "file"]):
            raise ValueError("Dieser Workflow erlaubt den gewählten Eingabetyp nicht")
        job = {
            "schema": 1, "id": job_id, "user_id": user["id"], "status": "preparing",
            "mode": "ai" if workflow else "direct", "workflow_tag": workflow_tag, "source_type": source_type,
            "source_text": str(source_text).strip() if source_type == "text" else None,
            "source_url": str(source_url).strip() if source_type == "url" else None,
            "source_path": source_path.relative_to(_main().DATA_DIR).as_posix() if source_path else None,
            "original_filename": original_filename,
            "mime_type": _source_mime(source_path.suffix.casefold()) if source_path else None,
            "byte_size": byte_size, "sha256": sha256, "title": title,
            "hashtags": tags, "extra_text": extra_text, "entry_at": _iso(when),
            "created_at": now, "updated_at": now, "error": None,
        }
        _write_json(directory / "metadata.json", job)
    except OverflowError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        return jsonify({"error": str(exc)}), 413
    except ValueError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    if not workflow:
        try:
            _deliver_job(user["id"], directory, job, {"response_text": _direct_entry_content(job)})
        except Exception as exc:
            logger.warning("Direct share %s failed: %s", job_id, type(exc).__name__)
            _update_job(directory, job, status="failed", completed_at=_iso(_now()), error=_public_error(exc))
            response = jsonify({"ok": False, **_public_job(_read_json(directory / "metadata.json", job)), "status_url": f"/api/share/jobs/{job_id}"})
            response.headers["Cache-Control"] = "no-store"
            return response, 500
        response = jsonify({"ok": True, **_public_job(_read_json(directory / "metadata.json", job)), "status_url": f"/api/share/jobs/{job_id}"})
        response.headers["Cache-Control"] = "no-store"
        return response, 201
    start_worker()
    _worker_wakeup.set()
    response = jsonify({"ok": True, **_public_job(job), "status_url": f"/api/share/jobs/{job_id}"})
    response.headers["Cache-Control"] = "no-store"
    return response, 202


@share_api_bp.route("/jobs/<job_id>", methods=["GET"])
def share_status(job_id):
    user = _token_user(())
    if not user:
        user, error = _browser_user()
        if error:
            return error
    directory = _job_dir(user["id"], job_id)
    job = _read_json(directory / "metadata.json") if directory else None
    if not isinstance(job, dict) or job.get("user_id") != user["id"]:
        return jsonify({"error": "Share-Auftrag nicht gefunden"}), 404
    with path_lock(directory / ".claim", exclusive=True):
        job = _read_json(directory / "metadata.json", job)
        _reconcile_job(user["id"], directory, job)
        job = _read_json(directory / "metadata.json", job)
    response = jsonify(_public_job(job))
    response.headers["Cache-Control"] = "no-store"
    return response


class _HtmlText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, _attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if not self._skip and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "article", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        value = " ".join(data.split())
        if value:
            self.parts.append(value + " ")
            if self._in_title:
                self.title.append(value)

    def text(self):
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        return re.sub(r"\n\s*\n+", "\n\n", value).strip()


def _public_host(hostname):
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("URL-Host konnte nicht aufgelöst werden") from exc
    addresses = {record[4][0] for record in records}
    if not addresses:
        raise ValueError("URL-Host besitzt keine Adresse")
    for raw in addresses:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
        if not address.is_global:
            raise ValueError("Interne, lokale oder nicht öffentliche URL-Ziele sind nicht erlaubt")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_remote_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_remote_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Unsichere URL")
    if parsed.port and parsed.port not in {80, 443}:
        raise ValueError("Nur die URL-Ports 80 und 443 sind erlaubt")
    _public_host(parsed.hostname)
    return parsed


def _fetch_url(url):
    _validate_remote_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SafeRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "Journl-Share/1.0", "Accept": "text/html,text/plain,application/pdf;q=0.9"})
    try:
        with opener.open(req, timeout=20) as response:
            final_url = response.geturl()
            _validate_remote_url(final_url)
            length = response.headers.get("Content-Length")
            if length:
                if not str(length).isdigit():
                    raise ValueError("URL meldet eine ungültige Inhaltsgröße")
                if int(length) > MAX_URL_BYTES:
                    raise ValueError("URL-Inhalt ist zu groß")
            raw = response.read(MAX_URL_BYTES + 1)
            if len(raw) > MAX_URL_BYTES:
                raise ValueError("URL-Inhalt ist zu groß")
            content_type = response.headers.get_content_type().casefold()
            charset = response.headers.get_content_charset() or "utf-8"
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise ValueError("URL konnte nicht sicher abgerufen werden") from exc
    if content_type == "application/pdf":
        return final_url, content_type, raw, _extract_pdf(io.BytesIO(raw)), ""
    if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
        raise ValueError("URL liefert weder Webseite, Text noch PDF")
    try:
        decoded = raw.decode(charset, errors="replace")
    except LookupError:
        decoded = raw.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        return final_url, content_type, raw, decoded, ""
    parser = _HtmlText()
    parser.feed(decoded)
    return final_url, content_type, raw, parser.text(), _clean_single_line(" ".join(parser.title), 160)


def _extract_pdf(source):
    from pypdf import PdfReader
    try:
        reader = PdfReader(source, strict=False)
        if reader.is_encrypted:
            raise ValueError("Passwortgeschützte PDFs werden nicht unterstützt")
        parts = []
        for page in reader.pages[:500]:
            parts.append(page.extract_text() or "")
            if sum(len(part) for part in parts) > MAX_CONTEXT_CHARS:
                break
        return "\n\n".join(part.strip() for part in parts if part.strip())
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF-Inhalt konnte nicht gelesen werden") from exc


def _xml_text(root, paragraph_names=()):
    parts = []
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local in paragraph_names and parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        if element.text and local in {"t", "p", "h", "span"}:
            parts.append(element.text)
        if local in {"p", "h", "row"}:
            parts.append("\n")
    return "".join(parts).strip()


def _extract_zip_document(path, suffix):
    def read_member(archive, name, budget):
        try:
            info = archive.getinfo(name)
        except KeyError as exc:
            raise ValueError("Office-Datei enthält nicht die erwartete Dokumentstruktur") from exc
        if info.flag_bits & 0x1 or info.file_size > budget:
            raise ValueError("Office-Datei ist verschlüsselt oder entpackt zu groß")
        return archive.read(info), budget - info.file_size

    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 2000:
                raise ValueError("Office-Datei enthält zu viele Bestandteile")
            budget = MAX_EXTRACT_BYTES
            if suffix == ".docx":
                raw, _ = read_member(archive, "word/document.xml", budget)
                return _xml_text(ElementTree.fromstring(raw), {"p"})
            if suffix == ".odt":
                raw, _ = read_member(archive, "content.xml", budget)
                return _xml_text(ElementTree.fromstring(raw), {"p", "h"})
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                raw, budget = read_member(archive, "xl/sharedStrings.xml", budget)
                root = ElementTree.fromstring(raw)
                shared = ["".join(node.text or "" for node in item.iter() if node.tag.rsplit("}", 1)[-1] == "t") for item in root if item.tag.rsplit("}", 1)[-1] == "si"]
            rows = []
            for name in sorted(item for item in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", item)):
                raw, budget = read_member(archive, name, budget)
                root = ElementTree.fromstring(raw)
                for row in (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "row"):
                    values = []
                    for cell in (node for node in row if node.tag.rsplit("}", 1)[-1] == "c"):
                        value = next((node.text or "" for node in cell if node.tag.rsplit("}", 1)[-1] == "v"), "")
                        if cell.attrib.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                        values.append(value)
                    if any(values):
                        rows.append("\t".join(values))
            return "\n".join(rows)
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError("Office-Datei konnte nicht gelesen werden") from exc


def _extract_file(path):
    suffix = path.suffix.casefold()
    if suffix in {".html", ".htm"}:
        parser = _HtmlText()
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            parser.feed(handle.read(MAX_CONTEXT_CHARS * 2))
        return parser.text()
    if suffix in _TEXT_SUFFIXES:
        with path.open("r", encoding="utf-8", errors="strict") as handle:
            return handle.read(MAX_CONTEXT_CHARS + 1)
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in {".docx", ".odt", ".xlsx"}:
        return _extract_zip_document(path, suffix)
    raise ValueError("Dieser Dateityp kann nicht inhaltlich ausgewertet werden")


def _source_path(job):
    path = (_main().DATA_DIR / str(job.get("source_path") or "")).resolve()
    root = _user_root(job["user_id"]).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Share-Quelldatei liegt außerhalb des Benutzerbereichs") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError("Share-Quelldatei fehlt")
    if hashlib.sha256(path.read_bytes()).hexdigest() != job.get("sha256"):
        raise ValueError("Share-Quelldatei wurde verändert")
    return path


def _prepare_context(job, directory):
    source_type = job["source_type"]
    attachment_images = []
    if source_type == "text":
        content = job.get("source_text") or ""
        label = "Geteilter Text"
    elif source_type == "url":
        final_url, content_type, raw, content, fetched_title = _fetch_url(job["source_url"])
        fetched = directory / ("fetched.pdf" if content_type == "application/pdf" else "fetched.bin")
        fetched.write_bytes(raw)
        job.update(final_url=final_url, fetched_content_type=content_type, fetched_sha256=hashlib.sha256(raw).hexdigest())
        if fetched_title and not job.get("title"):
            job["title"] = fetched_title
        label = f"Abgerufene URL: {final_url}"
    elif source_type == "audio":
        path = _source_path(job)
        transcript, content = ai_attachments._transcribe(
            path, os.environ.get("WHISPER_URL", "http://whisper:8090"), os.environ.get("WHISPER_API_KEY", ""),
        )
        _write_json(directory / "transcript.json", transcript)
        job["transcript_text"] = content
        label = f"Transkript der Audiodatei {job.get('original_filename') or path.name}"
    elif source_type == "image":
        path = _source_path(job)
        # The job UUID is also the stable snapshot UUID.  Retrying preparation
        # after a process crash cannot create a second untracked image tree.
        snapshot_id = job["id"]
        target_dir = _user_root(job["user_id"]) / "ai_jobs" / "attachments" / snapshot_id
        target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        suffix = ".jpg" if path.suffix.casefold() in {".jpg", ".jpeg"} else path.suffix.casefold()
        target = target_dir / ("image-1" + suffix)
        shutil.copyfile(path, target)
        raw = target.read_bytes()
        mime = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[suffix]
        attachment_images.append({
            "type": "image", "label": job.get("title") or job.get("original_filename") or "Geteiltes Bild",
            "mime_type": mime, "path": target.relative_to(_main().DATA_DIR).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw),
        })
        job["attachment_snapshot_id"] = snapshot_id
        content = "Das geteilte Bild ist als visueller Anhang beigefügt."
        label = "Geteiltes Bild"
    else:
        path = _source_path(job)
        content = _extract_file(path)
        label = f"Inhalt der Datei {job.get('original_filename') or path.name}"
    content = str(content or "").strip()
    if not content:
        raise ValueError("Die Quelle enthält keinen auswertbaren Inhalt")
    context = f"## {label}\n{content[:MAX_CONTEXT_CHARS]}"
    return context, attachment_images


def _update_job(directory, job, **changes):
    job.update(changes, updated_at=_iso(_now()))
    _write_json(directory / "metadata.json", job)
    return job


def _public_error(exc):
    if isinstance(exc, ValueError):
        return str(exc)[:500]
    return "Interner Fehler bei der Verarbeitung des Share-Auftrags"


def _process_job(user_id, directory, job):
    workflow = tagging.catalog_view(user_id).get("ai", {}).get(job.get("workflow_tag"))
    if not workflow or workflow.get("agent") != "pi" or workflow.get("target") != "daily_journal":
        raise ValueError("Der konfigurierte Tagesjournal-Workflow ist nicht mehr verfügbar")
    if not _main().agent_jobs_enabled():
        raise ValueError("DB-native Background-Jobs sind nicht verfügbar")
    if job.get("status") == "preparing":
        context, images = _prepare_context(job, directory)
        write_text_file(directory / "context.txt", context)
        user_request = job.get("extra_text") or "Verarbeite den freigegebenen Inhalt gemäß dem konfigurierten Workflow."
        job = _update_job(
            directory, job, status="prepared", attachment_images=images,
            user_request=user_request, prepared_at=_iso(_now()),
        )
    if job.get("status") == "prepared":
        session_id = background_jobs.create_agent_session(
            user_id, job["workflow_tag"], "pi", str(workflow.get("prompt") or ""),
            model_name=str(workflow.get("model") or ""),
        )
        job = _update_job(directory, job, status="queueing", agent_session_id=session_id)
    if job.get("status") != "queueing":
        return
    context = read_text_file(directory / "context.txt")
    if not context:
        raise ValueError("Vorbereiteter Share-Kontext fehlt")
    queued = background_jobs.queue_agent_request(
        job["agent_session_id"], user_id, job["user_request"], job["id"],
        submitted_text=job["user_request"], context_snapshot=context,
        attachment_images=job.get("attachment_images") or [],
    )
    _update_job(
        directory, job, status="queued", agent_job_id=queued["job_id"],
        queued_at=_iso(_now()), error=None,
    )


def _safe_suffix(path):
    suffix = path.suffix.casefold()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"


def _media_target(job, source, when):
    day_dir = _main()._get_day_dir(when, user_id=job["user_id"])
    basename = f"{when.strftime('%Y-%m-%d_%H-%M-%S')}_{job['id']}"
    if job["source_type"] == "audio":
        return day_dir / "media" / "Sprachi" / f"{basename}_audio{_safe_suffix(source)}"
    return day_dir / "media" / f"{basename}{_safe_suffix(source)}"


def _media_markdown(job, relative_path):
    kind = "audio" if job["source_type"] == "audio" else "image" if job["source_type"] == "image" else "document"
    marker = json.dumps({
        "id": job["id"], "type": kind, "captured_at": job["entry_at"],
        "mime_type": job.get("mime_type") or mimetypes.guess_type(relative_path)[0] or "application/octet-stream",
        "media_path": relative_path, "sha256": job.get("sha256"),
        "original_filename": job.get("original_filename"), "title": job.get("title") or None,
    }, ensure_ascii=False, separators=(",", ":"))
    label = {"audio": "Sprachnachricht", "image": "Foto", "document": "Dokument"}[kind]
    lines = [f"<!-- jt:media {marker} -->", f"{label}: {job.get('title') or job.get('original_filename') or 'Geteilter Inhalt'}"]
    if kind == "audio" and job.get("transcript_text"):
        lines.extend([
            f'<!-- jt:transcript {{"media_id":"{job["id"]}"}} -->',
            "### Transkript #Sprachnachricht #Transkription #Audio-Transkription",
            job["transcript_text"],
        ])
    return "\n\n".join(lines)


def _direct_entry_content(job):
    """Keep an unprocessed share useful without pretending it was AI output."""
    source_type = job["source_type"]
    extra = str(job.get("extra_text") or "").strip()
    if source_type == "text":
        source = str(job.get("source_text") or "").strip()
        return "\n\n".join(part for part in (source, f"Zusatzinformation:\n{extra}" if extra else "") if part)
    if source_type == "url":
        return extra or "API-Input #url"
    label = "API-Input #datei" if source_type == "file" else "API-Input #bild" if source_type == "image" else "API-Input #sprachnachricht"
    return "\n\n".join(part for part in (label, extra) if part)


def _entry_markdown(job, response, media_text=""):
    when = datetime.fromisoformat(job["entry_at"])
    title = job.get("title") or "Geteilter Inhalt"
    marker = json.dumps({"id": job["id"], "mode": job.get("mode", "ai"), "workflow_tag": job.get("workflow_tag")}, separators=(",", ":"))
    safe_response = re.sub(r"<!--\s*jt:", "&lt;!-- jt:", response.strip(), flags=re.IGNORECASE)
    safe_response = re.sub(r"(?m)^\s*___\s*$", r"\\___", safe_response)
    body = [f"<!-- journl-share-job {marker} -->", f"## {title} | Datum & Uhrzeit: {when.strftime('%Y-%m-%d %H:%M:%S')}", safe_response]
    if job.get("final_url") or job.get("source_url"):
        source_url = str(job.get("final_url") or job["source_url"]).replace("<", "%3C").replace(">", "%3E")
        body.append(f"Quelle: <{source_url}>")
    if media_text:
        body.append(media_text)
    if job.get("hashtags"):
        body.append(" ".join("#" + tag for tag in job["hashtags"]))
    return "___\n\n" + "\n\n".join(body) + "\n\n___\n\n"


def _cleanup_attachment_snapshot(job):
    snapshot_id = job.get("attachment_snapshot_id")
    if not snapshot_id:
        return
    try:
        ai_attachments.cleanup_snapshot(_main().DATA_DIR, job["user_id"], snapshot_id)
    except (OSError, ValueError):
        pass


def _deliver_job(user_id, directory, job, db_job):
    response = str(db_job.get("response_text") or "").strip()
    if not response:
        raise ValueError("AI-Workflow lieferte keinen Journaleintrag")
    when = datetime.fromisoformat(job["entry_at"])
    journal_path = _main()._get_journal_path(when, user_id=user_id)
    marker = f'<!-- journl-share-job {{"id":"{job["id"]}"'
    if marker in read_text_file(journal_path):
        _update_job(directory, job, status="completed", completed_at=_iso(_now()), journal_path=journal_path.relative_to(_user_root(user_id)).as_posix(), error=None)
        return
    source = None
    target = None
    media_text = ""
    if job["source_type"] in {"audio", "image", "file"}:
        try:
            source = _source_path(job)
        except ValueError:
            final_relative = job.get("media_path")
            candidate = (_user_root(user_id) / str(final_relative or "")).resolve()
            if not final_relative or not candidate.is_file():
                raise
            target = candidate
        if source:
            target = _media_target(job, source, when)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("Zieldatei für Share-Anhang existiert bereits")
            os.replace(source, target)
            job["media_path"] = target.relative_to(_user_root(user_id)).as_posix()
        media_text = _media_markdown(job, job["media_path"])
    entry = _entry_markdown(job, response, media_text)
    try:
        _main()._append_journal_entry(user_id, journal_path, entry)
    except Exception:
        if source and target and target.exists() and not source.exists():
            os.replace(target, source)
        raise
    if job["source_type"] == "audio" and target:
        base = target.name.rsplit("_audio", 1)[0]
        sidecar = {
            **job, "type": "audio", "storage": "journal",
            "journal_path": journal_path.relative_to(_user_root(user_id)).as_posix(),
        }
        transcript_path = (target.parent / f"{base}_transcript.json").relative_to(_user_root(user_id)).as_posix()
        if job.get("mode") == "direct":
            # Direct audio is a normal Sprachi: keep it eligible for the
            # existing private Whisper scheduler instead of invoking AI here.
            sidecar.update(status="ready", transcription_status="pending", transcript_path=transcript_path)
        else:
            # AI mode already transcribed synchronously in _prepare_context();
            # still mark status="ready" so the sidecar stays eligible for the
            # scheduler's/manual retranscribe's status filter (both skip
            # entries without status="ready" silently).
            sidecar.update(status="ready", transcription_status="completed", transcript_path=transcript_path)
        _write_json(target.parent / f"{base}_metadata.json", sidecar)
        if (directory / "transcript.json").is_file():
            shutil.copyfile(directory / "transcript.json", target.parent / f"{base}_transcript.json")
    _cleanup_attachment_snapshot(job)
    _update_job(
        directory, job, status="completed", completed_at=_iso(_now()),
        journal_path=journal_path.relative_to(_user_root(user_id)).as_posix(), error=None,
    )


def _reconcile_job(user_id, directory, job):
    agent_job_id = job.get("agent_job_id")
    if not agent_job_id or job.get("status") not in {"queued", "running", "delivery_pending"}:
        return
    try:
        db_job = background_jobs.agent_job_for_owner(agent_job_id, user_id)
    except (RuntimeError, ValueError):
        return
    if not db_job:
        return
    if db_job.get("response_status") == "proposed":
        _update_job(directory, job, status="delivery_pending")
        _deliver_job(user_id, directory, job, db_job)
    elif db_job.get("status") in {"failed", "timed_out", "cancelled"}:
        _cleanup_attachment_snapshot(job)
        _update_job(
            directory, job, status="failed", completed_at=_iso(_now()),
            error=background_jobs.agent_error_message(db_job.get("error_code")) or "AI-Workflow ist fehlgeschlagen",
        )
    elif db_job.get("status") == "leased" and job.get("status") != "running":
        _update_job(directory, job, status="running")


def finalize_agent_job(agent_job_id):
    """Finalize a Share job immediately after the authenticated worker responds."""
    root = _main().DATA_DIR
    for metadata_path in root.glob("*/.share-jobs/*/metadata.json"):
        job = _read_json(metadata_path)
        if isinstance(job, dict) and job.get("agent_job_id") == agent_job_id:
            directory = metadata_path.parent
            with path_lock(directory / ".claim", exclusive=True):
                current = _read_json(metadata_path, job)
                _reconcile_job(current["user_id"], directory, current)
            return True
    return False


def _worker_loop():
    while True:
        try:
            for metadata_path in _main().DATA_DIR.glob("*/.share-jobs/*/metadata.json"):
                directory = metadata_path.parent
                with path_lock(directory / ".claim", exclusive=True):
                    job = _read_json(metadata_path)
                    if not isinstance(job, dict):
                        continue
                    try:
                        if job.get("status") in {"preparing", "prepared", "queueing"}:
                            _process_job(job["user_id"], directory, job)
                        else:
                            _reconcile_job(job["user_id"], directory, job)
                    except Exception as exc:
                        _cleanup_attachment_snapshot(job)
                        logger.warning("Share job %s failed during %s: %s", job.get("id"), job.get("status"), type(exc).__name__)
                        _update_job(directory, job, status="failed", completed_at=_iso(_now()), error=_public_error(exc))
        except Exception as exc:
            logger.warning("Share worker scan failed: %s", type(exc).__name__)
        _worker_wakeup.wait(2)
        _worker_wakeup.clear()


def start_worker():
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return False
        _worker_thread = threading.Thread(target=_worker_loop, name="share-api-worker", daemon=True)
        _worker_thread.start()
        return True


__all__ = ["share_api_bp", "start_worker", "finalize_agent_job", "TOKEN_SCOPES", "_token_user"]
