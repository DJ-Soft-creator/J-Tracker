"""Main entry and account routes.

AI maintenance contract:
Own only login, settings, account and health HTTP handlers. Do not add
AI, journal mutation or history behavior here.
The module is imported only after main.py has created the Flask app and core
helpers. Runtime values that tests or DEV configuration may replace are synced
from that facade before each request.
"""

import sys

import journal_templates
import text_snippets

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before route modules are imported")
globals().update({name: getattr(_core, name) for name in dir(_core) if not name.startswith("__")})
_DYNAMIC_NAMES = ('DATA_DIR', 'USERS_PATH', 'CONFIG_PATH', 'IS_DEV', 'load_config', 'load_app_version', '_current_user', '_read_users_file', 'render_template')


def _sync_runtime():
    for name in _DYNAMIC_NAMES:
        if hasattr(_core, name):
            globals()[name] = getattr(_core, name)


@app.before_request
def _sync_main_entry_routes_runtime():
    _sync_runtime()


@app.route("/")
def index():
    return render_template(_get_template_for_device())


@app.route("/sw.js")
def service_worker():
    """Serve the worker from the root so its scope covers the whole PWA."""
    from flask import send_from_directory

    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Cache-Control"] = "no-cache"
    return response


def _get_template_for_device():
    user_agent = flask_request.headers.get("User-Agent", "").lower()
    is_mobile = any(keyword in user_agent for keyword in [
        "iphone", "ipad", "ipod", "windows phone"
    ]) or (
        ("android" in user_agent and "mobile" in user_agent)
        or ("macintosh" in user_agent and "mobile/" in user_agent)
    )
    return "desktop.html" if not is_mobile else "index.html"


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        if not check_rate_limit(ip):
            return jsonify({"error": "Too many attempts from this IP. Please wait 5 minutes."}), 429

        data = request.get_json(silent=True) or request.form
        username = (data.get("username", "") or "").strip()
        password = data.get("password", "") or ""

        user = get_user_by_username(username)
        if not user:
            record_login_attempt(ip, False)
            return jsonify({"error": "Invalid credentials"}), 401

        now = get_tz_aware_now()[0]

        # Account lock check (auto-unlock on expiry)
        locked, until = _is_locked(user)
        if locked:
            record_login_attempt(ip, False)
            update_user(user)
            return jsonify({
                "error": "Account is temporarily locked. Try again in a few minutes.",
                "locked_until": until.isoformat() if until else None,
            }), 423

        # Per-username rate limiting
        recent = _user_failed_window(user)
        if len(recent) >= USERNAME_FAIL_MAX:
            update_user(user)
            return jsonify({
                "error": "Too many failed attempts for this user. Please wait 15 minutes.",
            }), 429

        ok = _check_password(password, user.get("password", ""))
        if not ok:
            recent.append(now.isoformat())
            user["failed_login_attempts"] = recent
            user["failed_since_last_login"] = user.get("failed_since_last_login", 0) + 1
            if len(user["failed_login_attempts"]) >= ACCOUNT_LOCK_FAILS:
                user["locked_until"] = (now + timedelta(seconds=ACCOUNT_LOCK_SECONDS)).isoformat()
            update_user(user)
            record_login_attempt(ip, False)
            return jsonify({"error": "Invalid credentials"}), 401

        # Success
        record_login_attempt(ip, True)
        notice = None
        failed_since = user.get("failed_since_last_login", 0) or 0
        if failed_since > 0:
            notice = f"There were {failed_since} failed login attempt(s) since your last login."

        # Trusted device
        fp = _get_device_fingerprint()
        _prune_trusted_devices(user)
        _add_trusted_device(user, fp)

        user["failed_login_attempts"] = []
        user["locked_until"] = None
        user["failed_since_last_login"] = 0
        user["last_login_at"] = now.isoformat()
        update_user(user)

        # Session rotation + cookie
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["login_at"] = now.isoformat()
        _ensure_csrf_token()

        resp = {"ok": True}
        if notice:
            resp["notice"] = notice
        return jsonify(resp)

    return render_template(_get_template_for_device())


@app.route("/api/time")
@require_auth
def api_time():
    """Return current server time in Europe/Berlin timezone."""
    now, _ = get_tz_aware_now()
    return jsonify({"time": now.strftime("%H:%M")})


@app.route("/api/health")
def api_health():
    """Health check that returns auth status."""
    authenticated = is_authenticated()
    user = _current_user() if authenticated else None
    database = database_health()
    response = {
        "authenticated": authenticated,
        "username": user.get("username") if user else None,
        "database": database,
    }
    return jsonify(response), (200 if database["healthy"] else 503)


@app.route("/logout", methods=["POST"])
@csrf_protect
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/csrf-token")
@require_auth
def api_csrf_token():
    _ensure_csrf_token()
    return jsonify({"csrf_token": session["csrf_token"]})


# Felder, die nie an den Browser gehen. load_config() setzt die AI_KEY_*-Werte
# als api_key in die Anbieter ein; /api/config gab sie bis 2026-10-01 jedem
# angemeldeten Konto mit (D-021: ein Schluessel verlaesst den Server nie).
# Bewusst exakte Namen bzw. Endungen - "max_tokens" ist kein Geheimnis.
_SECRET_FIELDS = {"api_key", "apikey", "secret", "client_secret", "password", "token",
                  "access_token", "refresh_token", "bearer"}
_SECRET_SUFFIXES = ("_api_key", "_secret", "_password")


def _without_secrets(value):
    if isinstance(value, dict):
        return {key: _without_secrets(item) for key, item in value.items()
                if not (str(key).lower() in _SECRET_FIELDS or str(key).lower().endswith(_SECRET_SUFFIXES))}
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


@app.route("/api/config")
@require_auth
def api_config():
    """Return the dynamic config JSON to the frontend."""
    cfg = load_config()
    cfg["app_version"] = load_app_version()
    user = _current_user()
    if user:
        cfg["current_user"] = {
            "id": user["id"],
            "username": user["username"],
            "admin": user.get("admin") is True,
        }
    else:
        cfg["current_user"] = None
    # expose all users (id + username only) for dropdown selectors in forms
    all_users = _read_users_file().get("users", [])
    cfg["users"] = [{"id": u.get("id"), "username": u.get("username")} for u in all_users]
    with _youtube_mode_lock:
        cfg["presentation"] = {"youtube_mode": bool(IS_DEV and _youtube_mode_enabled), "can_use_youtube_mode": IS_DEV}
    # Solange niemand eigene Textbausteine gespeichert hat, fehlt der Schlüssel
    # in config.json noch ganz - ohne diesen Fallback bliebe der Editor beim
    # Kaltstart ohne die eingebauten Vorschläge (z. B. "-" -> "- [ ] ").
    cfg["text_snippets"] = cfg.get("text_snippets") or list(text_snippets.DEFAULT_SNIPPETS)
    return jsonify(_without_secrets(cfg))


# ─── Startzeit-Messung (startup-timing.js) ─────────────────────────
_STARTUP_NUMBERS = ("ready_ms", "hidden_ms", "response_ms", "dom_ms", "load_ms", "scripts", "scripts_cached", "network_kb")
_STARTUP_PATH_RE = re.compile(r"^/api/[A-Za-z0-9/_.-]{0,80}$")


@app.route("/api/diagnostics/startup", methods=["POST"])
@require_auth
def api_diagnostics_startup():
    """Eine Startmessung eines Geraets als eine Logzeile - nur Zahlen.

    Damit lassen sich langsame Starts (iPhone/PWA) aus dem Server-Log
    auswerten. Gespeichert wird nichts; Inhalte kommen nie mit, von
    API-Aufrufen nur der Pfad.
    """
    csrf_error = csrf_protect(lambda: None)()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    numbers = {}
    for key in _STARTUP_NUMBERS:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 10_000_000:
            numbers[key] = int(value)
    slow = []
    for item in (data.get("api") or [])[:5]:
        if isinstance(item, dict) and isinstance(item.get("path"), str) and _STARTUP_PATH_RE.match(item["path"]) \
                and isinstance(item.get("ms"), (int, float)) and not isinstance(item.get("ms"), bool):
            slow.append(f"{item['path']}={int(item['ms'])}ms")
    device = re.sub(r"[^A-Za-z ()]", "", str(data.get("device") or ""))[:30]
    user = _current_user() or {}
    logger.info("startup-timing user=%s device=%s sw=%s %s slow_api=[%s]",
                user.get("id", "?"), device or "?", bool(data.get("service_worker")),
                " ".join(f"{key}={value}" for key, value in numbers.items()), ", ".join(slow))
    return jsonify({"ok": True})


@app.route("/api/settings/youtube-mode", methods=["GET", "POST"])
@require_auth
def api_settings_youtube_mode():
    """Presentation-only DEV switch.  It is intentionally unavailable in production."""
    global _youtube_mode_enabled
    if not IS_DEV:
        return jsonify({"error": "YouTube-Mode is only available in DEV"}), 404
    if request.method == "POST":
        csrf_error = csrf_protect(lambda: None)()
        if csrf_error:
            return csrf_error
        enabled = (request.get_json(silent=True) or {}).get("enabled")
        if not isinstance(enabled, bool):
            return jsonify({"error": "enabled must be true or false"}), 400
        with _youtube_mode_lock:
            _youtube_mode_enabled = enabled
    with _youtube_mode_lock:
        return jsonify({"ok": True, "enabled": _youtube_mode_enabled, "environment": "dev"})


# ─── admin: user creation (T3) ────────────────────────────────────────
@app.route("/api/admin/users", methods=["POST"])
def api_admin_create_user():
    if not ADMIN_TOKEN:
        return jsonify({"error": "User creation endpoint is disabled (ADMIN_TOKEN not set)"}), 404
    provided = flask_request.headers.get("X-Admin-Token")
    if not provided or not secrets.compare_digest(provided, ADMIN_TOKEN):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    username = (data.get("username", "") or "").strip()
    password = data.get("password", "") or ""
    consent = bool(data.get("consent", True))
    admin = data.get("admin", False)
    if not isinstance(admin, bool):
        return jsonify({"error": "admin must be true or false"}), 400
    user, err = create_user(username, password, consent=consent, admin=admin)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({
        "ok": True,
        "user": {"id": user["id"], "username": user["username"], "admin": user["admin"]},
    }), 201


# ─── settings (T6) ───────────────────────────────────────────────────
@app.route("/api/settings/profile", methods=["GET"])
@require_auth
def api_settings_profile_get():
    user = _current_user()
    return jsonify({
        "username": user["username"],
        "created_at": user.get("created_at"),
        "consent": user.get("consent", False),
        "consent_at": user.get("consent_at"),
        "show_write_hint": user.get("show_write_hint", True),
        "show_hashtag_quick_button": user.get("show_hashtag_quick_button", True),
    })


@app.route("/api/settings/profile", methods=["POST"])
@require_auth
@csrf_protect
def api_settings_profile_set():
    user = _current_user()
    data = request.get_json(silent=True) or {}
    new_username = (data.get("username", "") or "").strip()
    if not new_username:
        return jsonify({"error": "Username is required"}), 400
    if new_username.lower() == user["username"].lower():
        return jsonify({"ok": True, "username": user["username"]})
    existing = get_user_by_username(new_username)
    if existing and existing["id"] != user["id"]:
        return jsonify({"error": "Username already taken"}), 409
    user["username"] = new_username
    update_user(user)
    return jsonify({"ok": True, "username": user["username"]})


@app.route("/api/settings/templates", methods=["GET"])
@require_auth
def api_settings_templates_get():
    """Rohe Vorlagenliste fuer den Verwaltungsdialog.

    Bewusst nicht ``load_config()``: dessen Rueckgabe ist env-injiziert, ein
    Zurueckschreiben wuerde aufgeloeste Platzhalter festschreiben.
    """
    user = _current_user()
    try:
        templates = journal_templates.load_templates(CONFIG_PATH)
    except journal_templates.TemplateError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({
        "templates": templates,
        "protected_ids": sorted(journal_templates.PROTECTED),
        "code_formatted_ids": list(journal_templates.CODE_FORMATTED_IDS),
        "template_types": list(journal_templates.TEMPLATE_TYPES),
        "field_types": list(journal_templates.FIELD_TYPES),
        "can_edit": bool(user) and user.get("admin") is True,
        "users": [
            {"id": u.get("id"), "username": u.get("username")}
            for u in _read_users_file().get("users", [])
        ],
    })


@app.route("/api/settings/templates", methods=["PUT"])
@require_auth
@csrf_protect
def api_settings_templates_set():
    """Ersetzt die komplette Vorlagenliste — Anlegen, Aendern, Loeschen, Sortieren.

    Vorlagen sind global, nicht pro Benutzer: ``assigned_users`` steuert nur die
    Sichtbarkeit. Deshalb bleibt das Schreiben Administratoren vorbehalten.
    """
    user = _current_user()
    if user.get("admin") is not True:
        return jsonify({"error": "Nur Administratoren dürfen Vorlagen ändern."}), 403
    payload = (request.get_json(silent=True) or {}).get("templates")
    known_user_ids = {u.get("id") for u in _read_users_file().get("users", [])}
    try:
        previous = journal_templates.load_templates(CONFIG_PATH)
        templates = journal_templates.validate(payload, known_user_ids, previous)
        journal_templates.save_templates(CONFIG_PATH, templates)
    except journal_templates.TemplateError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        logger.error("Templates konnten nicht gespeichert werden: %s", exc)
        return jsonify({"error": "Vorlagen konnten nicht gespeichert werden."}), 500
    logger.info("Templates updated by %s (%d entries)", user.get("username"), len(templates))
    return jsonify({"ok": True, "templates": templates})


@app.route("/api/settings/snippets", methods=["GET"])
@require_auth
def api_settings_snippets_get():
    """Textbausteine für den Verwaltungsdialog und die Vorschläge beim Tippen."""
    try:
        snippets = text_snippets.load_snippets(CONFIG_PATH)
    except text_snippets.SnippetError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"snippets": snippets})


@app.route("/api/settings/snippets", methods=["PUT"])
@require_auth
@csrf_protect
def api_settings_snippets_set():
    """Ersetzt die komplette Liste - Anlegen, Aendern, Loeschen.

    Anders als Vorlagen sind Textbausteine eine persönliche Tippabkürzung
    ohne strukturelle Wirkung auf gespeicherte Journale; jeder angemeldete
    Nutzer darf sie pflegen, nicht nur Administratoren.
    """
    user = _current_user()
    payload = (request.get_json(silent=True) or {}).get("snippets")
    try:
        snippets = text_snippets.validate(payload)
        text_snippets.save_snippets(CONFIG_PATH, snippets)
    except text_snippets.SnippetError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        logger.error("Textbausteine konnten nicht gespeichert werden: %s", exc)
        return jsonify({"error": "Textbausteine konnten nicht gespeichert werden."}), 500
    logger.info("Text snippets updated by %s (%d entries)", user.get("username"), len(snippets))
    return jsonify({"ok": True, "snippets": snippets})


@app.route("/api/settings/write-hint", methods=["POST"])
@require_auth
@csrf_protect
def api_settings_write_hint():
    """Store the compact writing hint per user, across devices and restarts."""
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be true or false"}), 400
    user = _current_user()
    user["show_write_hint"] = enabled
    update_user(user)
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/settings/hashtag-quick-button", methods=["POST"])
@require_auth
@csrf_protect
def api_settings_hashtag_quick_button():
    """Store whether the mobile editor exposes the optional hashtag key."""
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be true or false"}), 400
    user = _current_user()
    user["show_hashtag_quick_button"] = enabled
    update_user(user)
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/settings/password", methods=["POST"])
@require_auth
@csrf_protect
def api_settings_password():
    user = _current_user()
    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password", "") or ""
    new_password = data.get("new_password", "") or ""
    if not _check_password(old_password, user.get("password", "")):
        return jsonify({"error": "Current password is incorrect"}), 401
    ok, err = validate_password(new_password)
    if not ok:
        return jsonify({"error": err}), 400
    user["password"] = _hash_password(new_password)
    update_user(user)
    return jsonify({"ok": True})


@app.route("/api/settings/devices", methods=["GET"])
@require_auth
def api_settings_devices():
    user = _current_user()
    _prune_trusted_devices(user)
    update_user(user)
    return jsonify({"devices": user.get("trusted_devices", [])})


@app.route("/api/settings/devices/revoke", methods=["POST"])
@require_auth
@csrf_protect
def api_settings_devices_revoke():
    user = _current_user()
    data = request.get_json(silent=True) or {}
    fingerprint = data.get("fingerprint", "")
    if not fingerprint:
        return jsonify({"error": "fingerprint is required"}), 400
    devices = user.get("trusted_devices", []) or []
    user["trusted_devices"] = [d for d in devices if d.get("fingerprint") != fingerprint]
    update_user(user)
    return jsonify({"ok": True})


# ─── GDPR (T9) ───────────────────────────────────────────────────────
@app.route("/api/me/export", methods=["GET"])
@require_auth
def api_me_export():
    user = _current_user()
    user_sanitized = {k: v for k, v in user.items() if k != "password"}
    journals = {}
    user_dir = DATA_DIR / user["id"]
    if user_dir.is_dir():
        for year_dir in sorted(user_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            for month_dir in sorted(year_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                for day_dir in sorted(month_dir.iterdir()):
                    if not day_dir.is_dir():
                        continue
                    for f in sorted(day_dir.iterdir()):
                        if f.name.startswith("Journal_") and f.suffix == ".md":
                            try:
                                with open(f, "r", encoding="utf-8") as fh:
                                    journals[f.name] = fh.read()
                            except (IOError, OSError) as e:
                                logger.error(f"Failed to read {f}: {e}")
    payload = json.dumps({"user": user_sanitized, "journals": journals}, ensure_ascii=False, indent=2)
    resp = make_response(payload)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="user_export_{user["username"]}.json"'
    return resp


@app.route("/api/me/delete", methods=["POST"])
@require_auth
@csrf_protect
def api_me_delete():
    user = _current_user()
    data = request.get_json(silent=True) or {}
    password = data.get("password", "") or ""
    if not _check_password(password, user.get("password", "")):
        return jsonify({"error": "Password is incorrect"}), 401
    # Keep journal entries (Q8); only remove user record.
    delete_user(user["id"])
    session.clear()
    return jsonify({"ok": True})


__all__ = ['index', 'service_worker', '_get_template_for_device', 'login', 'api_time', 'api_health', 'logout', 'api_csrf_token', 'api_config', 'api_settings_youtube_mode', 'api_admin_create_user', 'api_settings_profile_get', 'api_settings_profile_set', 'api_settings_templates_get', 'api_settings_templates_set', 'api_settings_snippets_get', 'api_settings_snippets_set', 'api_settings_write_hint', 'api_settings_password', 'api_settings_devices', 'api_settings_devices_revoke', 'api_me_export', 'api_me_delete']
