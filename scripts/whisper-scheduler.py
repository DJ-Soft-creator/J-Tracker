#!/usr/bin/env python3
"""Transcribe Journl audio at 11:00 local time or after a manual trigger."""

import argparse
import hashlib
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
import sys
sys.path.insert(0, str(APP if APP.is_dir() else ROOT))
from scheduling import path_lock, read_text_file, update_text_file, write_text_file  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
TZ_NAME = os.environ.get("TZ", "Europe/Berlin")
WHISPER_URL = os.environ.get("WHISPER_URL", "http://whisper:8090").rstrip("/")
WHISPER_API_KEY = os.environ.get("WHISPER_API_KEY", "")
TRANSCRIPTION_HOUR = int(os.environ.get("WHISPER_SCHEDULE_HOUR", "11"))


def _now():
    return datetime.now(ZoneInfo(TZ_NAME))


def _read_json(path):
    try:
        return json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path, value):
    write_text_file(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _multipart(file_path):
    boundary = "----journl-" + uuid.uuid4().hex
    parts = []
    def field(name, value):
        parts.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode(), b"\r\n"])
    field("model", os.environ.get("WHISPER_MODEL", "base"))
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


def _call_whisper(file_path):
    if not WHISPER_API_KEY:
        raise RuntimeError("WHISPER_API_KEY is not configured")
    boundary, body = _multipart(file_path)
    request = urllib.request.Request(
        WHISPER_URL + "/v1/audio/transcriptions", data=body, method="POST",
        headers={"Authorization": "Bearer " + WHISPER_API_KEY, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=21600) as response:
        return json.loads(response.read().decode("utf-8"))


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
    marker = f'"id":"{item["id"]}"'
    transcript_marker = f'<!-- jt:transcript {{"media_id":"{item["id"]}"}} -->'
    def update(current):
        if marker not in current or transcript_marker in current:
            return current
        lines = current.splitlines()
        for index, line in enumerate(lines):
            if marker in line and "jt:media" in line:
                backup = journal.parent / "_Backup" / f"{journal.stem}_backup_{uuid.uuid4().hex[:8]}{journal.suffix}.bak"
                write_text_file(backup, current)
                insert_at = min(len(lines), index + 2)
                lines[insert_at:insert_at] = ["", transcript_marker, "### Transkript #Sprachnachricht #Transkription", transcript.get("text", "").strip()]
                return "\n".join(lines) + ("\n" if current.endswith("\n") else "")
        return current
    update_text_file(journal, update)
    request_dir = DATA_DIR / "indexes" / "brain_rebuild_requests"
    request_file = request_dir / f"{hashlib.sha256(user_id.encode('utf-8')).hexdigest()}.json"
    _write_json(request_file, {"request_key": user_id})


def pending(user_filter=None):
    locations = list(DATA_DIR.glob("*/*/*/*/media/Sprachi/*_metadata.json"))
    locations.extend(DATA_DIR.glob("*/*/*/*/.journal-media/*.json"))
    for metadata in locations:
        item = _read_json(metadata)
        if not isinstance(item, dict) or item.get("type") != "audio" or item.get("status") != "ready":
            continue
        if item.get("transcription_status") == "completed" or (user_filter and item.get("user_id") != user_filter):
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


def run(user_filter=None):
    completed = errors = 0
    for metadata, item in pending(user_filter):
        with path_lock(metadata.parent / ".transcription", exclusive=True):
            current = _read_json(metadata)
            if current.get("transcription_status") == "completed":
                continue
            original = _original_path(metadata, current)
            if not original:
                continue
            current.update(transcription_status="running", transcription_started_at=_now().isoformat(timespec="seconds"))
            _write_json(metadata, current)
            try:
                transcript = _call_whisper(original)
                transcript_path = _transcript_path(metadata, current)
                if not transcript_path:
                    raise RuntimeError("Invalid transcript path")
                _write_json(transcript_path, transcript)
                current.update(transcription_status="completed", transcription_completed_at=_now().isoformat(timespec="seconds"), transcript_text=transcript.get("text", ""))
                _write_json(metadata, current)
                _apply_to_journal(current["user_id"], current, transcript)
                completed += 1
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
    users = []
    for trigger in triggers:
        data = _read_json(trigger)
        if isinstance(data, dict) and data.get("user_id"):
            users.append(data["user_id"])
        try:
            trigger.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Cannot remove manual transcription trigger %s: %s", trigger, exc)
    return users


def loop():
    last_daily = None
    while True:
        now = _now()
        today = now.date().isoformat()
        if now.hour >= TRANSCRIPTION_HOUR and last_daily != today:
            logger.info("Starting daily transcription run")
            logger.info("Daily result: %s", run())
            last_daily = today
        for user_id in _manual_triggers():
            logger.info("Manual transcription result for %s: %s", user_id, run(user_id))
        time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.loop:
        loop()
    else:
        print(json.dumps(run(), ensure_ascii=False))
