"""Queue a fresh transcription for a voice message that already sits in a journal.

The synchronous endpoint in `ai_attachments` only serves recordings inside an
active write session.  Once a recording has been submitted, its audio lives in
the dated journal media tree and its transcript is a block in the Markdown, so
redoing it means asking the sidecar worker for another run and letting
`transcript_blocks.upsert` replace the block that is already there.

The run is deliberately asynchronous: the larger quality steps need minutes on
this host, far beyond any sane request timeout.
"""

from flask import jsonify, request

import ai_attachments
import write_sessions
from scheduling import path_lock


# The metadata sidecar of a submitted recording lives next to its audio file,
# named differently per media kind; a session-local recording has neither.
def _persisted_metadata_file(directory, media_id):
    if directory.name == "Sprachi":
        return next(iter(sorted(directory.glob(f"*_{media_id}_metadata.json"))), None)
    if directory.name == ".journal-media":
        candidate = directory / f"{media_id}.json"
        return candidate if candidate.is_file() and not candidate.is_symlink() else None
    return None


@write_sessions.write_sessions_bp.route("/media/<media_id>/retranscribe", methods=["POST"])
def retranscribe_media(media_id):
    """Ask the worker to transcribe one submitted recording again."""
    user, error = write_sessions._require_user()
    if error:
        return error
    csrf = write_sessions._csrf_error()
    if csrf:
        return csrf
    item, directory = write_sessions._find_media(user["id"], media_id)
    if not item or item.get("type") != "audio":
        return jsonify({"error": "Sprachnachricht nicht gefunden"}), 404
    if not item.get("journal_path"):
        return jsonify({"error": "Diese Aufnahme gehört noch zu einer offenen Session"}), 409
    metadata_file = _persisted_metadata_file(directory, media_id)
    if not metadata_file:
        return jsonify({"error": "Sprachnachricht nicht gefunden"}), 404
    model = ai_attachments.whisper_model((request.get_json(silent=True) or {}).get("model"))

    with path_lock(directory / ".transcription", exclusive=True):
        current = write_sessions._read_json(metadata_file)
        if not isinstance(current, dict) or current.get("user_id") != user["id"]:
            return jsonify({"error": "Sprachnachricht nicht gefunden"}), 404
        if current.get("transcription_status") == "running":
            return jsonify({"error": "Transkription läuft bereits"}), 409
        # The old transcript stays in the journal until the new one replaces it,
        # so a failed re-run never leaves the entry without any text.
        current.update(
            transcription_status="pending",
            transcription_model=model,
            transcription_error=None,
            updated_at=write_sessions._iso(write_sessions._now()),
        )
        write_sessions._write_json(metadata_file, current)

    root = write_sessions._main().DATA_DIR / "whisper_jobs"
    status = {
        "user_id": user["id"], "status": "queued",
        "requested_at": write_sessions._iso(write_sessions._now()),
        "model": model, "media_id": media_id,
    }
    write_sessions._write_json(root / "manual" / f"{user['id']}.json", status)
    write_sessions._write_json(root / "status" / f"{user['id']}.json", status)
    return jsonify({"ok": True, "queued": True, **status})
