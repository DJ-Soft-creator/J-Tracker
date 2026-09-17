"""Prepare private writing-session attachments for explicit AI requests."""

import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import jsonify, request

import write_sessions
from scheduling import path_lock, write_text_file


MAX_ATTACHMENTS = 12
MAX_AI_AUDIO_BYTES = 80 * 1024 * 1024
MAX_AI_TEXT_BYTES = 1 * 1024 * 1024
_SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
_AI_IMAGE_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_AI_TEXT_MIMES = {"text/plain", "text/markdown", "text/csv"}


class AiAttachmentError(ValueError):
    """The selected attachments cannot safely be supplied to the AI request."""


def _snapshot_dir(data_root, user_id, snapshot_id):
    if not isinstance(user_id, str) or not user_id or Path(user_id).name != user_id:
        raise AiAttachmentError("Ungültiger Benutzer für KI-Anhänge.")
    if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id or ""):
        raise AiAttachmentError("Ungültiger KI-Anhang-Snapshot.")
    return Path(data_root) / user_id / "ai_jobs" / "attachments" / snapshot_id


def cleanup_snapshot(data_root, user_id, snapshot_id):
    """Remove only one known per-request snapshot directory."""
    directory = _snapshot_dir(data_root, user_id, snapshot_id)
    if directory.is_dir() and not directory.is_symlink():
        shutil.rmtree(directory)
    try:
        directory.parent.rmdir()
    except OSError:
        pass


def _source_file(directory, item):
    media_dir = directory / "media" / str(item.get("id") or "")
    name = item.get("original_name")
    if not isinstance(name, str) or Path(name).name != name:
        raise AiAttachmentError("Ein KI-Anhang besitzt keinen gültigen Dateinamen.")
    source = media_dir / name
    if not source.is_file() or source.is_symlink():
        raise AiAttachmentError("Ein KI-Anhang ist nicht vollständig vorhanden.")
    try:
        source.resolve().relative_to((directory / "media").resolve())
    except ValueError as exc:
        raise AiAttachmentError("Ein KI-Anhang liegt außerhalb der Schreibsession.") from exc
    expected = item.get("sha256")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if not isinstance(expected, str) or actual != expected:
        raise AiAttachmentError("Ein KI-Anhang hat sich seit dem Upload verändert.")
    return source


_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MODEL_CATALOG_CACHE = {"fetched_at": 0.0, "value": None}


def whisper_model(requested=None):
    """Pick the transcription quality step, falling back to the configured default."""
    default = os.environ.get("WHISPER_MODEL", "base")
    if isinstance(requested, str) and _MODEL_NAME_RE.fullmatch(requested.strip()):
        return requested.strip()
    return default


def whisper_models(whisper_url=None):
    """Read the quality steps the local Whisper service offers, cached briefly.

    The service stays authoritative; if it is unreachable the UI still gets the
    configured default so the picker never disappears entirely.
    """
    now = time.monotonic()
    if _MODEL_CATALOG_CACHE["value"] and now - _MODEL_CATALOG_CACHE["fetched_at"] < 300:
        return _MODEL_CATALOG_CACHE["value"]
    default = os.environ.get("WHISPER_MODEL", "base")
    base = (whisper_url or os.environ.get("WHISPER_URL", "http://whisper:8090")).rstrip("/")
    try:
        with urllib.request.urlopen(base + "/v1/models", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = [item for item in payload.get("models", []) if isinstance(item, dict) and item.get("id")]
        if not models:
            raise ValueError("empty model catalogue")
        value = {"default": payload.get("default") or default, "models": models, "available": True}
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        value = {"default": default, "models": [{"id": default, "label": default, "note": ""}], "available": False}
    _MODEL_CATALOG_CACHE.update(fetched_at=now, value=value)
    return value


def _multipart_audio(file_path, model=None):
    boundary = "----journl-ai-" + uuid.uuid4().hex
    language = os.environ.get("WHISPER_LANGUAGE", "de")
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts = []

    def field(name, value):
        parts.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode(), b"\r\n",
        ])

    field("model", whisper_model(model))
    field("language", language)
    field("response_format", "verbose_json")
    parts.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(), file_path.read_bytes(), b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return boundary, b"".join(parts)


def _transcribe(file_path, whisper_url, api_key, model=None):
    if not api_key:
        raise AiAttachmentError("Sprachnachrichten benötigen den lokalen Whisper-Dienst.")
    boundary, body = _multipart_audio(file_path, model)
    request = urllib.request.Request(
        whisper_url.rstrip("/") + "/v1/audio/transcriptions", data=body, method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        timeout = max(30, min(1800, int(os.environ.get("WHISPER_AI_TIMEOUT", "300"))))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            transcript = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise AiAttachmentError(
            "Sprachnachricht konnte nicht für die KI transkribiert werden. "
            "Der lokale Whisper-Dienst ist nicht erreichbar oder hat einen Fehler gemeldet."
        ) from exc
    text = transcript.get("text") if isinstance(transcript, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise AiAttachmentError("Die Sprachnachricht enthält kein verwertbares Transkript.")
    return transcript, text.strip()


def _persist_transcript(directory, item, transcript, text):
    media_dir = directory / "media" / str(item["id"])
    with path_lock(directory / ".session", exclusive=True):
        current = write_sessions._read_json(media_dir / "metadata.json")
        if not isinstance(current, dict) or current.get("sha256") != item.get("sha256"):
            raise AiAttachmentError("Die Sprachnachricht wurde während der KI-Anfrage geändert.")
        write_text_file(
            media_dir / "transcript.json",
            json.dumps(transcript, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        completed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        current.update(
            transcription_status="completed", transcript_text=text,
            transcription_completed_at=completed_at, updated_at=completed_at,
        )
        write_sessions._write_json(media_dir / "metadata.json", current)


def prepare_session_attachments(data_root, user_id, session_id, submitted_text, *,
                                whisper_url=None, api_key=None, snapshot_id=None,
                                whisper_model_name=None):
    """Freeze image inputs and turn voice messages into explicit text context.

    The source session remains active. Only the immutable AI snapshot is handed
    to a queued worker, so later attachment removal cannot change an in-flight
    request.
    """
    if not session_id:
        return {"snapshot_id": None, "images": [], "context": "", "count": 0}
    data, directory = write_sessions._load_session(user_id, session_id)
    if not data or data.get("status") != "active" or write_sessions._is_expired(data):
        raise AiAttachmentError("Aktive, nicht abgelaufene Schreibsession erforderlich.")
    if data.get("content", "") != submitted_text:
        raise AiAttachmentError("Der Session-Stand stimmt nicht mit der KI-Anfrage überein.")
    items = write_sessions._media_items(directory)
    if any(item.get("status") != "ready" for item in items):
        raise AiAttachmentError("Eine Medienaufnahme wird noch gesichert.")
    if len(items) > MAX_ATTACHMENTS:
        raise AiAttachmentError(f"Für eine KI-Anfrage sind höchstens {MAX_ATTACHMENTS} Anhänge erlaubt.")
    if not items:
        return {"snapshot_id": None, "images": [], "context": "", "count": 0}

    snapshot_id = snapshot_id or str(uuid.uuid4())
    snapshot_dir = _snapshot_dir(data_root, user_id, snapshot_id)
    snapshot_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    copied = []
    try:
        with path_lock(directory / ".session", exclusive=True):
            for index, item in enumerate(items, start=1):
                source = _source_file(directory, item)
                copied_item = dict(item)
                if item.get("type") == "image" and item.get("mime_type") not in _AI_IMAGE_MIMES:
                    preview = directory / "media" / str(item["id"]) / str(item.get("preview_name") or "")
                    if not preview.is_file() or preview.is_symlink():
                        label = write_sessions._media_title(item.get("title")) or "Bild"
                        raise AiAttachmentError(f"Das Bildformat von „{label}“ kann die KI nicht lesen.")
                    source = preview
                    copied_item["ai_mime_type"] = "image/jpeg"
                suffix = source.suffix.lower() if re.fullmatch(r"\.[a-z0-9]{1,10}", source.suffix.lower()) else ".bin"
                target = snapshot_dir / f"source-{index}{suffix}"
                shutil.copyfile(source, target)
                copied.append((copied_item, target))

        images, context_parts = [], []
        for index, (item, copied_file) in enumerate(copied, start=1):
            kind = item.get("type")
            title = write_sessions._media_title(item.get("title"))
            label = title or {"image": "Bild", "audio": "Sprachnachricht", "document": "Dokument"}.get(kind, "Anhang")
            if kind == "image":
                mime = item.get("ai_mime_type") or item.get("mime_type")
                target = snapshot_dir / f"image-{index}{_AI_IMAGE_MIMES[mime]}"
                os.replace(copied_file, target)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                images.append({
                    "type": "image", "label": label, "mime_type": mime,
                    "path": target.relative_to(Path(data_root)).as_posix(),
                    "sha256": digest, "byte_size": target.stat().st_size,
                })
                context_parts.append(f"Bild {index}: {label} (als visueller KI-Anhang beigefügt)")
            elif kind == "audio":
                if copied_file.stat().st_size > MAX_AI_AUDIO_BYTES:
                    raise AiAttachmentError(f"Die Sprachnachricht „{label}“ ist für eine KI-Anfrage zu groß.")
                text = item.get("transcript_text") if item.get("transcription_status") == "completed" else None
                if not isinstance(text, str) or not text.strip():
                    transcript, text = _transcribe(
                        copied_file,
                        whisper_url or os.environ.get("WHISPER_URL", "http://whisper:8090"),
                        api_key if api_key is not None else os.environ.get("WHISPER_API_KEY", ""),
                        whisper_model(whisper_model_name),
                    )
                    _persist_transcript(directory, item, transcript, text)
                context_parts.append(f"Sprachnachricht {index} – {label} (Transkript):\n{text.strip()}")
                copied_file.unlink(missing_ok=True)
            elif kind == "document" and item.get("mime_type") in _AI_TEXT_MIMES:
                raw = copied_file.read_bytes()
                if len(raw) > MAX_AI_TEXT_BYTES:
                    raise AiAttachmentError(f"Das Textdokument „{label}“ ist für eine KI-Anfrage zu groß.")
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise AiAttachmentError(f"Das Textdokument „{label}“ ist nicht UTF-8-kodiert.") from exc
                context_parts.append(f"Dokument {index} – {label} (Referenzmaterial):\n{content}")
                copied_file.unlink(missing_ok=True)
            else:
                raise AiAttachmentError(
                    f"Der Anhang „{label}“ kann in diesem KI-Modus noch nicht gelesen werden. "
                    "Unterstützt werden Bilder, Sprachnachrichten sowie TXT-, Markdown- und CSV-Dateien."
                )

        if not images:
            snapshot_dir.rmdir()
            try:
                snapshot_dir.parent.rmdir()
            except OSError:
                pass
            snapshot_id = None
        context = ""
        if context_parts:
            context = "## Vom Benutzer ausdrücklich beigefügte Anhänge\n" + "\n\n".join(context_parts)
        return {"snapshot_id": snapshot_id, "images": images, "context": context, "count": len(items)}
    except Exception:
        if snapshot_dir.is_dir() and not snapshot_dir.is_symlink():
            shutil.rmtree(snapshot_dir)
        try:
            snapshot_dir.parent.rmdir()
        except OSError:
            pass
        raise


def image_content_blocks(data_root, images):
    """Build OpenAI-compatible image blocks after rechecking frozen hashes."""
    import base64

    blocks = []
    root = Path(data_root).resolve()
    for item in images or []:
        path = (root / str(item.get("path") or "")).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise AiAttachmentError("KI-Bild liegt außerhalb des Datenbereichs.") from exc
        raw = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
        if not raw or hashlib.sha256(raw).hexdigest() != item.get("sha256"):
            raise AiAttachmentError("KI-Bild-Snapshot ist nicht mehr unverändert vorhanden.")
        mime = item.get("mime_type")
        if mime not in _AI_IMAGE_MIMES:
            raise AiAttachmentError("KI-Bild besitzt ein nicht unterstütztes Format.")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"},
        })
    return blocks


@write_sessions.write_sessions_bp.route("/media/<media_id>/transcribe", methods=["POST"])
def transcribe_media_now(media_id):
    """Transcribe one still-unsent voice message right away, instead of waiting
    for the daily whisper run or a global manual trigger to reach it."""
    user, error = write_sessions._require_user()
    if error:
        return error
    csrf = write_sessions._csrf_error()
    if csrf:
        return csrf
    if not write_sessions._ID_RE.fullmatch(media_id or ""):
        return jsonify({"error": "Sprachnachricht nicht gefunden"}), 404
    model = whisper_model((request.get_json(silent=True) or {}).get("model"))
    media_dir = next((path for path in write_sessions._root(user["id"]).glob(f"*/media/{media_id}") if path.is_dir()), None)
    item = write_sessions._read_json(media_dir / "metadata.json") if media_dir else None
    if not item or item.get("user_id") != user["id"] or item.get("type") != "audio":
        return jsonify({"error": "Sprachnachricht nicht gefunden"}), 404
    directory = media_dir.parent.parent
    data, _ = write_sessions._load_session(user["id"], item.get("session_id") or "")
    if not data or data.get("status") != "active" or write_sessions._is_expired(data):
        return jsonify({"error": "Aktive Session erforderlich"}), 409
    if item.get("status") != "ready":
        return jsonify({"error": "Aufnahme wird noch gesichert"}), 409
    if item.get("transcription_status") == "completed":
        return jsonify(item)
    name = item.get("original_name")
    original = media_dir / name if isinstance(name, str) and Path(name).name == name else None
    if not original or not original.is_file() or original.is_symlink():
        return jsonify({"error": "Audiodatei fehlt"}), 404
    with path_lock(directory / ".session", exclusive=True):
        current = write_sessions._read_json(media_dir / "metadata.json")
        if not isinstance(current, dict) or current.get("transcription_status") == "running":
            return jsonify({"error": "Transkription läuft bereits"}), 409
        current.update(transcription_status="running", updated_at=write_sessions._iso(write_sessions._now()))
        write_sessions._write_json(media_dir / "metadata.json", current)
    try:
        transcript, text = _transcribe(
            original, os.environ.get("WHISPER_URL", "http://whisper:8090"), os.environ.get("WHISPER_API_KEY", ""),
            model,
        )
        _persist_transcript(directory, item, transcript, text)
    except AiAttachmentError as exc:
        with path_lock(directory / ".session", exclusive=True):
            current = write_sessions._read_json(media_dir / "metadata.json") or item
            current.update(transcription_status="error", transcription_error=str(exc)[:500], updated_at=write_sessions._iso(write_sessions._now()))
            write_sessions._write_json(media_dir / "metadata.json", current)
        return jsonify({"error": str(exc)}), 502
    return jsonify(write_sessions._read_json(media_dir / "metadata.json"))


@write_sessions.write_sessions_bp.route("/transcriptions/models", methods=["GET"])
def transcription_models():
    """Offer the quality steps every transcription control in the UI can pick."""
    _, error = write_sessions._require_user()
    if error:
        return error
    response = jsonify(whisper_models())
    response.headers["Cache-Control"] = "no-store"
    return response
