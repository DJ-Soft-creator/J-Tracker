"""Server-side writing sessions and private media attachments."""

import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from PIL import Image, ImageOps

from scheduling import path_lock, read_text_file, write_text_file
import transcript_blocks
import write_session_audio
import write_session_cleanup


write_sessions_bp = Blueprint("write_sessions", __name__, url_prefix="/api/write-sessions")
SESSION_TTL = timedelta(days=7)
SESSION_EXTENSION = timedelta(days=1)
_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
_REV_RE = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic", "image/heif": ".heif"}
_DOCUMENT_MIMES = {
    "application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md",
    "text/csv": ".csv", "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
_DOCUMENT_EXTENSIONS = {extension: mime for mime, extension in _DOCUMENT_MIMES.items()}
_FINAL_EXTENSION_MIMES = {
    extension: mime
    for mime, extension in {**_IMAGE_MIMES, **write_session_audio.AUDIO_MIMES, **_DOCUMENT_MIMES}.items()
}
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_DOCUMENT_BYTES = 40 * 1024 * 1024
_FINAL_MEDIA_RE = re.compile(
    r"^(?:"
    r"\d{4}/\d{2}/\d{2}/media/(?:Sprachi/)?\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[0-9a-f-]{36}(?:_audio)?\.[A-Za-z0-9]+"
    r"|\d{4}/\d{2}/\d{2}/(?:Foto|Sprachnachricht|Dokument)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_[A-Za-z0-9._-]+)?(?:_\d+)?\.[A-Za-z0-9]+"
    r")$"
)
_INBOX_METADATA_RE = re.compile(
    r"^\d{4}/\d{2}/\d{2}/media/Screenshot-Inbox/"
    r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[0-9a-f-]{36}\.json$"
)
_DAY_DIR_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
# The editor marks where new attachments belong before their final paths exist.
MEDIA_PLACEHOLDER = "<!-- jt:media-placeholder -->"
_PLACEHOLDER_RE = re.compile(r"\n*[ \t]*" + re.escape(MEDIA_PLACEHOLDER) + r"[ \t]*")


def _main():
    import sys
    return sys.modules.get("main") or sys.modules.get("__main__")


def _user():
    main = _main()
    return main._current_user() if main else None


def _csrf_error():
    main = _main()
    return main.csrf_protect(lambda: None)() if main else (jsonify({"error": "Unauthorized"}), 401)


def _require_user():
    user = _user()
    return (user, None) if user else (None, (jsonify({"error": "Unauthorized"}), 401))


def _root(user_id):
    return _main().DATA_DIR / user_id / "write_sessions"


def _user_root(user_id):
    return _main().DATA_DIR / user_id


def _session_dir(user_id, session_id):
    if not _ID_RE.fullmatch(session_id or ""):
        return None
    root = _root(user_id)
    candidate = root / session_id
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _now():
    return datetime.now(timezone.utc).astimezone()


def _iso(value):
    return value.isoformat(timespec="seconds")


def _parse_iso(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _revision(content):
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _read_json(path, default=None):
    if not path or not path.is_file() or path.is_symlink():
        return default
    try:
        value = json.loads(read_text_file(path))
        return value
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path, value):
    write_text_file(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _inbox_metadata_file(user_id, item):
    relative = item.get("inbox_metadata_path") if isinstance(item, dict) else None
    if not isinstance(relative, str) or not _INBOX_METADATA_RE.fullmatch(relative):
        return None
    root = _user_root(user_id).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if not path.is_symlink() else None


def _inbox_item_file(user_id, relative, metadata_path, media_id):
    if not isinstance(relative, str):
        return None
    root = _user_root(user_id).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if path.is_symlink() or path.parent != metadata_path.parent or f"_{media_id}" not in path.name:
        return None
    return path


def _release_inbox_attachment(user_id, item):
    """Make a reserved upload selectable again when its draft attachment is removed."""
    metadata_path = _inbox_metadata_file(user_id, item)
    inbox = _read_json(metadata_path) if metadata_path else None
    if not isinstance(inbox, dict) or inbox.get("id") != item.get("id"):
        return
    if inbox.get("status") == "attached" and inbox.get("attached_session_id") == item.get("session_id"):
        inbox.update(status="pending", updated_at=_iso(_now()))
        inbox.pop("attached_session_id", None)
        _write_json(metadata_path, inbox)


def _consume_inbox_attachment(user_id, item):
    """Retire the inbox copy only after its final journal media has been committed."""
    metadata_path = _inbox_metadata_file(user_id, item)
    inbox = _read_json(metadata_path) if metadata_path else None
    if not isinstance(inbox, dict) or inbox.get("id") != item.get("id"):
        return
    inbox.update(status="linked", linked_at=_iso(_now()), media_path=item.get("media_path"))
    try:
        _write_json(metadata_path, inbox)
    except OSError:
        # Inbox cleanup is housekeeping after the journal and its immutable
        # media metadata are already durable; never turn that into a duplicate
        # journal submission on retry.
        return
    for key in ("source_path", "preview_path"):
        path = _inbox_item_file(user_id, inbox.get(key), metadata_path, item.get("id"))
        try:
            if path:
                path.unlink(missing_ok=True)
        except OSError:
            return
    try:
        metadata_path.unlink(missing_ok=True)
    except OSError:
        pass


def _load_session(user_id, session_id):
    directory = _session_dir(user_id, session_id)
    data = _read_json(directory / "session.json") if directory else None
    if not isinstance(data, dict) or data.get("id") != session_id or data.get("user_id") != user_id:
        return None, directory
    return data, directory


def _is_expired(data, now=None):
    expires = _parse_iso(data.get("expires_at"))
    return bool(expires and (now or _now()) >= expires and data.get("status") == "active")


def _media_items(directory):
    """Return a session's attachments in capture order, not in UUID order."""
    items = []
    media_root = directory / "media"
    if not media_root.is_dir():
        return items
    for path in media_root.glob("*/metadata.json"):
        item = _read_json(path)
        if isinstance(item, dict):
            items.append(item)
    return sorted(items, key=lambda item: _parse_iso(item.get("captured_at")) or datetime.min.replace(tzinfo=timezone.utc))


def _session_preview(content, limit=180):
    """Return a short continuation after a session's first non-empty line."""
    seen_title = False
    continuation = []
    for line in str(content or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if not seen_title:
            seen_title = True
            continue
        continuation.append(text)
    preview = " ".join(continuation)
    if len(preview) <= limit:
        return preview
    shortened = preview[:limit].rsplit(" ", 1)[0].strip() or preview[:limit].strip()
    return shortened + "…"


def _public_session(data, directory, include_content=False):
    result = {key: data.get(key) for key in (
        "id", "status", "created_at", "updated_at", "expires_at", "template_id", "title", "revision"
    )}
    result["expired"] = _is_expired(data)
    result["media_count"] = len(_media_items(directory))
    result["preview"] = _session_preview(data.get("content", ""))
    if include_content:
        result["content"] = data.get("content", "")
        result["media"] = _media_items(directory)
    return result


def _new_session(user_id, content="", template_id="schnell"):
    root = _root(user_id)
    with path_lock(root / ".collection", exclusive=True):
        now = _now()
        session_id = str(uuid.uuid4())
        directory = root / session_id
        content = str(content or "")
        data = {
            "schema": 1, "id": session_id, "user_id": user_id, "status": "active",
            "created_at": _iso(now), "updated_at": _iso(now), "expires_at": _iso(now + SESSION_TTL),
            "template_id": str(template_id or "schnell")[:100], "title": next((line.strip()[:100] for line in content.splitlines() if line.strip()), ""), "content": content,
            "revision": _revision(content),
        }
        directory.mkdir(parents=True, exist_ok=False)
        _write_json(directory / "session.json", data)
    return data, directory


@write_sessions_bp.route("", methods=["GET", "POST", "DELETE"])
def sessions_collection():
    user, error = _require_user()
    if error:
        return error
    if request.method == "GET":
        sessions = []
        root = _root(user["id"])
        write_session_cleanup.purge_daily(root)
        if root.is_dir():
            for path in root.glob("*/session.json"):
                data = _read_json(path)
                if isinstance(data, dict) and data.get("user_id") == user["id"] and data.get("status") in {"active", "archived"}:
                    write_session_audio.recover_stale(path.parent)
                    sessions.append(_public_session(data, path.parent))
        sessions.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        response = jsonify({"sessions": sessions})
        response.headers["Cache-Control"] = "no-store"
        return response
    csrf = _csrf_error()
    if csrf:
        return csrf
    if request.method == "DELETE":
        deleted = []
        root = _root(user["id"])
        # This is the deliberately confirmed recovery action for broken or
        # excessive drafts. Unlike single deletion it also removes sessions
        # with a stuck upload, while archived sessions remain untouched.
        with path_lock(root / ".collection", exclusive=True):
            for path in list(root.glob("*/session.json")) if root.is_dir() else []:
                directory = path.parent
                if directory.is_symlink():
                    continue
                data = _read_json(path)
                if not isinstance(data, dict) or data.get("user_id") != user["id"] or data.get("status") != "active":
                    continue
                session_id = data.get("id")
                if not _ID_RE.fullmatch(session_id or "") or directory.name != session_id:
                    continue
                with path_lock(directory / ".session", exclusive=True):
                    current = _read_json(directory / "session.json")
                    if not isinstance(current, dict) or current.get("id") != session_id or current.get("user_id") != user["id"] or current.get("status") != "active":
                        continue
                    for item in _media_items(directory):
                        _release_inbox_attachment(user["id"], item)
                    shutil.rmtree(directory)
                    deleted.append(session_id)
        return jsonify({"ok": True, "deleted": len(deleted), "ids": deleted})
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not isinstance(content, str) or len(content) > 200000:
        return jsonify({"error": "Ungültiger Session-Inhalt"}), 400
    created, directory = _new_session(user["id"], content, data.get("template_id", "schnell"))
    return jsonify(_public_session(created, directory, include_content=True)), 201


@write_sessions_bp.route("/<session_id>", methods=["GET", "PUT", "DELETE"])
def session_item(session_id):
    user, error = _require_user()
    if error:
        return error
    data, directory = _load_session(user["id"], session_id)
    if not data:
        return jsonify({"error": "Session nicht gefunden"}), 404
    if request.method == "GET":
        write_session_audio.recover_stale(directory)
        response = jsonify(_public_session(data, directory, include_content=True))
        response.headers["Cache-Control"] = "no-store"
        return response
    csrf = _csrf_error()
    if csrf:
        return csrf
    if request.method == "DELETE":
        # A session is only removable before it has been archived into a note.
        # Never remove a directory while an audio upload is still in progress.
        if data.get("status") != "active" or directory.is_symlink():
            return jsonify({"error": "Nur eine aktive Session kann gelöscht werden."}), 409
        with path_lock(_root(user["id"]) / ".collection", exclusive=True):
            with path_lock(directory / ".session", exclusive=True):
                current = _read_json(directory / "session.json")
                if not isinstance(current, dict) or current.get("id") != session_id:
                    return jsonify({"error": "Session nicht gefunden"}), 404
                write_session_audio.recover_stale(directory)
                if any(item.get("status") == "uploading" for item in _media_items(directory)):
                    return jsonify({"error": "Eine Sprachnachricht wird gerade aufgenommen. Bitte die Aufnahme erst beenden."}), 409
                for item in _media_items(directory):
                    _release_inbox_attachment(user["id"], item)
                shutil.rmtree(directory)
        return jsonify({"ok": True, "id": session_id})
    if data.get("status") != "active" or _is_expired(data):
        return jsonify({"error": "Die Session muss zuerst verlängert, archiviert oder verworfen werden.", "expired": True}), 409
    payload = request.get_json(silent=True) or {}
    content, expected = payload.get("content"), payload.get("revision")
    discard_if_empty = payload.get("discard_if_empty", False)
    if not isinstance(content, str) or len(content) > 200000 or not isinstance(expected, str) or not _REV_RE.fullmatch(expected):
        return jsonify({"error": "Ungültiger Session-Stand"}), 400
    if not isinstance(discard_if_empty, bool):
        return jsonify({"error": "Ungültige Leerstand-Behandlung"}), 400
    with path_lock(_root(user["id"]) / ".collection", exclusive=True):
        current, directory = _load_session(user["id"], session_id)
        if not current:
            return jsonify({"error": "Session nicht gefunden"}), 404
        if current.get("status") != "active" or _is_expired(current):
            return jsonify({"error": "Die Session muss zuerst verlängert, archiviert oder verworfen werden.", "expired": True}), 409
        with path_lock(directory / ".session", exclusive=True):
            current = _read_json(directory / "session.json")
            if not isinstance(current, dict):
                return jsonify({"error": "Session nicht gefunden"}), 404
            if current.get("revision") != expected:
                return jsonify({"error": "Die Session wurde auf einem anderen Gerät geändert.", **_public_session(current, directory, True)}), 409
            now = _now()
            current.update(content=content, revision=_revision(content), updated_at=_iso(now), template_id=str(payload.get("template_id") or current.get("template_id") or "schnell")[:100], title=next((line.strip()[:100] for line in content.splitlines() if line.strip()), ""))
            # A deliberately blank editor is not a resumable thought. Keep
            # media-only sessions, but hide recoverable text-free remnants from
            # the open-session list instead of accumulating ghost cards.
            if discard_if_empty and not content.strip() and not _media_items(directory):
                current.update(status="discarded", discarded_at=_iso(now), discard_reason="empty")
            _write_json(directory / "session.json", current)
    return jsonify(_public_session(current, directory, include_content=True))


@write_sessions_bp.route("/<session_id>/decision", methods=["POST"])
def session_decision(session_id):
    user, error = _require_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    action = (request.get_json(silent=True) or {}).get("action")
    with path_lock(_root(user["id"]) / ".collection", exclusive=True):
        data, directory = _load_session(user["id"], session_id)
        if not data:
            return jsonify({"error": "Session nicht gefunden"}), 404
        with path_lock(directory / ".session", exclusive=True):
            data = _read_json(directory / "session.json")
            if not isinstance(data, dict) or data.get("status") != "active":
                return jsonify({"error": "Nur eine aktive Session kann geändert werden."}), 409
            if action == "extend":
                if not _is_expired(data):
                    return jsonify({"error": "Nur abgelaufene Sessions können verlängert werden."}), 409
                data.update(expires_at=_iso(_now() + SESSION_EXTENSION), updated_at=_iso(_now()))
            elif action == "archive":
                archive = _main().DATA_DIR / user["id"] / "notes" / f"Session_{data['created_at'][:10]}_{data['id'][:8]}.md"
                lines = ["---", f"id: {data['id']}", f"created_at: {data['created_at']}", "type: write-session", "---", "", data.get("content", "").strip()]
                for media in _media_items(directory):
                    lines.extend(["", f"Medienanhang: {media.get('type')} | {media.get('captured_at')} | {media.get('id')}"])
                write_text_file(archive, "\n".join(lines).rstrip() + "\n")
                data.update(status="archived", archived_path=str(archive.relative_to(_main().DATA_DIR / user["id"])), updated_at=_iso(_now()))
                for item in _media_items(directory):
                    _release_inbox_attachment(user["id"], item)
                _main().brain_module.enqueue_rebuild(user["id"])
            elif action == "discard":
                data.update(status="discarded", updated_at=_iso(_now()))
                for item in _media_items(directory):
                    _release_inbox_attachment(user["id"], item)
            else:
                return jsonify({"error": "Unbekannte Session-Entscheidung"}), 400
            _write_json(directory / "session.json", data)
    return jsonify(_public_session(data, directory, include_content=True))


def _captured_at(value):
    parsed = _parse_iso(value)
    return _iso(parsed) if parsed else _iso(_now())


def _new_media(data, directory, kind, mime, captured_at):
    media_id = str(uuid.uuid4())
    media_dir = directory / "media" / media_id
    media_dir.mkdir(parents=True, exist_ok=False)
    item = {
        "schema": 1, "id": media_id, "session_id": data["id"], "user_id": data["user_id"],
        "type": kind, "mime_type": mime, "captured_at": _captured_at(captured_at),
        "received_at": _iso(_now()), "status": "uploading" if kind == "audio" else "ready",
        "transcription_status": "pending" if kind == "audio" else "not_applicable",
    }
    _write_json(media_dir / "metadata.json", item)
    return item, media_dir


def _media_title(value):
    """Keep attachment titles compact enough for one human-readable journal line."""
    if not isinstance(value, str):
        return None
    title = " ".join(value.split())
    return title[:160]


@write_sessions_bp.route("/<session_id>/images", methods=["POST"])
def upload_image(session_id):
    user, error = _require_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    data, directory = _load_session(user["id"], session_id)
    if not data or data.get("status") != "active" or _is_expired(data):
        return jsonify({"error": "Aktive Session erforderlich"}), 409
    upload = request.files.get("file")
    mime = (upload.mimetype or "").split(";", 1)[0].lower() if upload else ""
    if not upload or mime not in _IMAGE_MIMES:
        return jsonify({"error": "Nicht unterstütztes Bildformat"}), 400
    raw = upload.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        return jsonify({"error": "Bild ist zu groß"}), 413
    item, media_dir = _new_media(data, directory, "image", mime, request.form.get("captured_at"))
    original = media_dir / ("original" + _IMAGE_MIMES[mime])
    original.write_bytes(raw)
    item.update(original_name=original.name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    try:
        with Image.open(io.BytesIO(raw)) as image:
            preview = ImageOps.exif_transpose(image)
            preview.thumbnail((1600, 1600))
            if preview.mode not in {"RGB", "L"}:
                preview = preview.convert("RGB")
            preview.save(media_dir / "preview.jpg", "JPEG", quality=82, optimize=True)
            item["preview_name"] = "preview.jpg"
    except Exception:
        item["preview_name"] = None
    _write_json(media_dir / "metadata.json", item)
    return jsonify(item), 201


def _document_mime(upload):
    """Accept a small, explicit document set; browsers often call office files octet-stream."""
    supplied = (upload.mimetype or "").split(";", 1)[0].lower()
    if supplied in _DOCUMENT_MIMES:
        return supplied
    suffix = Path(upload.filename or "").suffix.lower()
    return _DOCUMENT_EXTENSIONS.get(suffix) if supplied in {"", "application/octet-stream"} else None


@write_sessions_bp.route("/<session_id>/documents", methods=["POST"])
def upload_document(session_id):
    user, error = _require_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    data, directory = _load_session(user["id"], session_id)
    if not data or data.get("status") != "active" or _is_expired(data):
        return jsonify({"error": "Aktive Session erforderlich"}), 409
    upload = request.files.get("file")
    mime = _document_mime(upload) if upload else None
    if not upload or not mime:
        return jsonify({"error": "Nicht unterstütztes Dokumentformat"}), 400
    raw = upload.read(MAX_DOCUMENT_BYTES + 1)
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        return jsonify({"error": "Dokument ist leer oder zu groß"}), 413
    item, media_dir = _new_media(data, directory, "document", mime, request.form.get("captured_at"))
    original = media_dir / ("original" + _DOCUMENT_MIMES[mime])
    original.write_bytes(raw)
    item.update(original_name=original.name, original_filename=Path(upload.filename or "Dokument").name[:255], size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    _write_json(media_dir / "metadata.json", item)
    return jsonify(item), 201


@write_sessions_bp.route("/<session_id>/audio", methods=["POST"])
def start_audio(session_id):
    user, error = _require_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    data, directory = _load_session(user["id"], session_id)
    payload = request.get_json(silent=True) or {}
    mime = str(payload.get("mime_type") or "").split(";", 1)[0].lower()
    if not data or data.get("status") != "active" or _is_expired(data):
        return jsonify({"error": "Aktive Session erforderlich"}), 409
    if mime not in write_session_audio.AUDIO_MIMES:
        return jsonify({"error": "Nicht unterstütztes Audioformat"}), 400
    item, media_dir = _new_media(data, directory, "audio", mime, payload.get("captured_at"))
    # Herkunft: das Sternchen-Mikrofon (write-voice.js) transkribiert immer ueber
    # den lokalen Voice-Anbieter; der Whisper-Sidecar laesst solche Aufnahmen aus.
    if payload.get("engine") == "voice":
        item["engine"] = "voice"
        _write_json(media_dir / "metadata.json", item)
    return jsonify(item), 201


@write_sessions_bp.route("/media/<media_id>/chunks/<int:index>", methods=["PUT"])
def upload_audio_chunk(media_id, index):
    user, error = _require_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    if index < 0 or index > 100000:
        return jsonify({"error": "Ungültiger Chunk"}), 400
    media_dir = None
    for candidate in _root(user["id"]).glob(f"*/media/{media_id}"):
        if candidate.is_dir() and not candidate.is_symlink():
            media_dir = candidate
            break
    item = _read_json(media_dir / "metadata.json") if media_dir else None
    if not item or item.get("user_id") != user["id"] or item.get("type") != "audio":
        return jsonify({"error": "Audio nicht gefunden"}), 404
    raw = request.get_data(cache=False)
    if not raw or len(raw) > write_session_audio.MAX_AUDIO_CHUNK_BYTES:
        return jsonify({"error": "Ungültiger Audio-Chunk"}), 413
    chunks = media_dir / "chunks"
    chunks.mkdir(exist_ok=True)
    target = chunks / f"{index:06d}.part"
    digest = hashlib.sha256(raw).hexdigest()
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        return jsonify({"error": "Chunk-Konflikt"}), 409
    if not target.exists():
        target.write_bytes(raw)
    return jsonify({"ok": True, "index": index, "sha256": digest})


@write_sessions_bp.route("/media/<media_id>/complete", methods=["POST"])
def complete_audio(media_id):
    user, error = _require_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    media_dir = next((path for path in _root(user["id"]).glob(f"*/media/{media_id}") if path.is_dir()), None)
    item = _read_json(media_dir / "metadata.json") if media_dir else None
    if not item or item.get("user_id") != user["id"] or item.get("type") != "audio":
        return jsonify({"error": "Audio nicht gefunden"}), 404
    expected_count = (request.get_json(silent=True) or {}).get("chunk_count")
    if not isinstance(expected_count, int) or not 0 < expected_count <= 100000:
        return jsonify({"error": "Ungültige erwartete Chunk-Anzahl"}), 400
    chunks = write_session_audio.chunk_files(media_dir, expected_count)
    if len(chunks) != expected_count:
        write_session_audio.mark_failed(media_dir, item, "Audio unvollständig; mindestens ein Chunk fehlt")
        return jsonify({"error": "Audio unvollständig; mindestens ein Chunk fehlt"}), 409
    return jsonify(write_session_audio.assemble(media_dir, item, chunks))


@write_sessions_bp.route("/<session_id>/media/<media_id>", methods=["DELETE"])
def delete_session_media(session_id, media_id):
    """Remove one unsubmitted attachment. The deliberate confirmation happens in the UI."""
    user, error = _require_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    data, directory = _load_session(user["id"], session_id)
    if not data or data.get("status") != "active" or _is_expired(data):
        return jsonify({"error": "Aktive Session erforderlich"}), 409
    if not _ID_RE.fullmatch(media_id or ""):
        return jsonify({"error": "Medium nicht gefunden"}), 404
    media_dir = directory / "media" / media_id
    item = _read_json(media_dir / "metadata.json")
    if not item or item.get("id") != media_id or item.get("session_id") != session_id or item.get("user_id") != user["id"] or media_dir.is_symlink():
        return jsonify({"error": "Medium nicht gefunden"}), 404
    try:
        media_dir.resolve().relative_to((directory / "media").resolve())
    except ValueError:
        return jsonify({"error": "Ungültiger Medienpfad"}), 400
    _release_inbox_attachment(user["id"], item)
    shutil.rmtree(media_dir)
    data.update(updated_at=_iso(_now()))
    _write_json(directory / "session.json", data)
    return jsonify(_public_session(data, directory, include_content=True))


@write_sessions_bp.route("/<session_id>/media/<media_id>", methods=["PUT"])
def update_session_media(session_id, media_id):
    """Set the optional human title of one active attachment."""
    user, error = _require_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    data, directory = _load_session(user["id"], session_id)
    if not data or data.get("status") != "active" or _is_expired(data):
        return jsonify({"error": "Aktive Session erforderlich"}), 409
    if not _ID_RE.fullmatch(media_id or ""):
        return jsonify({"error": "Medium nicht gefunden"}), 404
    title = _media_title((request.get_json(silent=True) or {}).get("title"))
    if title is None:
        return jsonify({"error": "Ungültiger Titel"}), 400
    media_dir = directory / "media" / media_id
    item = _read_json(media_dir / "metadata.json")
    if (
        not item or item.get("id") != media_id or item.get("session_id") != session_id
        or item.get("user_id") != user["id"] or item.get("type") not in {"image", "audio", "document"}
        or media_dir.is_symlink()
    ):
        return jsonify({"error": "Anhang nicht gefunden"}), 404
    try:
        media_dir.resolve().relative_to((directory / "media").resolve())
    except ValueError:
        return jsonify({"error": "Ungültiger Medienpfad"}), 400
    with path_lock(directory / ".session", exclusive=True):
        item.update(title=title, updated_at=_iso(_now()))
        _write_json(media_dir / "metadata.json", item)
        data.update(updated_at=_iso(_now()))
        _write_json(directory / "session.json", data)
    return jsonify(_public_session(data, directory, include_content=True))


def _find_media(user_id, media_id):
    if not _ID_RE.fullmatch(media_id or ""):
        return None, None
    for path in _root(user_id).glob(f"*/media/{media_id}/metadata.json"):
        item = _read_json(path)
        if isinstance(item, dict) and item.get("user_id") == user_id:
            return item, path.parent
    for path in _user_root(user_id).glob(f"*/*/*/media/Sprachi/*_{media_id}_metadata.json"):
        item = _read_json(path)
        if isinstance(item, dict) and item.get("user_id") == user_id:
            return item, path.parent
    for path in _user_root(user_id).glob(f"*/*/*/.journal-media/{media_id}.json"):
        item = _read_json(path)
        if isinstance(item, dict) and item.get("user_id") == user_id:
            return item, path.parent
    return None, None


def _final_media_file(user_id, relative_path):
    """Resolve only a final attachment path, never an arbitrary user file."""
    if not isinstance(relative_path, str) or not _FINAL_MEDIA_RE.fullmatch(relative_path):
        return None
    root = _user_root(user_id).resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() and not path.is_symlink() else None


def _final_media_mimetype(path):
    """Return stable MIME types for attachment extensions across host images."""
    return _FINAL_EXTENSION_MIMES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


@write_sessions_bp.route("/media/<media_id>/<variant>")
def media_content(media_id, variant):
    user, error = _require_user()
    if error:
        return error
    item, directory = _find_media(user["id"], media_id)
    if not item or variant not in {"original", "preview"}:
        return jsonify({"error": "Medium nicht gefunden"}), 404
    if item.get("media_path"):
        path = _final_media_file(user["id"], item["media_path"])
        if variant == "preview" and item.get("preview_name"):
            candidate = directory / str(item["preview_name"])
            path = candidate if candidate.is_file() and not candidate.is_symlink() else path
    else:
        name = item.get("preview_name") if variant == "preview" else item.get("original_name")
        path = directory / str(name or "")
    if not path or not path.is_file() or path.is_symlink():
        return jsonify({"error": "Datei nicht gefunden"}), 404
    response = send_file(path, mimetype="image/jpeg" if variant == "preview" else item.get("mime_type"), conditional=True)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@write_sessions_bp.route("/media/final")
def final_media_content():
    """Serve an attachment by the immutable path stored in its journal marker."""
    user, error = _require_user()
    if error:
        return error
    path = _final_media_file(user["id"], request.args.get("path"))
    if not path:
        return jsonify({"error": "Datei nicht gefunden"}), 404
    response = send_file(path, mimetype=_final_media_mimetype(path), conditional=True)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


def submission_media(user_id, session_id):
    data, directory = _load_session(user_id, session_id)
    if not data or data.get("status") != "active" or _is_expired(data):
        raise ValueError("Aktive, nicht abgelaufene Session erforderlich")
    write_session_audio.recover_stale(directory)
    media = _media_items(directory)
    if any(item.get("status") == "failed" for item in media):
        raise ValueError("Eine Aufnahme ist unvollständig. Bitte sie im Anhang entfernen und erneut senden.")
    if any(item.get("status") != "ready" for item in media):
        raise ValueError("Eine Sprachnachricht wird gerade aufgenommen. Bitte die Aufnahme erst beenden.")
    return data, directory, media


def commit_submission_without_media(user_id, session_id, submission_target=None):
    """Complete a successful non-journal submission without losing attachments."""
    root = _root(user_id)
    with path_lock(root / ".collection", exclusive=True):
        data, directory = _load_session(user_id, session_id)
        if not data or data.get("status") != "active" or _is_expired(data):
            raise ValueError("Aktive, nicht abgelaufene Session erforderlich")
        with path_lock(directory / ".session", exclusive=True):
            data = _read_json(directory / "session.json")
            if not isinstance(data, dict) or data.get("status") != "active" or _is_expired(data):
                raise ValueError("Aktive, nicht abgelaufene Session erforderlich")
            if _media_items(directory):
                raise ValueError("Anhänge können mit diesem Template nicht übernommen werden.")
            now = _now()
            data.update(status="submitted", submitted_at=_iso(now), updated_at=_iso(now))
            if submission_target:
                data["submission_target"] = str(submission_target)[:500]
            _write_json(directory / "session.json", data)
    return data


def media_markdown(items):
    """Render the journal block for one submission's attachments.

    Several voice messages in one entry need headings that say which recording a
    transcript belongs to: the attachment's title wins, otherwise the recordings
    are numbered.  A single recording keeps the plain heading.
    """
    audio_ids = [item.get("id") for item in items if item.get("type") == "audio"]
    lines = []
    for item in items:
        marker = json.dumps({key: item.get(key) for key in (
            "id", "type", "captured_at", "mime_type", "media_path", "sha256", "original_filename",
            "title",
        )}, ensure_ascii=False, separators=(",", ":"))
        label = {"image": "Foto", "audio": "Sprachnachricht", "document": "Dokument"}.get(item.get("type"), "Anhang")
        title = _media_title(item.get("title"))
        label_with_title = f"{label}: {title}" if title else label
        lines.extend([f"<!-- jt:media {marker} -->", f"{label_with_title} | Aufnahmezeit: {(_parse_iso(item.get('captured_at')) or _now()).astimezone(_main().get_tz_aware_now()[1]).isoformat(timespec='seconds')}"])
        # ``transcript_inline``: die Sternchen-Aufnahme hat denselben Text schon
        # in den Editor geschrieben - ein zweiter Block verdoppelte den Eintrag.
        if (
            item.get("type") == "audio" and item.get("transcription_status") == "completed"
            and item.get("transcript_text") and not item.get("transcript_inline")
        ):
            position = audio_ids.index(item.get("id")) + 1 if len(audio_ids) > 1 else None
            lines.extend([
                transcript_blocks.transcript_marker(item.get("id")),
                transcript_blocks.heading(title, position), str(item.get("transcript_text")).strip(),
            ])
    return "\n\n".join(lines)


def apply_media_placeholder(content, media_text):
    """Put final media markers where the editor left its placeholder.

    The replacement is a function so a backslash inside marker JSON can never
    be read as a regex group reference.
    """
    text = str(content or "")
    if not _PLACEHOLDER_RE.search(text):
        return (text.rstrip() + "\n\n" + media_text) if media_text else text
    replacement = "\n\n" + media_text if media_text else ""
    text = _PLACEHOLDER_RE.sub(lambda _match: replacement, text, count=1)
    # A second placeholder can only come from pasted text; it is never content.
    return _PLACEHOLDER_RE.sub("", text)


def attachment_day_dir(user_id, path):
    """Return the day directory whose media folder may hold a file's attachments.

    Attachments are only servable from a `YYYY/MM/DD` folder below the user
    root, so a note outside that tree keeps its media with today's journal.
    """
    root = _user_root(user_id).resolve()
    try:
        relative = Path(path).resolve().parent.relative_to(root).as_posix()
    except (ValueError, OSError):
        relative = ""
    return Path(path).parent if _DAY_DIR_RE.fullmatch(relative) else root / _now().strftime("%Y/%m/%d")


def _final_basename(item):
    captured = _parse_iso(item.get("captured_at")) or _now()
    local = captured.astimezone()
    return f"{local.strftime('%Y-%m-%d_%H-%M-%S')}_{item['id']}"


def _final_suffix(item):
    suffix = Path(str(item.get("original_name") or "")).suffix.lower()
    if not suffix or not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = mimetypes.guess_extension(item.get("mime_type") or "") or ".bin"
    return suffix


def _final_paths(day_dir, item):
    basename = _final_basename(item)
    if item.get("type") == "audio":
        directory = day_dir / "media" / "Sprachi"
        return {
            "media": directory / f"{basename}_audio{_final_suffix(item)}",
            "metadata": directory / f"{basename}_metadata.json",
            "transcript": directory / f"{basename}_transcript.json",
        }
    return {"media": day_dir / "media" / f"{basename}{_final_suffix(item)}"}


def stage_submission_media(user_id, session_id, journal_path, day_dir=None):
    """Move ready media before writing the journal; callers can roll this back.

    ``day_dir`` lets an edit of a source outside the journal tree keep its
    attachments in a day folder they can actually be served from.
    """
    _data, directory, items = submission_media(user_id, session_id)
    day_dir = day_dir or journal_path.parent
    root = _user_root(user_id).resolve()
    moves, staged_items, reserved = [], [], set()
    with path_lock(directory / ".session", exclusive=True):
        for item in items:
            source_dir = directory / "media" / str(item.get("id"))
            source = source_dir / str(item.get("original_name") or "")
            if not source.is_file() or source.is_symlink():
                raise ValueError("Ein Medienanhang ist nicht vollständig vorhanden.")
            targets = _final_paths(day_dir, item)
            target = targets["media"]
            required_targets = list(targets.values())
            if any(path.exists() or path in reserved for path in required_targets):
                raise ValueError("Ein Medienanhang besitzt bereits eine gleichnamige Zieldatei.")
            reserved.update(required_targets)
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = dict(item, media_path=target.relative_to(root).as_posix())
            if item.get("type") == "audio":
                staged.update(
                    metadata_path=targets["metadata"].relative_to(root).as_posix(),
                    transcript_path=targets["transcript"].relative_to(root).as_posix(),
                )
            moves.append((source, target))
            source_transcript = source_dir / "transcript.json"
            if item.get("type") == "audio" and source_transcript.is_file() and not source_transcript.is_symlink():
                moves.append((source_transcript, targets["transcript"]))
            staged_items.append(staged)
        try:
            for source, target in moves:
                os.replace(source, target)
        except OSError as exc:
            for source, target in reversed(moves):
                if target.exists() and not source.exists():
                    os.replace(target, source)
            raise ValueError("Medien konnten nicht sicher in den Journal-Ordner verschoben werden.") from exc
    return {"session_id": session_id, "directory": directory, "journal_path": journal_path,
            "day_dir": day_dir, "items": staged_items, "moves": moves}


def rollback_staged_media(stage):
    """Undo a pre-journal move. A failed rollback must be visible to the caller."""
    failures = []
    for source, target in reversed(stage.get("moves", [])):
        try:
            if target.exists() and not source.exists():
                os.replace(target, source)
        except OSError as exc:
            failures.append(exc)
    if failures:
        raise RuntimeError("Final abgelegte Medien konnten nicht zurückverschoben werden.") from failures[0]


def commit_submission_media(user_id, stage):
    """Persist final metadata beside the journal and remove session-only remnants."""
    directory = stage["directory"]
    journal_path = stage["journal_path"]
    journal_relative = journal_path.relative_to(_user_root(user_id)).as_posix()
    root = _user_root(user_id).resolve()
    with path_lock(directory / ".session", exclusive=True):
        data, current_dir = _load_session(user_id, stage["session_id"])
        if not data or current_dir != directory:
            raise RuntimeError("Session konnte nach dem Speichern nicht abgeschlossen werden.")
        for item in stage["items"]:
            final_item = dict(item, journal_path=journal_relative, storage="journal", finalized_at=_iso(_now()))
            if item.get("type") == "audio":
                metadata_path = (root / str(item["metadata_path"])).resolve()
                metadata_path.relative_to(root)
                _write_json(metadata_path, final_item)
            else:
                # The index belongs beside the media file, which for a note
                # outside the journal tree is not the source's own folder.
                index_dir = (stage.get("day_dir") or journal_path.parent) / ".journal-media"
                index_dir.mkdir(parents=True, exist_ok=True)
                _write_json(index_dir / f"{item['id']}.json", final_item)
            _consume_inbox_attachment(user_id, final_item)
        media_root = directory / "media"
        if media_root.exists():
            shutil.rmtree(media_root)
        data.update(status="submitted", submitted_at=_iso(_now()), journal_path=journal_relative, updated_at=_iso(_now()))
        _write_json(directory / "session.json", data)


def media_from_text(user_id, text):
    items = []
    for raw in re.findall(r"<!--\s*jt:media\s+(\{.*?\})\s*-->", text or ""):
        try:
            marker = json.loads(raw)
            media_id = marker.get("id")
        except (json.JSONDecodeError, AttributeError):
            continue
        final = _final_media_file(user_id, marker.get("media_path"))
        if final:
            items.append({key: marker.get(key) for key in (
                "id", "type", "mime_type", "captured_at", "original_filename", "media_path", "title",
            )})
            continue
        item, _ = _find_media(user_id, media_id)
        if item:
            items.append({key: item.get(key) for key in ("id", "type", "mime_type", "captured_at", "transcription_status", "original_filename", "media_path", "title")})
    return items
