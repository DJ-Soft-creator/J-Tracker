"""Token-protected screenshot uploads waiting to be attached to a write session."""

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import sys
import uuid
import warnings
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from PIL import Image, ImageOps

from scheduling import path_lock, read_text_file, write_text_file
import write_sessions


screenshot_inbox_bp = Blueprint("screenshot_inbox", __name__, url_prefix="/api/screenshot-inbox")
MAX_SCREENSHOT_BYTES = 40 * 1024 * 1024
_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _now():
    main = _main()
    return main.get_tz_aware_now()[0] if main else datetime.now().astimezone()


def _iso(value):
    return value.isoformat(timespec="seconds")


def _user_root(user_id):
    return _main().DATA_DIR / user_id


def _token_path(user_id):
    return _user_root(user_id) / ".config" / "screenshot-upload.json"


def _read_json(path, default=None):
    if not path.is_file() or path.is_symlink():
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
    if not separator or scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _upload_user():
    token = _bearer_token()
    if not token or len(token) > 256:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    users = _main()._read_users_file().get("users", [])
    for user in users:
        user_id = user.get("id")
        config = _read_json(_token_path(user_id), {}) if user_id else {}
        stored = config.get("token_sha256") if isinstance(config, dict) else None
        if isinstance(stored, str) and hmac.compare_digest(stored, digest):
            config["last_used_at"] = _iso(_now())
            _write_json(_token_path(user_id), config)
            return user
    return None


def _image_payload():
    upload = request.files.get("file")
    if upload:
        raw = upload.read(MAX_SCREENSHOT_BYTES + 1)
        filename = Path(upload.filename or "Screenshot").name[:255]
        captured_at = request.form.get("captured_at")
    else:
        raw = request.stream.read(MAX_SCREENSHOT_BYTES + 1)
        filename = Path(request.headers.get("X-Filename") or "Screenshot").name[:255]
        captured_at = request.headers.get("X-Captured-At")
    if not raw:
        raise ValueError("Kein Screenshot übertragen")
    if len(raw) > MAX_SCREENSHOT_BYTES:
        raise OverflowError("Screenshot ist zu groß")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                image_format = image.format
                image.verify()
    except Exception as exc:
        raise ValueError("Datei ist kein unterstützter Screenshot") from exc
    if image_format not in _FORMATS:
        raise ValueError("Nur PNG, JPEG und WebP werden unterstützt")
    return raw, filename, captured_at, _FORMATS[image_format]


def _safe_captured_at(value, fallback):
    try:
        parsed = datetime.fromisoformat(str(value))
        if not parsed.tzinfo:
            parsed = parsed.replace(tzinfo=fallback.tzinfo)
        return _iso(parsed)
    except (TypeError, ValueError):
        return _iso(fallback)


def _atomic_bytes(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o660)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _create_preview(raw, path):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            preview = ImageOps.exif_transpose(image)
            preview.thumbnail((1200, 1200))
            if preview.mode not in {"RGB", "L"}:
                preview = preview.convert("RGB")
            buffer = io.BytesIO()
            preview.save(buffer, "JPEG", quality=82, optimize=True)
        _atomic_bytes(path, buffer.getvalue())
        return True
    except Exception:
        return False


def _inbox_paths(user_id, now, upload_id, extension):
    directory = _user_root(user_id) / now.strftime("%Y/%m/%d") / "media" / "Screenshot-Inbox"
    basename = f"{now.strftime('%Y-%m-%d_%H-%M-%S')}_{upload_id}"
    return directory / f"{basename}{extension}", directory / f"{basename}_preview.jpg", directory / f"{basename}.json"


def _metadata_paths(user_id):
    root = _user_root(user_id)
    return root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/media/Screenshot-Inbox/*.json")


def _validated_path(user_id, relative):
    if not isinstance(relative, str):
        return None
    root = _user_root(user_id).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if not path.is_symlink() else None


def _reconcile_reservation(user_id, item, metadata_path):
    if item.get("status") != "attached":
        return item
    session_id = item.get("attached_session_id")
    session, directory = write_sessions._load_session(user_id, session_id)
    attachment = directory / "media" / str(item.get("id")) / "metadata.json" if directory else None
    if session and session.get("status") == "active" and attachment and attachment.is_file():
        return item
    item.update(status="pending", updated_at=_iso(_now()))
    item.pop("attached_session_id", None)
    _write_json(metadata_path, item)
    return item


def _find_item(user_id, upload_id):
    if not _ID_RE.fullmatch(upload_id or ""):
        return None, None
    for path in _metadata_paths(user_id):
        item = _read_json(path)
        if isinstance(item, dict) and item.get("id") == upload_id and item.get("user_id") == user_id:
            return _reconcile_reservation(user_id, item, path), path
    return None, None


def _public_item(item):
    return {key: item.get(key) for key in (
        "id", "received_at", "captured_at", "original_filename", "date", "mime_type", "size",
    )}


@screenshot_inbox_bp.route("/token", methods=["GET", "POST", "DELETE"])
def upload_token():
    user, error = _browser_user()
    if error:
        return error
    path = _token_path(user["id"])
    current = _read_json(path, {})
    if request.method == "GET":
        response = jsonify({
            "enabled": bool(current.get("token_sha256")),
            "created_at": current.get("created_at"),
            "last_used_at": current.get("last_used_at"),
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    csrf = _csrf_error()
    if csrf:
        return csrf
    if request.method == "DELETE":
        path.unlink(missing_ok=True)
        return jsonify({"ok": True, "enabled": False})
    token = "jti_" + secrets.token_urlsafe(32)
    now = _iso(_now())
    _write_json(path, {
        "schema": 1,
        "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "created_at": now,
        "last_used_at": None,
    })
    response = jsonify({"ok": True, "enabled": True, "token": token, "created_at": now})
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@screenshot_inbox_bp.route("/upload", methods=["POST"])
def external_upload():
    user = _upload_user()
    if not user:
        response = jsonify({"error": "Ungültiger Upload-Schlüssel"})
        response.headers["WWW-Authenticate"] = 'Bearer realm="Journl Screenshot Upload"'
        return response, 401
    if request.content_length and request.content_length > MAX_SCREENSHOT_BYTES + (1024 * 1024):
        return jsonify({"error": "Screenshot ist zu groß"}), 413
    try:
        raw, original_filename, captured_at, (mime_type, extension) = _image_payload()
    except OverflowError as exc:
        return jsonify({"error": str(exc)}), 413
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    now = _now()
    upload_id = str(uuid.uuid4())
    original, preview, metadata = _inbox_paths(user["id"], now, upload_id, extension)
    _atomic_bytes(original, raw)
    has_preview = _create_preview(raw, preview)
    root = _user_root(user["id"])
    item = {
        "schema": 1,
        "id": upload_id,
        "user_id": user["id"],
        "type": "image",
        "status": "pending",
        "received_at": _iso(now),
        "captured_at": _safe_captured_at(captured_at, now),
        "date": now.strftime("%Y-%m-%d"),
        "mime_type": mime_type,
        "original_filename": original_filename or "Screenshot",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source_path": original.relative_to(root).as_posix(),
        "preview_path": preview.relative_to(root).as_posix() if has_preview else None,
    }
    try:
        _write_json(metadata, item)
    except Exception:
        original.unlink(missing_ok=True)
        preview.unlink(missing_ok=True)
        raise
    response = jsonify({"ok": True, **_public_item(item)})
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@screenshot_inbox_bp.route("/items", methods=["GET"])
def inbox_items():
    user, error = _browser_user()
    if error:
        return error
    requested_date = request.args.get("date") or _now().strftime("%Y-%m-%d")
    if requested_date != "all":
        try:
            datetime.strptime(requested_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            return jsonify({"error": "Ungültiges Datum"}), 400
    today = _now().strftime("%Y-%m-%d")
    pending = []
    for path in _metadata_paths(user["id"]):
        item = _read_json(path)
        if not isinstance(item, dict) or item.get("user_id") != user["id"]:
            continue
        item = _reconcile_reservation(user["id"], item, path)
        if item.get("status") == "pending":
            pending.append(item)
    pending.sort(key=lambda item: item.get("received_at") or "", reverse=True)
    selected = pending if requested_date == "all" else [item for item in pending if item.get("date") == requested_date]
    response = jsonify({
        "items": [_public_item(item) for item in selected[:100]],
        "today": today,
        "selected_date": requested_date,
        "today_count": sum(item.get("date") == today for item in pending),
        "total_count": len(pending),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@screenshot_inbox_bp.route("/items/<upload_id>/preview", methods=["GET"])
def inbox_preview(upload_id):
    user, error = _browser_user()
    if error:
        return error
    item, _ = _find_item(user["id"], upload_id)
    path = _validated_path(user["id"], item.get("preview_path") or item.get("source_path")) if item else None
    if not path or not path.is_file():
        return jsonify({"error": "Screenshot nicht gefunden"}), 404
    response = send_file(path, mimetype="image/jpeg" if item.get("preview_path") else item.get("mime_type"), conditional=True)
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


@screenshot_inbox_bp.route("/items/<upload_id>", methods=["DELETE"])
def delete_item(upload_id):
    """Delete an unlinked inbox upload; attached items must be released via the session."""
    user, error = _browser_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    item, metadata_path = _find_item(user["id"], upload_id)
    if not item or not metadata_path:
        return jsonify({"error": "Screenshot nicht gefunden"}), 404
    with path_lock(metadata_path.with_suffix(".claim"), exclusive=True):
        item = _read_json(metadata_path)
        if not isinstance(item, dict) or item.get("status") != "pending":
            return jsonify({"error": "Screenshot wurde bereits übernommen"}), 409
        source = _validated_path(user["id"], item.get("source_path"))
        preview = _validated_path(user["id"], item.get("preview_path"))
        if source and source.parent != metadata_path.parent:
            source = None
        if preview and preview.parent != metadata_path.parent:
            preview = None
        metadata_path.unlink(missing_ok=True)
        if source:
            source.unlink(missing_ok=True)
        if preview:
            preview.unlink(missing_ok=True)
    return jsonify({"ok": True, "id": upload_id})


@screenshot_inbox_bp.route("/items/<upload_id>/attach", methods=["POST"])
def attach_item(upload_id):
    user, error = _browser_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf:
        return csrf
    session_id = (request.get_json(silent=True) or {}).get("session_id")
    if not write_sessions._ID_RE.fullmatch(session_id or ""):
        return jsonify({"error": "Ungültige Schreib-Session"}), 400
    item, metadata_path = _find_item(user["id"], upload_id)
    if not item or not metadata_path:
        return jsonify({"error": "Screenshot nicht gefunden"}), 404
    # Use a sibling claim lock; JSON reads/writes already lock the metadata
    # path itself and flock locks must not recursively acquire the same file.
    with path_lock(metadata_path.with_suffix(".claim"), exclusive=True):
        item = _read_json(metadata_path)
        if not isinstance(item, dict) or item.get("status") != "pending":
            return jsonify({"error": "Screenshot wurde bereits übernommen"}), 409
        source = _validated_path(user["id"], item.get("source_path"))
        preview = _validated_path(user["id"], item.get("preview_path"))
        session, directory = write_sessions._load_session(user["id"], session_id)
        if not session or session.get("status") != "active" or write_sessions._is_expired(session):
            return jsonify({"error": "Aktive Schreib-Session erforderlich"}), 409
        expected_fragment = f"_{upload_id}"
        if (
            not source or not source.is_file() or source.parent != metadata_path.parent
            or expected_fragment not in source.name
            or (preview and (preview.parent != metadata_path.parent or expected_fragment not in preview.name))
        ):
            return jsonify({"error": "Screenshot-Datei fehlt"}), 409
        media_dir = directory / "media" / upload_id
        with path_lock(directory / ".session", exclusive=True):
            if media_dir.exists():
                return jsonify({"error": "Screenshot wurde bereits übernommen"}), 409
            media_dir.mkdir(parents=True)
            try:
                original_name = "original" + Path(item["source_path"]).suffix.lower()
                shutil.copy2(source, media_dir / original_name)
                preview_name = None
                if preview and preview.is_file():
                    shutil.copy2(preview, media_dir / "preview.jpg")
                    preview_name = "preview.jpg"
                attachment = {
                    "schema": 1,
                    "id": upload_id,
                    "session_id": session_id,
                    "user_id": user["id"],
                    "type": "image",
                    "mime_type": item["mime_type"],
                    "captured_at": item["captured_at"],
                    "received_at": item["received_at"],
                    "status": "ready",
                    "transcription_status": "not_applicable",
                    "original_name": original_name,
                    "original_filename": item.get("original_filename"),
                    "preview_name": preview_name,
                    "size": item["size"],
                    "sha256": item["sha256"],
                    "inbox_metadata_path": metadata_path.relative_to(_user_root(user["id"])).as_posix(),
                }
                write_sessions._write_json(media_dir / "metadata.json", attachment)
                session.update(updated_at=write_sessions._iso(write_sessions._now()))
                write_sessions._write_json(directory / "session.json", session)
                item.update(status="attached", attached_session_id=session_id, updated_at=_iso(_now()))
                _write_json(metadata_path, item)
            except Exception:
                shutil.rmtree(media_dir, ignore_errors=True)
                raise
    return jsonify(write_sessions._public_session(session, directory, include_content=True))
