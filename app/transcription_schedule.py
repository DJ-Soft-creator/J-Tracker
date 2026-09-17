"""Steuerung des Whisper-Sidecars: Zeitplan, manueller Lauf und dessen Stand.

The sidecar transcribes every recording that is still without a transcript.  For
a long time that happened once a day at a fixed hour baked into the deployment;
whoever dictates several times a day had to wait until the next morning.  The
schedule therefore lives in a small file under ``DATA_DIR`` that the worker
re-reads on every pass, so a change takes effect without a container restart.

One sidecar serves the whole instance, so the setting is global: everyone may
read it, only an administrator may change it.
"""

import os

from flask import jsonify, request

import write_sessions


SCHEDULE_MODES = ("hourly", "daily", "off")
DEFAULT_HOUR = 11


def _schedule_file():
    return write_sessions._main().DATA_DIR / "whisper_jobs" / "schedule.json"


def _default_hour():
    """The deployed default stays the fallback until somebody changes it."""
    try:
        hour = int(os.environ.get("WHISPER_SCHEDULE_HOUR", str(DEFAULT_HOUR)))
    except ValueError:
        return DEFAULT_HOUR
    return hour if 0 <= hour <= 23 else DEFAULT_HOUR


def current_schedule():
    """Return the stored schedule, falling back to a daily run at the default hour."""
    stored = write_sessions._read_json(_schedule_file())
    mode, hour = "daily", _default_hour()
    if isinstance(stored, dict):
        if stored.get("mode") in SCHEDULE_MODES:
            mode = stored["mode"]
        if isinstance(stored.get("hour"), int) and 0 <= stored["hour"] <= 23:
            hour = stored["hour"]
    return {"mode": mode, "hour": hour}


@write_sessions.write_sessions_bp.route("/transcriptions/schedule", methods=["GET", "POST"])
def transcription_schedule():
    user, error = write_sessions._require_user()
    if error:
        return error
    if request.method == "GET":
        response = jsonify({**current_schedule(), "can_edit": bool(user.get("admin"))})
        response.headers["Cache-Control"] = "no-store"
        return response
    csrf = write_sessions._csrf_error()
    if csrf:
        return csrf
    if not user.get("admin"):
        return jsonify({"error": "Nur Administratoren können den Zeitplan ändern"}), 403
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    if mode not in SCHEDULE_MODES:
        return jsonify({"error": "Ungültiger Zeitplan"}), 400
    hour = data.get("hour", current_schedule()["hour"])
    if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
        return jsonify({"error": "Ungültige Stunde"}), 400
    write_sessions._write_json(_schedule_file(), {
        "mode": mode, "hour": hour,
        "updated_at": write_sessions._iso(write_sessions._now()), "updated_by": user["id"],
    })
    return jsonify({"ok": True, "mode": mode, "hour": hour, "can_edit": True})


@write_sessions.write_sessions_bp.route("/transcriptions/run", methods=["POST"])
def request_transcriptions():
    """Den Sidecar bitten, jetzt alles Offene dieses Kontos zu transkribieren."""
    user, error = write_sessions._require_user()
    if error:
        return error
    csrf = write_sessions._csrf_error()
    if csrf:
        return csrf
    import ai_attachments
    root = write_sessions._main().DATA_DIR / "whisper_jobs"
    status = {
        "user_id": user["id"], "status": "queued",
        "requested_at": write_sessions._iso(write_sessions._now()),
        "model": ai_attachments.whisper_model((request.get_json(silent=True) or {}).get("model")),
    }
    write_sessions._write_json(root / "manual" / f"{user['id']}.json", status)
    write_sessions._write_json(root / "status" / f"{user['id']}.json", status)
    return jsonify({"ok": True, "queued": True, **status})


@write_sessions.write_sessions_bp.route("/transcriptions/status", methods=["GET"])
def transcription_status():
    user, error = write_sessions._require_user()
    if error:
        return error
    path = write_sessions._main().DATA_DIR / "whisper_jobs" / "status" / f"{user['id']}.json"
    status = write_sessions._read_json(path, {"status": "idle"})
    if not isinstance(status, dict) or status.get("user_id") not in {None, user["id"]}:
        status = {"status": "idle"}
    response = jsonify(status)
    response.headers["Cache-Control"] = "no-store"
    return response
