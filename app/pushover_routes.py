"""HTTP-Oberfläche für Pushover (`/api/pushover/...`).

Nur Request-Validierung und Serialisierung; Fachlogik in ``pushover.py``.
Jeder Nutzer verwaltet ausschließlich seine eigenen Zugangsdaten; sie gehen
nie im Klartext zurück an die Oberfläche.
"""

import sys
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

import pushover


pushover_bp = Blueprint("pushover", __name__, url_prefix="/api/pushover")


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _guard(*, write=False):
    main = _main()
    user = main._current_user() if main else None
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if write:
        error = main.csrf_protect(lambda: None)()
        if error:
            return None, error
    return user, None


def _base_url(value):
    """Die Adresse, unter der der Nutzer Journl gerade öffnet - nur für den
    Link in der Nachricht, Journl ruft sie nie selbst auf."""
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _view(user_id):
    data_dir = _main().DATA_DIR
    return pushover.public_view(pushover.load_settings(data_dir, user_id),
                                pushover.load_state(data_dir, user_id))


@pushover_bp.route("/settings", methods=["GET"])
def get_settings():
    user, error = _guard()
    if error:
        return error
    return jsonify(_view(user["id"]))


@pushover_bp.route("/settings", methods=["PUT"])
def put_settings():
    user, error = _guard(write=True)
    if error:
        return error
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    main = _main()
    try:
        pushover.save_settings(main.DATA_DIR, user["id"], payload,
                               base_url=_base_url(payload.get("base_url")),
                               now=main.get_tz_aware_now()[0])
    except pushover.PushoverError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError:
        return jsonify({"error": "Einstellungen konnten nicht gespeichert werden."}), 500
    return jsonify(_view(user["id"]))


@pushover_bp.route("/test", methods=["POST"])
def send_test():
    user, error = _guard(write=True)
    if error:
        return error
    main = _main()
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    base_url = _base_url(payload.get("base_url"))
    if base_url:
        pushover.save_settings(main.DATA_DIR, user["id"], {}, base_url=base_url)
    ok, message = pushover.send_test(main.DATA_DIR, user["id"], main.get_tz_aware_now()[0])
    view = _view(user["id"])
    if not ok:
        return jsonify({**view, "error": message}), 502
    return jsonify(view)
