#!/usr/bin/env python3
"""Transcribe Journl audio on the configured schedule or after a manual trigger."""

import argparse
import hashlib
import json
import logging
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
import sys
sys.path.insert(0, str(APP if APP.is_dir() else ROOT))
import transcript_blocks  # noqa: E402
from scheduling import path_lock, read_text_file, update_text_file, write_text_file  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
TZ_NAME = os.environ.get("TZ", "Europe/Berlin")
WHISPER_URL = os.environ.get("WHISPER_URL", "http://whisper:8090").rstrip("/")
WHISPER_API_KEY = os.environ.get("WHISPER_API_KEY", "")
TRANSCRIPTION_HOUR = int(os.environ.get("WHISPER_SCHEDULE_HOUR", "11"))
SCHEDULE_MODES = ("hourly", "daily", "off")
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MEDIA_ID_RE = re.compile(r"^[0-9a-f-]{36}$")


def _now():
    return datetime.now(ZoneInfo(TZ_NAME))


def _read_json(path):
    try:
        return json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path, value):
    write_text_file(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _default_model():
    """Ein Modell aus WHISPER_MODEL; eine versehentliche Liste ergibt den ersten Eintrag."""
    for item in os.environ.get("WHISPER_MODEL", "base").split(","):
        if MODEL_NAME_RE.fullmatch(item.strip()):
            return item.strip()
    return "base"


class ServiceUnavailable(RuntimeError):
    """Der Transkriptionsdienst nimmt gar keine Verbindung an (z. B. noch im Start)."""


def _multipart(file_path, model=None):
    boundary = "----journl-" + uuid.uuid4().hex
    parts = []
    def field(name, value):
        parts.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode(), b"\r\n"])
    field("model", model or _default_model())
    field("language", os.environ.get("WHISPER_LANGUAGE", "de"))
    field("response_format", "verbose_json")
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(), file_path.read_bytes(), b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return boundary, b"".join(parts)


def _call_whisper(file_path, model=None):
    if not WHISPER_API_KEY:
        raise RuntimeError("WHISPER_API_KEY is not configured")
    boundary, body = _multipart(file_path, model)
    request = urllib.request.Request(
        WHISPER_URL + "/v1/audio/transcriptions", data=body, method="POST",
        headers={"Authorization": "Bearer " + WHISPER_API_KEY, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=21600) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        # Keine Antwort, keine Verbindung: das ist kein Fehler der Aufnahme.
        raise ServiceUnavailable(str(exc.reason)) from exc


def _call_voice(file_path):
    """Aufnahmen des Sternchen-Mikrofons (``engine: "voice"``) ueber den lokalen
    KI-Server (MacMini) statt ueber Whisper - derselbe Anbieter und dasselbe
    ASR-Modell wie im Schreiben-Tab. Die Originale sind bereits WAV.

    Gelesen wird die rohe config.json; den ``{locale_LLM_IP}``-Platzhalter
    ersetzt hier die Umgebung, wie es ``main.load_config`` in der App tut.
    """
    import voice

    config = voice._raw_config(DATA_DIR / "config.json")
    local_ip = os.environ.get("locale_LLM_IP", "").strip()
    for provider in config.get("ai_providers") or []:
        if isinstance(provider, dict) and local_ip and isinstance(provider.get("api_url"), str):
            provider["api_url"] = provider["api_url"].replace("{locale_LLM_IP}", local_ip)
    settings = voice._clean_settings(config.get("voice"))
    provider = voice.resolve_provider(config, settings["provider_id"])
    if provider is None:
        raise RuntimeError("Kein lokaler Voice-Anbieter für Sternchen-Aufnahmen konfiguriert")
    text = voice.transcribe(file_path, voice.provider_base_url(provider.get("api_url")), settings["asr_model"],
                            language=os.environ.get("WHISPER_LANGUAGE", "de"))
    return {"text": text, "engine": "voice", "provider_id": provider.get("id"), "model": settings["asr_model"]}


def _apply_to_journal(user_id, item, transcript):
    relative = item.get("journal_path")
    if not relative:
        return
    root = (DATA_DIR / user_id).resolve()
    journal = (root / relative).resolve()
    try:
        journal.relative_to(root)
    except ValueError:
        return
    if not journal.is_file() or journal.is_symlink():
        return
    def update(current):
        # A re-run replaces the block it finds instead of adding a second one,
        # which is what makes retrying a weak model with a better one possible.
        updated, changed = transcript_blocks.upsert(
            current, item["id"], transcript.get("text", ""), item.get("title"),
        )
        if not changed:
            return current
        backup = journal.parent / "_Backup" / f"{journal.stem}_backup_{uuid.uuid4().hex[:8]}{journal.suffix}.bak"
        write_text_file(backup, current)
        return updated
    update_text_file(journal, update)
    request_dir = DATA_DIR / "indexes" / "brain_rebuild_requests"
    request_file = request_dir / f"{hashlib.sha256(user_id.encode('utf-8')).hexdigest()}.json"
    _write_json(request_file, {"request_key": user_id})


def pending(user_filter=None, media_filter=None):
    locations = list(DATA_DIR.glob("*/*/*/*/media/Sprachi/*_metadata.json"))
    locations.extend(DATA_DIR.glob("*/*/*/*/.journal-media/*.json"))
    for metadata in locations:
        item = _read_json(metadata)
        if not isinstance(item, dict) or item.get("type") != "audio" or item.get("status") != "ready":
            continue
        if user_filter and item.get("user_id") != user_filter:
            continue
        # An explicitly requested recording is transcribed again even though it
        # already carries a transcript; that is the whole point of the request.
        if media_filter:
            if item.get("id") != media_filter:
                continue
        elif item.get("transcription_status") == "completed":
            continue
        yield metadata, item


def _original_path(metadata, item):
    relative = item.get("media_path")
    if relative:
        root = (DATA_DIR / str(item.get("user_id") or "")).resolve()
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        return path if path.is_file() and not path.is_symlink() else None
    path = metadata.parent / str(item.get("original_name") or "")
    return path if path.is_file() and not path.is_symlink() else None


def _transcript_path(metadata, item):
    relative = item.get("transcript_path")
    if relative:
        root = (DATA_DIR / str(item.get("user_id") or "")).resolve()
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        return path
    return metadata.with_name(f"{item.get('id')}.transcript.json")


def run(user_filter=None, model=None, media_filter=None):
    completed = errors = 0
    for metadata, item in pending(user_filter, media_filter):
        with path_lock(metadata.parent / ".transcription", exclusive=True):
            current = _read_json(metadata)
            if not media_filter and current.get("transcription_status") == "completed":
                continue
            original = _original_path(metadata, current)
            if not original:
                continue
            previous_status = current.get("transcription_status")
            current.update(transcription_status="running", transcription_started_at=_now().isoformat(timespec="seconds"))
            _write_json(metadata, current)
            try:
                # Sternchen-Mikrofon: immer MacMini; altes Mikrofon: Whisper.
                transcript = _call_voice(original) if current.get("engine") == "voice" else _call_whisper(original, model)
                transcript_path = _transcript_path(metadata, current)
                if not transcript_path:
                    raise RuntimeError("Invalid transcript path")
                _write_json(transcript_path, transcript)
                current.update(transcription_status="completed", transcription_completed_at=_now().isoformat(timespec="seconds"), transcript_text=transcript.get("text", ""))
                _write_json(metadata, current)
                _apply_to_journal(current["user_id"], current, transcript)
                completed += 1
            except ServiceUnavailable as exc:
                # Dienst noch nicht oben (Startwettlauf) oder weg: Aufnahme
                # unveraendert lassen und den Lauf beenden, statt alle
                # restlichen als Fehler zu markieren. Der naechste Lauf holt nach.
                logger.warning("Transcription service unavailable (%s); run stopped, %s stays %s",
                               exc, current.get("id"), previous_status)
                current.update(transcription_status=previous_status or "pending")
                current.pop("transcription_started_at", None)
                _write_json(metadata, current)
                return {"completed": completed, "errors": errors, "unavailable": True}
            except Exception as exc:
                logger.exception("Transcription failed for %s", current.get("id"))
                current.update(transcription_status="error", transcription_error=str(exc)[:500])
                _write_json(metadata, current)
                errors += 1
    return {"completed": completed, "errors": errors}


def _manual_triggers():
    root = DATA_DIR / "whisper_jobs" / "manual"
    try:
        if not root.is_dir():
            return []
        triggers = list(root.glob("*.json"))
    except OSError as exc:
        logger.warning("Cannot read manual transcription triggers in %s: %s", root, exc)
        return []
    requests = []
    for trigger in triggers:
        data = _read_json(trigger)
        if isinstance(data, dict) and data.get("user_id"):
            requests.append(data)
        try:
            trigger.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Cannot remove manual transcription trigger %s: %s", trigger, exc)
    return requests


def _requested_media(request_data):
    """Limit a manual run to the one recording the UI asked to redo."""
    media_id = request_data.get("media_id")
    if isinstance(media_id, str) and MEDIA_ID_RE.fullmatch(media_id.strip()):
        return media_id.strip()
    return None


def _requested_model(request_data):
    """Honour the quality step chosen in the UI for this manual run only."""
    model = request_data.get("model")
    if isinstance(model, str) and MODEL_NAME_RE.fullmatch(model.strip()):
        return model.strip()
    return None


def _manual_status(request_data, status, **details):
    user_id = request_data["user_id"]
    value = {
        "user_id": user_id,
        "status": status,
        "requested_at": request_data.get("requested_at"),
        **details,
    }
    _write_json(DATA_DIR / "whisper_jobs" / "status" / f"{user_id}.json", value)


def _schedule():
    """Read the schedule the app writes; the deployed default stays the fallback.

    The file is the only knob that may change at runtime, so it is read on every
    pass instead of at start-up - a change in the settings takes effect within a
    minute and does not need a container restart.
    """
    stored = _read_json(DATA_DIR / "whisper_jobs" / "schedule.json")
    mode, hour = "daily", TRANSCRIPTION_HOUR
    if isinstance(stored, dict):
        if stored.get("mode") in SCHEDULE_MODES:
            mode = stored["mode"]
        if isinstance(stored.get("hour"), int) and 0 <= stored["hour"] <= 23:
            hour = stored["hour"]
    return mode, hour


def _due_slot(mode, hour, now):
    """Return the identifier of the run that is due now, or None."""
    if mode == "hourly":
        return now.strftime("%Y-%m-%dT%H")
    if mode == "daily" and now.hour >= hour:
        return now.date().isoformat()
    return None


def loop():
    last_slot = None
    while True:
        now = _now()
        mode, hour = _schedule()
        slot = _due_slot(mode, hour, now)
        if slot and slot != last_slot:
            logger.info("Starting scheduled transcription run (%s)", mode)
            logger.info("Scheduled result: %s", run())
            last_slot = slot
        for request_data in _manual_triggers():
            user_id = request_data["user_id"]
            _manual_status(request_data, "running", started_at=_now().isoformat(timespec="seconds"))
            result = run(user_id, _requested_model(request_data), _requested_media(request_data))
            _manual_status(
                request_data,
                "completed",
                completed_at=_now().isoformat(timespec="seconds"),
                completed=result["completed"],
                errors=result["errors"],
            )
            logger.info("Manual transcription result for %s: %s", user_id, result)
        time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.loop:
        loop()
    else:
        print(json.dumps(run(), ensure_ascii=False))
