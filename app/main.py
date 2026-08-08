import os
import json
import uuid
import hashlib
import secrets
import logging
import re
import fcntl
import threading
import time
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    session,
    make_response,
    request as flask_request,
)
from werkzeug.security import check_password_hash, generate_password_hash

import family as family_module
from family import family_bp
import brain as brain_module
from brain import brain_bp
import ai_sessions as ai_sessions_module
from ai_sessions import ai_sessions_bp
import tagging as tagging_module
from scheduling import update_text_file, write_text_file, read_text_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── paths ───────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))  # volume-mounted; persists across updates
USERS_PATH = DATA_DIR / "users.json"  # multiuser store (persisted in volume)
CONFIG_PATH = DATA_DIR / "config.json"  # persisted global configuration
VERSION_PATH = Path(__file__).with_name("VERSION")  # shipped application version

app = Flask(__name__)
app.register_blueprint(family_bp)
app.register_blueprint(brain_bp)
app.register_blueprint(ai_sessions_bp)

# ─── Environment flag (prod or dev) ──────────────────────────────────
ENVIRONMENT = os.environ.get("ENVIRONMENT", "prod").strip().lower()
IS_DEV = ENVIRONMENT == "dev"
_youtube_mode_lock = threading.Lock()
# This process-local flag deliberately never changes the environment, data paths,
# credentials, or API targets.  It is reset with the DEV process.
_youtube_mode_enabled = False

# ─── session cookie config (4 weeks, secure flags) ───────────────────
app.permanent_session_lifetime = timedelta(weeks=4)
app.config.update(
    # The DEV stage is intentionally served over HTTP on the local network.
    # Secure cookies are not sent over HTTP, which would make every API call
    # after a successful login unauthenticated.
    SESSION_COOKIE_SECURE=not IS_DEV,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


@app.after_request
def set_security_headers(response):
    """Set security headers on every response."""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "img-src 'self' data:; "
        "font-src 'self' https://cdn.tailwindcss.com;"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.context_processor
def inject_env():
    """Make the development-stage flag available in all templates."""
    return {"IS_DEV": IS_DEV}

_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key or _secret_key == "change-this-to-a-random-secret-key":
    logger.warning(
        "⚠️  SECRET_KEY is not set or uses default value. "
        "Set a strong random key via the SECRET_KEY environment variable!"
    )
app.secret_key = _secret_key or os.urandom(32).hex()

# ─── local LLM IP (substituted into {locale_LLM_IP} placeholders in config.json) ──
LOCALE_LLM_IP = os.environ.get("locale_LLM_IP", "").strip()
if not LOCALE_LLM_IP:
    logger.warning(
        "⚠️  locale_LLM_IP is not set. {locale_LLM_IP} placeholders in config.json "
        "will not be resolved. Set it in your .env (see .env.example)."
    )

# ─── auth (legacy env used only for one-time migration — see T11) ──
_AUTH_USER = os.environ.get("AUTH_USER", "")
_AUTH_PASS = os.environ.get("AUTH_PASS", "")

# Optional static admin token for user creation endpoint (T3). If unset,
# /api/admin/users is disabled (returns 404).
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

LOGIN_ATTEMPTS = {}  # {ip: [(timestamp, success), ...]}
MAX_LOGIN_ATTEMPTS = 10
ATTEMPT_WINDOW_SECONDS = 300
_CLEANUP_INTERVAL_SECONDS = 300  # cleanup every 5 minutes

# Per-username rate limiting / account lock (T5)
USERNAME_FAIL_WINDOW_SECONDS = 900      # 15 minutes
USERNAME_FAIL_MAX = 5                   # 5 fails / 15 min → reject
ACCOUNT_LOCK_FAILS = 10                 # 10 total fails → lock
ACCOUNT_LOCK_SECONDS = 900              # 15 minutes lock

_TRUSTED_DEVICE_TTL = timedelta(weeks=4)

def _cleanup_login_attempts():
    """Periodically remove stale login attempts older than ATTEMPT_WINDOW_SECONDS."""
    while True:
        time.sleep(_CLEANUP_INTERVAL_SECONDS)
        now = datetime.now(timezone.utc)
        stale_ips = []
        for ip, entries in LOGIN_ATTEMPTS.items():
            LOGIN_ATTEMPTS[ip] = [
                entry for entry in entries
                if (now - entry[0]).total_seconds() < ATTEMPT_WINDOW_SECONDS
            ]
            if not LOGIN_ATTEMPTS[ip]:
                stale_ips.append(ip)
        for ip in stale_ips:
            del LOGIN_ATTEMPTS[ip]
        logger.debug(f"Login attempts cleanup: {len(stale_ips)} IPs removed")

# Start background cleanup thread
_cleanup_thread = threading.Thread(target=_cleanup_login_attempts, daemon=True)
_cleanup_thread.start()


def _apply_env_overrides(items, prefix):
    """Apply AI_PROVIDER_<idx>_<KEY> / AI_FUNCTION_<idx>_<KEY> env overrides."""
    for idx, item in enumerate(items):
        item_prefix = f"{prefix}_{idx}"
        for key in ["api_url", "model", "system_prompt", "max_tokens", "temperature", "api_key", "mode", "label"]:
            env_key = f"{item_prefix}_{key.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                try:
                    item[key] = json.loads(env_val)
                except (json.JSONDecodeError, ValueError):
                    item[key] = env_val


def _inject_env_vars(config):
    """Inject environment variables into AI config.

    Legacy:  AI_TEMPLATE_<idx>_<KEY>
    New:     AI_PROVIDER_<idx>_<KEY> + auto AI_KEY_<UPPER_ID> for api_key
              AI_FUNCTION_<idx>_<KEY>
    Also substitutes {locale_LLM_IP} placeholders in api_url strings.

    Order matters: env overrides are applied first, so that placeholders inside
    URLs supplied via AI_PROVIDER_*_API_URL are resolved too. Substitution only
    runs when LOCALE_LLM_IP is set; otherwise the literal placeholder is kept,
    which _call_ai_api detects and turns into a clear error per request.
    """
    locale_ip = LOCALE_LLM_IP

    _apply_env_overrides(config.get("ai_templates", []), "AI_TEMPLATE")
    _apply_env_overrides(config.get("ai_providers", []), "AI_PROVIDER")
    _apply_env_overrides(config.get("ai_functions", []), "AI_FUNCTION")

    for provider in config.get("ai_providers", []):
        pid = provider.get("id")
        if not pid:
            continue
        env_key = f"AI_KEY_{pid.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            provider["api_key"] = env_val
        elif "api_key" not in provider:
            provider.setdefault("api_key", "")

    if locale_ip:
        def _substitute_urls(obj):
            if isinstance(obj, dict):
                return {k: _substitute_urls(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_substitute_urls(item) for item in obj]
            elif isinstance(obj, str):
                return obj.replace("{locale_LLM_IP}", locale_ip)
            return obj
        config = _substitute_urls(config)

    return config


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Configuration file is required at {CONFIG_PATH}") from exc
    return _inject_env_vars(config)


def load_app_version():
    """Read the version shipped with the application, never the data volume."""
    try:
        return VERSION_PATH.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        logger.warning("Application version file is missing at %s", VERSION_PATH)
        return "unknown"


def _is_hashed(password):
    """Check if password is already a werkzeug hash."""
    return password.startswith("pbkdf2:") or password.startswith("scrypt:")


def _check_password(password, stored):
    """Check password against stored value (supports plaintext and hashed)."""
    if _is_hashed(stored):
        return check_password_hash(stored, password)
    return password == stored


def _hash_password(password):
    """Hash a password with werkzeug (pbkdf2). Used when changing passwords."""
    return generate_password_hash(password, method="pbkdf2:sha256")


# ─── users.json storage (file-locked, atomic) ──────────────────────────
_users_lock = threading.Lock()


def _read_users_file():
    """Read users.json with shared lock. Returns dict {"users": [...]}; empty on missing."""
    if not USERS_PATH.exists():
        return {"users": []}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if not isinstance(data, dict) or "users" not in data:
            return {"users": []}
        return data
    except (IOError, OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to read users file {USERS_PATH}: {e}")
        return {"users": []}


def _write_users_file(data):
    """Write users.json atomically with exclusive lock."""
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = USERS_PATH.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        os.replace(str(tmp_path), str(USERS_PATH))
    except (IOError, OSError) as e:
        logger.error(f"Failed to write users file {USERS_PATH}: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def get_user_by_id(uid):
    with _users_lock:
        data = _read_users_file()
    for u in data.get("users", []):
        if u.get("id") == uid:
            return u
    return None


def get_user_by_username(name):
    name_lc = (name or "").strip().lower()
    with _users_lock:
        data = _read_users_file()
    for u in data.get("users", []):
        if (u.get("username") or "").strip().lower() == name_lc:
            return u
    return None


def get_all_users():
    """Liefert alle User (Liste von dicts)."""
    with _users_lock:
        data = _read_users_file()
    return list(data.get("users", []))


def create_user(username, password, consent=True, admin=False):
    """Create a new user. Returns (user, error_str). error_str is None on success."""
    username = (username or "").strip()
    if not username:
        return None, "Username is required"
    ok, err = validate_password(password)
    if not ok:
        return None, err
    with _users_lock:
        data = _read_users_file()
        for u in data.get("users", []):
            if (u.get("username") or "").strip().lower() == username.lower():
                return None, "Username already exists"
        now_iso = get_tz_aware_now()[0].isoformat()
        user = {
            "id": str(uuid.uuid4()),
            "username": username,
            "password": password,  # plaintext initially (migration / first creation)
            "admin": bool(admin),
            "consent": bool(consent),
            "consent_at": now_iso if consent else None,
            "trusted_devices": [],
            "created_at": now_iso,
            "failed_login_attempts": [],
            "locked_until": None,
            "last_login_at": None,
            "failed_since_last_login": 0,
        }
        data.setdefault("users", []).append(user)
        _write_users_file(data)
    return user, None


def update_user(user):
    """Full replace a user by id."""
    if not user or "id" not in user:
        return False
    with _users_lock:
        data = _read_users_file()
        for i, u in enumerate(data.get("users", [])):
            if u.get("id") == user["id"]:
                data["users"][i] = user
                _write_users_file(data)
                return True
        return False


def delete_user(uid):
    """Delete a user record (journal entries are kept — Q8)."""
    with _users_lock:
        data = _read_users_file()
        before = len(data.get("users", []))
        data["users"] = [u for u in data.get("users", []) if u.get("id") != uid]
        if len(data["users"]) == before:
            return False
        _write_users_file(data)
        return True


def validate_password(pw):
    """Strong password: min 8 chars, upper, lower, digit, special. Returns (ok, error)."""
    if not pw or len(pw) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r"[A-Z]", pw):
        return False, "Password must contain an uppercase letter"
    if not re.search(r"[a-z]", pw):
        return False, "Password must contain a lowercase letter"
    if not re.search(r"\d", pw):
        return False, "Password must contain a digit"
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;':\",./<>?`~]", pw):
        return False, "Password must contain a special character"
    return True, None


# ─── trusted devices (T10) ───────────────────────────────────────────
def _get_device_fingerprint():
    ua = flask_request.headers.get("User-Agent", "")
    return hashlib.sha256(ua.encode("utf-8")).hexdigest()


def _add_trusted_device(user, fingerprint):
    now = get_tz_aware_now()[0]
    expires = now + _TRUSTED_DEVICE_TTL
    devices = user.get("trusted_devices", []) or []
    devices = [d for d in devices if d.get("fingerprint") != fingerprint]
    devices.append({
        "fingerprint": fingerprint,
        "added_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    })
    user["trusted_devices"] = devices


def _prune_trusted_devices(user):
    now = get_tz_aware_now()[0]
    devices = user.get("trusted_devices", []) or []
    kept = []
    for d in devices:
        exp = d.get("expires_at")
        if not exp:
            continue
        try:
            exp_dt = datetime.fromisoformat(exp)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if exp_dt > now:
            kept.append(d)
    user["trusted_devices"] = kept


# ─── session auth (T4/T5) ─────────────────────────────────────────────
def is_authenticated():
    return "user_id" in session


def _current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_user_by_id(uid)


def check_rate_limit(ip_address):
    now = datetime.now(timezone.utc)
    if ip_address not in LOGIN_ATTEMPTS:
        LOGIN_ATTEMPTS[ip_address] = []
    LOGIN_ATTEMPTS[ip_address] = [
        entry for entry in LOGIN_ATTEMPTS[ip_address]
        if (now - entry[0]).total_seconds() < ATTEMPT_WINDOW_SECONDS
    ]
    if len(LOGIN_ATTEMPTS[ip_address]) >= MAX_LOGIN_ATTEMPTS:
        return False
    return True


def record_login_attempt(ip_address, success):
    now = datetime.now(timezone.utc)
    LOGIN_ATTEMPTS[ip_address].append((now, success))


def _user_failed_window(user):
    """Return recent failed-attempt timestamps within the per-username window."""
    now = datetime.now(timezone.utc)
    attempts = user.get("failed_login_attempts", []) or []
    kept = []
    for ts in attempts:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if (now - dt).total_seconds() < USERNAME_FAIL_WINDOW_SECONDS:
            kept.append(ts)
    user["failed_login_attempts"] = kept
    return kept


def _is_locked(user):
    lu = user.get("locked_until")
    if not lu:
        return False, None
    now = datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(lu)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        user["locked_until"] = None
        return False, None
    if dt > now:
        return True, dt
    user["locked_until"] = None
    return False, None


def require_auth(f):
    """Decorator that returns 401 if not authenticated."""
    from functools import wraps

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapped


def _ensure_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)


def csrf_protect(f):
    """Verify X-CSRF-Token header against session csrf_token for state-changing methods."""
    from functools import wraps

    @wraps(f)
    def wrapped(*args, **kwargs):
        if flask_request.method in ("POST", "PUT", "DELETE", "PATCH"):
            token = flask_request.headers.get("X-CSRF-Token")
            sess_token = session.get("csrf_token")
            if not sess_token or not token or not secrets.compare_digest(token, sess_token):
                return jsonify({"error": "Invalid CSRF token"}), 403
        return f(*args, **kwargs)

    return wrapped


def get_tz_aware_now():
    """Get current datetime with timezone."""
    tz_name = os.environ.get("TZ", "Europe/Berlin")
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except ImportError:
        tz = timezone(timedelta(hours=2))  # fallback: CEST
    return datetime.now(tz), tz


def _get_day_dir(now, user_id=None):
    """Get the day directory path (per-user) and create it if needed."""
    if user_id is None:
        user_id = session.get("user_id")
    base = DATA_DIR / user_id if user_id else DATA_DIR
    year = now.strftime("%Y")
    month = now.strftime("%m")
    day = now.strftime("%d")
    day_dir = base / year / month / day
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir


def _get_journal_path(now, user_id=None):
    """Get the journal markdown file path for today (per-user)."""
    day_dir = _get_day_dir(now, user_id=user_id)
    filename = f"Journal_{now.strftime('%Y-%m-%d')}.md"
    return day_dir / filename


def _write_journal_backup(filepath, content):
    """Persist a unique pre-change snapshot while the source transaction is locked."""
    backup_dir = filepath.parent / "_Backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"{filepath.stem}_backup_{uuid.uuid4().hex[:8]}{filepath.suffix}.bak"
    )
    write_text_file(backup_path, content)


def _update_journal_file(filepath, updater):
    """Mutate a journal source under the lock shared with Brain and scheduling."""
    try:
        return update_text_file(filepath, updater)
    except (IOError, OSError) as e:
        logger.error(f"Failed to update journal file {filepath}: {e}")
        raise


def _append_journal_entry(user_id, filepath, entry):
    """Append ahead of the technical footer and rebuild its canonical tag map."""
    date_match = re.fullmatch(r"Journal_(\d{4}-\d{2}-\d{2})\.md", filepath.name)
    journal_date = date_match.group(1) if date_match else None

    def append(current):
        _write_journal_backup(filepath, current)
        body, existing_footer = tagging_module.strip_footer(current)
        updated = body.rstrip() + "\n\n" + entry
        updated, agent_session = ai_sessions_module.prepare_document_save(
            user_id, updated, body, is_journal=True, actor_id=user_id,
        )
        tagging_module.propose_tags(
            user_id,
            [tag for block in tagging_module.journal_blocks(updated, journal_date) for tag in block["raw_tags"]],
        )
        return tagging_module.refresh_journal_footer(
            user_id, updated, journal_date, existing_footer
        ), {"line_hint": body.count("\n") + 1, "agent_session": agent_session}

    return _update_journal_file(filepath, append)


_TEMPLATE_FORMATTERS = {
    "schnell": lambda c, t, d: f"___\n\n- {t}\n{c.strip()}\n~~{t}~~\n\n___\n\n",
    "aufgabe": lambda c, t, d: f"___\n\n## Aufgabe | Datum & Uhrzeit: {d}\n- [ ] {c.strip()}\n\n___\n\n",
    "nebengedanke": lambda c, t, d: f"___\n\n## Nebengedanke | Datum & Uhrzeit: {d}\n{c.strip()}\n\n___\n\n",
    "thema": lambda c, t, d: f"___\n\n## Thema | Datum & Uhrzeit: {d}\n{c.strip()}\n\n___\n\n",
    "brainablage": lambda c, t, d: f"___\n\n## Brainablage: Tags: {c.strip()} **Datum & Uhrzeit: {d}**\n\n___\n\n",
    "idee": lambda c, t, d: f"___\n\n## Idee | Datum & Uhrzeit: {d}\n{c.strip()}\n\n___\n\n",
    "erkenntnis": lambda c, t, d: f"___\n\n## Erkenntnis | Datum & Uhrzeit: {d}\n{c.strip()}\n\n___\n\n",
}


def _write_markdown_entry(template_id, content, time_str, datetime_str):
    """Write markdown entry with separators using template mapping."""
    formatter = _TEMPLATE_FORMATTERS.get(template_id)
    if formatter:
        return formatter(content, time_str, datetime_str)
    return f"___\n\n{content.strip()}\n\n___\n\n"


def _write_form_markdown(label, time_str, fields, values, datetime_str=None):
    """Write form entry content to markdown."""
    lines = []
    for field in fields:
        fid = field["id"]
        val = values.get(fid)
        if val is not None and str(val).strip() != "" and str(val).strip() != "—":
            label_text = field.get("label", fid)
            ftype = field.get("type")
            if ftype == "number" and "min" in field and "max" in field:
                if label_text.strip():
                    lines.append(f"- **{label_text}:** {val}/10")
                else:
                    lines.append(f"- {val}/10")
            elif ftype == "textarea":
                if label_text.strip():
                    lines.append(f"- **{label_text}:** {val.strip()}")
                else:
                    lines.append(f"- {val.strip()}")
            else:
                if label_text.strip():
                    lines.append(f"- **{label_text}:** {val.strip()}")
                else:
                    lines.append(f"- {val.strip()}")
    timestamp = datetime_str or time_str
    return f"___\n\n## {label} | Datum & Uhrzeit: {timestamp}\n" + "\n".join(lines) + "\n\n___\n\n"


def _parse_entries_from_markdown(content):
    """Parse entries from markdown content by splitting on ___ separators."""
    content, _ = tagging_module.strip_footer(content)
    separator_pattern = re.compile(r'^___$\n', re.MULTILINE)
    parts = separator_pattern.split(content)

    entries = []
    for part in parts:
        stripped = part.strip()
        # Skip empty blocks, journal headers, and leading/trailing whitespace-only blocks
        if not stripped:
            continue
        if stripped.startswith('# Journal '):
            continue
        entries.append(stripped)

    return entries


# ─── routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(_get_template_for_device())


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
    return jsonify({
        "authenticated": authenticated,
        "username": user.get("username") if user else None,
    })


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
    return jsonify(cfg)


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


def _call_ai_api(provider, ai_function, user_message):
    """Call an AI API (OpenAI-compatible) and return the response text.

    `provider` supplies connection params (api_url, model, max_tokens, temperature, api_key).
    `ai_function` supplies the system_prompt.
    Either may also be a legacy ai_template dict (carrying all fields itself).
    """
    import urllib.request

    url = (provider.get("api_url") or ai_function.get("api_url", "") or "").strip()
    if not url:
        raise ValueError("No api_url configured")

    if "{locale_LLM_IP}" in url:
        raise ValueError(
            "locale_LLM_IP is not set. Configure it in your .env (see .env.example)."
        )

    model = provider.get("model", "")
    system_prompt = ai_function.get("system_prompt", "") or provider.get("system_prompt", "")
    max_tokens = ai_function.get("max_tokens", provider.get("max_tokens", 500))
    temperature = ai_function.get("temperature", provider.get("temperature", 0.7))

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    payload_data = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if ai_function.get("response_format") is not None:
        payload_data["response_format"] = ai_function["response_format"]
    payload = json.dumps(payload_data).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
    }
    api_key = provider.get("api_key", "") or ai_function.get("api_key", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    # Bypass any HTTP proxy inherited from the environment (HTTP_PROXY/HTTPS_PROXY).
    # Local LLM endpoints (e.g. LM Studio) must be reached directly; a corporate or
    # host proxy in the path typically returns 504 from openresty/nginx because it
    # cannot route to private-network IPs.
    _no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    if str(provider.get("id", "")).startswith("lm_"):
        models_url = re.sub(r"/chat/completions/?$", "/models", url)
        if models_url == url:
            raise ValueError("LM Studio api_url must end with /chat/completions")
        models_req = urllib.request.Request(models_url, headers=headers, method="GET")
        logger.info("Checking LM Studio model availability at %s", models_url)
        try:
            with _no_proxy_opener.open(models_req, timeout=5) as resp:
                models_result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise ConnectionError(f"LM Studio model check failed with HTTP {e.code}: {body[:500]}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"LM Studio is not reachable: {e.reason}")
        except TimeoutError:
            raise ConnectionError("LM Studio model check timed out after 5 seconds")
        except json.JSONDecodeError as e:
            raise ConnectionError(f"LM Studio returned an invalid model list: {e}")

        model_items = models_result.get("data") if isinstance(models_result, dict) else None
        if not isinstance(model_items, list):
            raise ConnectionError("LM Studio returned an invalid model list")
        available_models = {
            item.get("id") for item in model_items
            if isinstance(item, dict) and item.get("id")
        }
        if model not in available_models:
            loaded = ", ".join(sorted(available_models)) or "none"
            raise ConnectionError(f"LM Studio model '{model}' is not loaded (available: {loaded})")

    logger.info(f"AI request → {url} (model={model})")

    try:
        with _no_proxy_opener.open(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            choices = result.get("choices", [])
            if not choices:
                raise ValueError("No choices in AI response")
            message = choices[0]["message"]
            content = message.get("content", "") or ""
            if not content.strip():
                if ai_function.get("require_content"):
                    raise ValueError("AI returned no message.content for the required structured output")
                content = message.get("reasoning_content", "") or ""
            if not content.strip():
                raise ValueError("AI returned empty content (no content or reasoning_content)")
            return content.strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ConnectionError(f"AI API HTTP error {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise ConnectionError(f"AI API connection failed: {e.reason}")
    except TimeoutError:
        raise ConnectionError("AI API request timed out after 300 seconds")
    except json.JSONDecodeError as e:
        raise ValueError(f"AI returned non-JSON response: {e}")
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected AI response format: {e}")


def _write_draft_path(user_id):
    """The only writable AI scratch document; it is private to one user."""
    return DATA_DIR / user_id / "temp_Eingabe.md"


def _draft_revision(content):
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _append_write_ai_history(user_id, event):
    """Persist a small per-user audit trail without ever sharing it as a journal."""
    path = DATA_DIR / user_id / "ai_write_history.json"
    def update(current):
        try:
            history = json.loads(current) if current else []
        except json.JSONDecodeError:
            history = []
        history = history if isinstance(history, list) else []
        history.append(event)
        # Keep history useful across devices without growing the private state forever.
        return json.dumps(history[-200:], ensure_ascii=False, indent=2) + "\n"
    update_text_file(path, update)


@app.route("/api/write-ai/draft", methods=["GET", "POST"])
@require_auth
def api_write_ai_draft():
    """Load/save the current user's temporary writing-tab document only."""
    user = _current_user()
    path = _write_draft_path(user["id"])
    if request.method == "GET":
        content = read_text_file(path) if path.exists() else ""
        return jsonify({"content": content, "revision": _draft_revision(content)})
    csrf_error = csrf_protect(lambda: None)()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if not isinstance(content, str) or len(content) > 200000:
        return jsonify({"error": "Draft content is invalid or too large"}), 400
    expected = data.get("revision")
    result = {}
    def save(current):
        if expected and expected != _draft_revision(current):
            result["conflict"] = current
            return current
        result["content"] = content
        return content
    update_text_file(path, save)
    if "conflict" in result:
        return jsonify({"error": "Der Schreibstand wurde auf einem anderen Gerät geändert.", "content": result["conflict"], "revision": _draft_revision(result["conflict"])}), 409
    return jsonify({"ok": True, "content": content, "revision": _draft_revision(content)})


@app.route("/api/write-ai/submit", methods=["POST"])
@require_auth
@csrf_protect
def api_write_ai_submit():
    """Run an explicit writing-tab hashtag request against the configured provider.

    The handler neither appends to a journal nor uses project/document context.
    It accepts exactly today's private journal or the private temporary draft.
    """
    data = request.get_json(silent=True) or {}
    user = _current_user()
    uid = user["id"]
    tag = tagging_module.normalise_tag(data.get("workflow_tag", ""))
    workflow = tagging_module.catalog_view(uid).get("ai", {}).get(tag)
    if not workflow or workflow.get("target", "document") != "write_tab":
        return jsonify({"error": "Unbekanntes Schreiben-Tab-AI-Hashtag"}), 400
    context_type = data.get("context_type")
    if context_type not in {"draft", "today_journal"}:
        return jsonify({"error": "Bitte Kontext Aktuelles Textfeld oder Heutiges Journal wählen"}), 400
    submitted = data.get("text")
    if not isinstance(submitted, str) or not submitted.strip() or len(submitted) > 200000:
        return jsonify({"error": "Bitte einen gültigen Text eingeben"}), 400
    provider_id = str(data.get("provider_id") or workflow.get("provider_id") or "").strip()
    config = load_config()
    provider = next((item for item in config.get("ai_providers", []) if item.get("id") == provider_id), None)
    if not provider:
        return jsonify({"error": "Unbekannter AI-Anbieter"}), 400
    model = str(data.get("model") or provider.get("model") or "").strip()
    # Models are selected from the existing provider configuration, not supplied
    # freely by the browser.
    if not model or model != str(provider.get("model") or ""):
        return jsonify({"error": "Unbekanntes Modell für diesen Anbieter"}), 400

    draft_path = _write_draft_path(uid)
    expected_revision = data.get("revision")
    state = {}
    def begin(current):
        if expected_revision and expected_revision != _draft_revision(current):
            state["conflict"] = current
            return current
        state["base"] = submitted
        return submitted
    update_text_file(draft_path, begin)
    if "conflict" in state:
        current = state["conflict"]
        return jsonify({"error": "Der Schreibstand wurde auf einem anderen Gerät geändert.", "content": current, "revision": _draft_revision(current)}), 409

    if context_type == "today_journal":
        now, _ = get_tz_aware_now()
        journal_path = _get_journal_path(now, user_id=uid)
        context = read_text_file(journal_path) if journal_path.exists() else ""
    else:
        context = submitted
    prompt = f"{workflow.get('prompt', '').strip()}\n\nKontext ({'heutiges Journal' if context_type == 'today_journal' else 'aktuelles Textfeld'}):\n{context}"
    event = {"id": str(uuid.uuid4()), "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "workflow_tag": tag, "provider_id": provider_id, "model": model, "context_type": context_type, "status": "running"}
    _append_write_ai_history(uid, event)
    try:
        response = _call_ai_api(provider, {"system_prompt": workflow.get("prompt", "")}, prompt)
    except (ConnectionError, ValueError) as exc:
        event.update({"status": "error", "error": str(exc)[:500]})
        _append_write_ai_history(uid, event)
        return jsonify({"error": str(exc)}), 502

    result = {}
    def apply_response(current):
        # A second device may have saved while the request was in flight.  Preserve
        # that text below the atomic AI update instead of overwriting it.
        final = response if current == submitted else response + "\n\n" + current
        result["content"] = final
        return final
    update_text_file(draft_path, apply_response)
    event.update({"status": "completed"})
    _append_write_ai_history(uid, event)
    return jsonify({"ok": True, "response": response, "content": result["content"], "revision": _draft_revision(result["content"]), "provider_id": provider_id, "model": model, "context_type": context_type})


def _write_ai_markdown(content, ai_response, time_str, mode="append", datetime_str=None):
    """Write AI-generated entry to markdown.

    mode="replace": the AI response becomes the entry body (original text is replaced).
    mode="append": original text is kept, AI response appended as a quoted block.
    Legacy (mode=None or unknown): original "## KI-Antwort" block format.
    """
    timestamp = datetime_str or time_str
    if mode == "replace":
        return f"___\n\n## KI | Datum & Uhrzeit: {timestamp}\n{ai_response.strip()}\n\n___\n\n"
    if mode == "append":
        return f"___\n\n## KI | Datum & Uhrzeit: {timestamp}\n{content.strip()}\n\n---\n> {ai_response}\n\n___\n\n"
    return f"___\n\n## KI-Antwort | Datum & Uhrzeit: {timestamp}\n{content.strip()}\n\n---\n> {ai_response}\n\n___\n\n"


@app.route("/api/ai-submit", methods=["POST"])
@require_auth
@csrf_protect
def api_ai_submit():
    """Handle AI submission → call AI API → write response to markdown.

    New format:  { ai_provider_id, ai_function_id, text }
    Legacy:      { ai_template_id, message }
    """
    data = request.get_json()
    now, _ = get_tz_aware_now()
    time_str = now.strftime("%H:%M:%S")
    datetime_str = now.strftime("%Y-%m-%d %H:%M:%S")

    config = load_config()

    ai_provider_id = data.get("ai_provider_id")
    ai_function_id = data.get("ai_function_id")
    text = (data.get("text") or data.get("message") or "").strip()

    provider = None
    ai_function = None
    mode = "append"

    if ai_provider_id and ai_function_id:
        for p in config.get("ai_providers", []):
            if p["id"] == ai_provider_id:
                provider = p
                break
        if not provider:
            return jsonify({"error": "Unknown AI provider"}), 400
        for f in config.get("ai_functions", []):
            if f["id"] == ai_function_id:
                ai_function = f
                break
        if not ai_function:
            return jsonify({"error": "Unknown AI function"}), 400
        mode = ai_function.get("mode", "append")
    else:
        ai_template_id = data.get("ai_template_id")
        if not ai_template_id or not text:
            return jsonify({"error": "Missing ai_provider_id/ai_function_id or ai_template_id and text"}), 400
        ai_template = None
        for t in config.get("ai_templates", []):
            if t["id"] == ai_template_id:
                ai_template = t
                break
        if not ai_template:
            return jsonify({"error": "Unknown AI template"}), 400
        provider = ai_template
        ai_function = ai_template
        mode = "legacy"

    if not text:
        return jsonify({"error": "Missing text"}), 400

    # write_to_journal respektieren (default true fuer ai_templates)
    write_to_journal = True
    if ai_function and "write_to_journal" in ai_function:
        write_to_journal = bool(ai_function.get("write_to_journal"))
    elif provider and "write_to_journal" in provider:
        write_to_journal = bool(provider.get("write_to_journal"))

    try:
        ai_response = _call_ai_api(provider, ai_function, text)
    except (ConnectionError, ValueError) as e:
        logger.error(f"AI API error: {e}")
        return jsonify({"error": str(e)}), 500

    if not write_to_journal:
        logger.info("AI template write_to_journal=false – skipping journal write")
        return jsonify({"ok": True, "response": ai_response, "mode": mode})

    filepath = _get_journal_path(now)
    md_entry = _write_ai_markdown(text, ai_response, time_str, mode=mode, datetime_str=datetime_str)
    _append_journal_entry(_current_user()["id"], filepath, md_entry)

    logger.info("AI response written successfully")
    return jsonify({"ok": True, "response": ai_response, "mode": mode})


@app.route("/api/submit", methods=["POST"])
@require_auth
@csrf_protect
def api_submit():
    """Handle form submission → write clean markdown (no IDs)."""
    data = request.get_json()
    template_id = data.get("template_id") or data.get("form_id")
    content = data.get("content", "")
    values = data.get("values", {})

    logger.info(f"Submitting template {template_id}")

    config = load_config()
    template_def = None
    for t in config.get("templates", []):
        if t["id"] == template_id:
            template_def = t
            break

    if not template_def:
        logger.error(f"Unknown template: {template_id}")
        return jsonify({"error": "Unknown template"}), 400

    user = _current_user()
    uid = user["id"] if user else None
    assigned_users = template_def.get("assigned_users") or []
    if assigned_users and uid not in assigned_users:
        return jsonify({"error": "Template is not assigned to this user"}), 403

    project_assignment = data.get("project_assignment", "")
    if project_assignment and not template_def.get("project_selector"):
        return jsonify({"error": "This template does not support project assignment"}), 400
    if project_assignment:
        project_assignment = brain_module._valid_project_reference(uid, project_assignment)
        if project_assignment is None:
            return jsonify({"error": "Unknown personal project"}), 400

    now, _ = get_tz_aware_now()
    time_str = now.strftime("%H:%M:%S")
    datetime_str = now.strftime("%Y-%m-%d %H:%M:%S")

    write_to_journal = template_def.get("write_to_journal", True)
    target_file = template_def.get("target_file")
    # Family-Side-Effect: target_file → Task an Projekt-Datei anhaengen
    if target_file:
        title_val = values.get("titel") or values.get("item") or content or template_id
        user_uid = values.get("verantwortlich") or values.get("user") or uid
        target_date = values.get("target_date") or ""
        # Einkaufsliste: mehrere Items (eine pro Zeile) als einzelne Tasks anlegen
        if target_file.endswith("einkaufsliste.md") and values.get("item"):
            lines = [l.strip().lstrip("-*•·").strip() for l in str(values["item"]).splitlines() if l.strip()]
            for line in lines:
                try:
                    family_module.append_task_to_target_file(
                        target_file=target_file,
                        title=line,
                        user_uid=str(user_uid).strip() if user_uid else "",
                        target_date="",
                        created_by=uid,
                    )
                except Exception as e:
                    logger.error(f"Family append_task_to_target_file failed: {e}")
        else:
            try:
                family_module.append_task_to_target_file(
                    target_file=target_file,
                    title=str(title_val).strip() or template_id,
                    user_uid=str(user_uid).strip() if user_uid else "",
                    target_date=str(target_date).strip(),
                    created_by=uid,
                )
            except Exception as e:
                logger.error(f"Family append_task_to_target_file failed: {e}")

    planner_id = None

    # Aufgabenplaner → recurring-Eintrag im Planner
    if template_id == "aufgabenplaner":
        try:
            planner_id = family_module.add_recurring_to_planner(
                title=str(values.get("titel") or "").strip(),
                user_uid=str(values.get("user") or "").strip(),
                recurrence=str(values.get("recurrence") or "").strip(),
                target_date=str(values.get("target_date") or "").strip(),
                created_by=uid,
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Family add_recurring_to_planner failed: {e}")
            return jsonify({"error": "Plan konnte nicht gespeichert werden"}), 500

    # Projekt → eigenstaendige Projekt-Datei
    if template_id == "projekt":
        try:
            v_users_raw = str(values.get("verantwortlich") or "").strip()
            assigned = [u.strip() for u in v_users_raw.split(",") if u.strip()]
            family_module.create_standalone_project(
                title=str(values.get("titel") or "").strip() or template_id,
                template_id=template_id,
                assigned_users=assigned,
                created_by=uid,
            )
        except Exception as e:
            logger.error(f"Family create_standalone_project failed: {e}")

    # Kein Journal-Eintrag wenn write_to_journal false
    if not write_to_journal:
        logger.info(f"Template {template_id} write_to_journal=false – skipping journal write")
        response = {"ok": True}
        if planner_id:
            response["planner_id"] = planner_id
        return jsonify(response)

    filepath = _get_journal_path(now)

    # Build clean markdown (no UUID, no headers/separators)
    if template_def.get("type") == "simple":
        md_content = _write_markdown_entry(template_id, content, time_str, datetime_str)
    else:
        label = template_def.get("label", template_id)
        fields = template_def.get("fields", [])
        md_content = _write_form_markdown(label, time_str, fields, values, datetime_str)

    append_result = _append_journal_entry(uid, filepath, md_content) or {}
    ai_sessions = [append_result["agent_session"]] if append_result.get("agent_session") else []

    if project_assignment:
        brain_module.record_template_assignment(
            uid,
            filepath,
            project_assignment,
            content.strip().splitlines()[0] if template_id == "aufgabe" and content.strip() else None,
            append_result.get("line_hint"),
        )

    logger.info("File written successfully")
    return jsonify({"ok": True, "ai_sessions": ai_sessions})


@app.route("/api/dashboard/init", methods=["POST"])
@require_auth
@csrf_protect
def api_dashboard_init():
    """Initialize today's journal file if it doesn't exist."""
    now, _ = get_tz_aware_now()
    filepath = _get_journal_path(now)
    user = _current_user()

    def initialize(current):
        body = current or f"# Journal {now.strftime('%Y-%m-%d')}\n\n"
        return tagging_module.refresh_journal_footer(user["id"], body)

    _update_journal_file(filepath, initialize)

    return jsonify({"ok": True, "file": filepath.name, "path": str(filepath)})


def _get_today_entries(user_id=None):
    """Read and parse all today's markdown journal entries (fresh from disk)."""
    if user_id is None:
        user_id = session.get("user_id")
    now, _ = get_tz_aware_now()
    day_dir = _get_day_dir(now, user_id=user_id)
    all_entries = []

    if day_dir.is_dir():
        for f in sorted(day_dir.iterdir()):
            if f.name.startswith("Journal_") and f.suffix == ".md":
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        content = fh.read()
                    entries = _parse_entries_from_markdown(content)
                    for entry_text in entries:
                        all_entries.append({"content": entry_text, "file": f.name})
                except (IOError, OSError) as e:
                    logger.error(f"Failed to read journal file {f}: {e}")

    all_entries.reverse()
    return all_entries


@app.route("/api/dashboard")
@app.route("/api/dashboard/refresh")
@require_auth
def api_dashboard():
    """Read all today's markdown entries fresh from disk."""
    return jsonify(_get_today_entries())


@app.route("/api/dashboard/edit", methods=["POST"])
@require_auth
@csrf_protect
def api_dashboard_edit():
    """Edit a dashboard entry by replacing its content in the markdown file."""
    data = request.get_json()
    old_content = data.get("content", "")  # Original full text of entry (without ___)
    new_content = data.get("new_content", "")  # Edited version
    filename = data.get("filename")  # Optional: specific journal filename

    now, _ = get_tz_aware_now()

    if filename:
        filepath = _get_day_dir(now) / filename
    else:
        filepath = _get_journal_path(now)

    if not filepath.exists():
        return jsonify({"error": "No journal file"}), 404

    escaped_old = re.escape(old_content.strip()) + r'\s*'
    pattern = re.compile(r'(___\n\n)' + escaped_old + r'(\n\s*___)', re.DOTALL)
    replacement = r'\1' + new_content.strip() + r'\2'

    def replace_entry(current):
        updated = pattern.sub(replacement, current, count=1)
        if updated == current:
            return current, {"conflict": True}
        _write_journal_backup(filepath, current)
        return tagging_module.refresh_journal_footer(_current_user()["id"], updated), {"ok": True}

    result = _update_journal_file(filepath, replace_entry)
    if result.get("conflict"):
        return jsonify({"error": "Entry not found or modified in background", "conflict": True}), 409
    return jsonify({"ok": True})


@app.route("/api/dashboard/delete", methods=["POST"])
@require_auth
@csrf_protect
def api_dashboard_delete():
    """Delete a dashboard entry by removing its content from the markdown file."""
    data = request.get_json()
    content_to_delete = data.get("content", "")
    filename = data.get("filename")

    now, _ = get_tz_aware_now()

    if filename:
        filepath = _get_day_dir(now) / filename
    else:
        filepath = _get_journal_path(now)

    if not filepath.exists():
        return jsonify({"error": "No journal file"}), 404

    escaped_content = re.escape(content_to_delete.strip()) + r'\s*'
    pattern = re.compile(r'(___\n\n)' + escaped_content + r'(\n\s*___)', re.DOTALL)

    def delete_entry(current):
        updated = pattern.sub('', current, count=1)
        if updated == current:
            return current, {"conflict": True}
        _write_journal_backup(filepath, current)
        return tagging_module.refresh_journal_footer(_current_user()["id"], updated), {"ok": True}

    result = _update_journal_file(filepath, delete_entry)
    if result.get("conflict"):
        return jsonify({"error": "Entry not found or modified in background", "conflict": True}), 409
    return jsonify({"ok": True})


# ─── main ────────────────────────────────────────────────────────────

def _migrate_auth_user():
    """One-time migration of AUTH_USER/AUTH_PASS into users.json (T11).

    Runs if users.json is missing or has zero users AND the legacy env vars
    are set. The password is stored in plaintext (per spec) — user is prompted
    to change it via settings. Once users exist, AUTH_USER/AUTH_PASS are ignored.
    """
    if not _AUTH_USER or not _AUTH_PASS:
        return
    with _users_lock:
        data = _read_users_file()
        if data.get("users"):
            return  # users already exist — ignore legacy env
        now_iso = get_tz_aware_now()[0].isoformat()
        user = {
            "id": str(uuid.uuid4()),
            "username": _AUTH_USER.strip(),
            "password": _AUTH_PASS.strip(),  # plaintext intentionally (per spec)
            "admin": True,
            "consent": True,
            "consent_at": now_iso,
            "trusted_devices": [],
            "created_at": now_iso,
            "failed_login_attempts": [],
            "locked_until": None,
            "last_login_at": None,
            "failed_since_last_login": 0,
        }
        data["users"] = [user]
        _write_users_file(data)
    logger.warning(
        "Migrated AUTH_USER/AUTH_PASS to users.json as first user. "
        "Password is stored in plaintext — please change it via settings."
    )


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_auth_user()
    brain_module.start_index_worker(initial=True)
    app.run(host="0.0.0.0", port=4098, debug=False)
else:
    # WSGI imports do not execute the block above; start the elected index worker
    # after this module has finished defining its user and configuration helpers.
    brain_module.start_index_worker(initial=True)
