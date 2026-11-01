"""Journl Flask composition root and compatibility facade.

AI maintenance contract: keep application construction, shared runtime state,
authentication primitives and cross-domain write guards here. Feature routes
belong in the focused main_* modules loaded near the end of this file.
"""

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
from pathlib import Path, PurePosixPath

from flask import (Flask, render_template, request, jsonify, session, make_response,
                   request as flask_request)
from werkzeug.security import check_password_hash, generate_password_hash

import asset_delivery
import family as family_module
from family import family_bp
import brain as brain_module
from brain import brain_bp
import ai_sessions as ai_sessions_module
from ai_sessions import ai_sessions_bp
import write_sessions as write_sessions_module
from write_sessions import write_sessions_bp
import ai_attachments as ai_attachments_module
import transcription_jobs as transcription_jobs_module  # noqa: F401  (registers the retranscribe route)
import transcription_schedule as transcription_schedule_module  # noqa: F401  (registers the schedule route)
from screenshot_inbox import screenshot_inbox_bp
import share_api as share_api_module
from share_api import share_api_bp
from worker_api import worker_api_bp
from agent_project_routes import agent_projects_bp
from tickets import tickets_bp
from agent_workbench import worker_bp
from buckets import buckets_bp
from screensaver import screensaver_bp
from screensaver_questions import questions_bp as screensaver_questions_bp
from screensaver_presets import presets_bp as screensaver_presets_bp
from screensaver_player import player_bp as screensaver_player_bp
import background_runs
from background_runs_routes import background_runs_bp
import journal_analysis as journal_analysis_module  # noqa: F401
import journal_analysis_manual as journal_analysis_manual_module  # noqa: F401  (registers the run handler)
from journal_analysis_routes import journal_analysis_bp
from voice_routes import voice_bp
from pushover_routes import pushover_bp
import tagging as tagging_module
from scheduling import update_text_file, write_text_file, read_text_file
from db import agent_jobs_enabled, database_health, database_url, is_enabled
import background_jobs

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
app.register_blueprint(write_sessions_bp)
app.register_blueprint(screenshot_inbox_bp)
app.register_blueprint(share_api_bp)
app.register_blueprint(worker_api_bp)
app.register_blueprint(agent_projects_bp)
app.register_blueprint(tickets_bp)
app.register_blueprint(worker_bp)
app.register_blueprint(buckets_bp)
app.register_blueprint(screensaver_bp)
app.register_blueprint(screensaver_questions_bp)
app.register_blueprint(screensaver_presets_bp)
app.register_blueprint(screensaver_player_bp)
app.register_blueprint(background_runs_bp)
app.register_blueprint(journal_analysis_bp)
app.register_blueprint(voice_bp)
app.register_blueprint(pushover_bp)


class JournalWriteConflict(RuntimeError):
    """A managed journal changed outside the submitted browser revision."""

    def __init__(self, conflict_id):
        super().__init__("Journal wurde parallel geaendert; bitte Konflikt aufloesen")
        self.conflict_id = conflict_id


@app.errorhandler(JournalWriteConflict)
def _journal_write_conflict_response(error):
    return jsonify({"error": str(error), "conflict": True, "conflict_id": str(error.conflict_id)}), 409

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
    # Seit Tailwind als fertiges Stylesheet aus /static kommt, laedt die App
    # nichts mehr von fremden Hosts - die CSP bleibt entsprechend bei 'self'.
    # media-src erlaubt blob:, weil das Vorlesen das TTS-Audio als Blob im
    # Player abspielt; ohne die Angabe galt default-src und der Browser
    # verweigerte die Wiedergabe („kann der Browser nicht abspielen“).
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "media-src 'self' blob:; "
        "font-src 'self';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Inline DEV UI changes must reach mobile pilot testers immediately. Static
    # assets keep their normal caching behavior and are cache-busted separately.
    if IS_DEV and request.path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


# gzip und Cache-Header fuer die Frontend-Dateien; siehe app/asset_delivery.py.
asset_delivery.register(app, IS_DEV)


@app.context_processor
def inject_env():
    """Make the development-stage flags available in all templates.

    ``DB_SEARCH_ENABLED`` spiegelt genau die Bedingung von
    ``/api/journal-search``: der Knopf "Journal DB" erscheint nur, wenn ein
    aktiver Suchindex antworten kann — sonst beantwortete er jeden Klick mit
    "Journalsuche ist nicht aktiv".
    """
    return {
        "IS_DEV": IS_DEV,
        "DB_SEARCH_ENABLED": is_enabled(),
        # Cache-Buster fuer den Service Worker: die Registrierung haengt die
        # Version an /sw.js, der Worker benennt damit seinen Cache und wirft
        # beim Aktivieren alle alten Staende weg. Ohne das sammelte der
        # Cache-Name 'journl-static-v1' jede Asset-Version unbegrenzt an.
        "APP_VERSION": load_app_version(),
    }

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
        for key in ["api_url", "model", "system_prompt", "max_tokens", "temperature", "thinking_budget",
                    "api_key", "mode", "label"]:
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


def _update_journal_file(filepath, updater, user_id=None):
    """Mutate a journal source under the lock shared with Brain and scheduling."""
    managed = _managed_document_for_journal(filepath, user_id=user_id)
    if managed:
        from document_store import DocumentConflict, configured_document_store

        current = read_text_file(filepath)
        update = updater(current)
        if isinstance(update, tuple):
            updated, result = update
        else:
            updated, result = update, None
        if updated == current:
            return result
        engine = managed["engine"]
        try:
            configured_document_store(engine).write(
                document_id=managed["document_id"],
                storage_root_key=managed["storage_root_key"],
                root=managed["root"],
                content=updated,
                idempotency_key=str(uuid.uuid4()),
                expected_file_hash=hashlib.sha256(current.encode("utf-8")).hexdigest(),
            )
        except DocumentConflict as error:
            from conflicts import record_conflict
            from sqlalchemy import text

            external = read_text_file(filepath)
            with engine.begin() as connection:
                state = connection.scalar(text("SELECT state FROM documents WHERE id = :id FOR UPDATE"),
                                          {"id": managed["document_id"]})
                if state == "conflict":
                    conflict_id = connection.scalar(text("""
                        SELECT id FROM sync_conflicts
                        WHERE document_id = :document AND status = 'open'
                        ORDER BY created_at DESC LIMIT 1
                    """), {"document": managed["document_id"]})
                else:
                    conflict_id = record_conflict(
                        connection, document_id=managed["document_id"],
                        base_revision_id=managed["base_revision_id"], base_content=current,
                        external_content=external, jtracker_content=updated,
                        now=datetime.now(timezone.utc),
                    )
            raise JournalWriteConflict(conflict_id) from error
        finally:
            engine.dispose()
        return result
    try:
        return update_text_file(filepath, updater)
    except (IOError, OSError) as e:
        logger.error(f"Failed to update journal file {filepath}: {e}")
        raise


def _managed_document_for_journal(filepath, user_id=None):
    """Return the server-authorized managed document for a current DEV journal."""
    if not IS_DEV or not is_enabled():
        return None
    user_id = user_id or session.get("user_id")
    if not user_id:
        return None
    root = (DATA_DIR / user_id).resolve()
    try:
        relative_path = Path(filepath).resolve().relative_to(root).as_posix()
    except ValueError:
        return None
    engine = None
    try:
        from sqlalchemy import create_engine, text

        # DocumentStore keeps one connection for its PostgreSQL advisory lock
        # while opening a second short transaction for prepare/finalize.
        engine = create_engine(database_url(), pool_pre_ping=True, pool_size=2, max_overflow=0)
        with engine.connect() as connection:
            document = connection.execute(text("""
                    SELECT document.id, document.storage_mode, document.state, document.current_revision_id,
                           (SELECT id FROM sync_conflicts
                            WHERE document_id = document.id AND status = 'open'
                            ORDER BY created_at DESC LIMIT 1) AS open_conflict_id
                    FROM documents document
                    WHERE scope = 'personal' AND storage_root_key = :root AND relative_path = :path
                      AND state <> 'deleted'
                """), {"root": "dev-user-" + user_id, "path": relative_path}).mappings().one_or_none()
    except Exception as error:
        if engine is not None:
            engine.dispose()
        raise RuntimeError("Verwaltetes Journal kann derzeit nicht sicher geschrieben werden") from error
    if not document or document["storage_mode"] != "db_managed":
        engine.dispose()
        return None
    if document["state"] == "conflict":
        engine.dispose()
        if document["open_conflict_id"] is not None:
            raise JournalWriteConflict(document["open_conflict_id"])
        raise RuntimeError("Journal-Konflikt ist unvollständig und muss geprüft werden")
    return {
        "engine": engine,
        "document_id": document["id"],
        "base_revision_id": document["current_revision_id"],
        "storage_root_key": "dev-user-" + user_id,
        "root": root,
    }


def _owned_managed_conflict(conflict_id):
    """Load one personal conflict only from the logged-in user's DEV root."""
    if not IS_DEV or not is_enabled() or not session.get("user_id"):
        return None
    try:
        conflict_uuid = uuid.UUID(str(conflict_id))
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url(), pool_pre_ping=True, pool_size=2, max_overflow=0)
        with engine.connect() as connection:
            conflict = connection.execute(text("""
                SELECT conflict.id, conflict.document_id, conflict.status, conflict.resolution,
                       document.storage_root_key
                FROM sync_conflicts conflict JOIN documents document ON document.id = conflict.document_id
                WHERE conflict.id = :id AND document.scope = 'personal'
                  AND document.storage_root_key = :root
            """), {"id": conflict_uuid, "root": "dev-user-" + session["user_id"]}).mappings().one_or_none()
        if conflict is None:
            engine.dispose()
            return None
        return engine, conflict
    except Exception:
        if 'engine' in locals():
            engine.dispose()
        raise


def _owned_managed_journal(journal_date):
    """Load one db-managed personal journal by ISO date for the current user."""
    if not IS_DEV or not is_enabled() or not session.get("user_id"):
        return None
    if not isinstance(journal_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", journal_date):
        return None
    try:
        date_value = datetime.strptime(journal_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    user_id = session["user_id"]
    relative_path = "{:%Y/%m/%d}/Journal_{}.md".format(date_value, journal_date)
    engine = None
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url(), pool_pre_ping=True, pool_size=2, max_overflow=0)
        with engine.connect() as connection:
            document = connection.execute(text("""
                SELECT id, current_revision_id, materialized_file_hash, state
                FROM documents WHERE scope = 'personal' AND storage_root_key = :root
                  AND relative_path = :path AND storage_mode = 'db_managed' AND state <> 'deleted'
            """), {"root": "dev-user-" + user_id, "path": relative_path}).mappings().one_or_none()
        if document is None:
            engine.dispose()
            return None
        path = (DATA_DIR / user_id / relative_path).resolve()
        path.relative_to((DATA_DIR / user_id).resolve())
        return engine, document, path
    except Exception:
        if engine is not None:
            engine.dispose()
        raise


def _append_journal_entry(user_id, filepath, entry):
    """Append ahead of the technical footer and rebuild its canonical tag map."""
    date_match = re.fullmatch(r"Journal_(\d{4}-\d{2}-\d{2})\.md", filepath.name)
    journal_date = date_match.group(1) if date_match else None

    def append(current):
        _write_journal_backup(filepath, current)
        body, existing_footer = tagging_module.strip_footer(current)
        updated = body.rstrip() + "\n\n" + entry
        tagging_module.propose_tags(
            user_id,
            [tag for block in tagging_module.journal_blocks(updated, journal_date) for tag in block["raw_tags"]],
        )
        return tagging_module.refresh_journal_footer(
            user_id, updated, journal_date, existing_footer
        ), {"line_hint": body.count("\n") + 1}

    return _update_journal_file(filepath, append, user_id=user_id)


_TEMPLATE_FORMATTERS = {
    "aufgabe": lambda c, t, d: f"___\n\n## Aufgabe | Datum & Uhrzeit: {d}\n- [ ] {c.strip()}\n\n___\n\n",
    "nebengedanke": lambda c, t, d: f"___\n\n## Nebengedanke | Datum & Uhrzeit: {d}\n{c.strip()}\n\n___\n\n",
    "thema": lambda c, t, d: f"___\n\n## Thema | Datum & Uhrzeit: {d}\n{c.strip()}\n\n___\n\n",
    "brainablage": lambda c, t, d: f"___\n\n## Brainablage: Tags: {c.strip()} **Datum & Uhrzeit: {d}**\n\n___\n\n",
    "idee": lambda c, t, d: f"___\n\n## Idee | Datum & Uhrzeit: {d}\n{c.strip()}\n\n___\n\n",
    "erkenntnis": lambda c, t, d: f"___\n\n## Erkenntnis | Datum & Uhrzeit: {d}\n{c.strip()}\n\n___\n\n",
}


def _write_markdown_entry(template_id, content, time_str, datetime_str, quick_started_at=None, quick_style=False,
                          heading=None):
    """Write markdown entry with separators using template mapping.

    ``heading`` kommt aus der Vorlagenverwaltung und gibt neu angelegten
    einfachen Vorlagen dieselbe Ueberschriftenform wie den oben fest
    verdrahteten. Die verdrahteten Formatter haben Vorrang, damit bestehende
    Journale ihre gewohnte Form behalten.
    """
    if template_id == "schnell" or quick_style:
        ended_at = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S").strftime("%d.%m. %H:%M:%S")
        started_at = quick_started_at or ended_at
        return f"___\n\n- {started_at}\n{content.strip()}\n~~{ended_at}~~\n\n___\n\n"
    formatter = _TEMPLATE_FORMATTERS.get(template_id)
    if formatter:
        return formatter(content, time_str, datetime_str)
    heading = (heading or "").strip()
    if heading:
        return f"___\n\n## {heading} | Datum & Uhrzeit: {datetime_str}\n{content.strip()}\n\n___\n\n"
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


# Domain route modules are loaded after the core helpers above. Keep this order:
# core routes -> AI helpers -> AI routes -> journal/history routes -> journal
# days. Each module
# publishes its compatibility surface back through main.py for existing callers.
import main_core_routes as _main_core_routes
for _export_name in _main_core_routes.__all__:
    globals()[_export_name] = getattr(_main_core_routes, _export_name)

import main_ai as _main_ai
for _export_name in _main_ai.__all__:
    globals()[_export_name] = getattr(_main_ai, _export_name)

import main_ai_routes as _main_ai_routes
for _export_name in _main_ai_routes.__all__:
    globals()[_export_name] = getattr(_main_ai_routes, _export_name)


import main_journal_routes as _main_journal_routes
for _export_name in _main_journal_routes.__all__:
    globals()[_export_name] = getattr(_main_journal_routes, _export_name)

# Day availability comes last: it reads _get_today_entries off the facade above.
import main_journal_days as _main_journal_days
for _export_name in _main_journal_days.__all__:
    globals()[_export_name] = getattr(_main_journal_days, _export_name)


# The MCP endpoint reuses the journal search helpers defined above and the Brain
# facade, so it is wired last and registered here rather than beside the
# blueprints at the top of this module.
import mcp_tools as _mcp_tools
import mcp_server as _mcp_server
app.register_blueprint(_mcp_server.mcp_bp)

# Der Brain-Chat ist ein App-interner MCP-Client: er ruft dieselben Werkzeuge
# ohne Token in der Sitzung des angemeldeten Benutzers auf und wird deshalb
# hinter mcp_server geladen.
import brain_chat as _brain_chat
import brain_chat_routes as _brain_chat_routes
app.register_blueprint(_brain_chat_routes.brain_chat_bp)
import shopping_agent_routes as _shopping_agent_routes
app.register_blueprint(_shopping_agent_routes.shopping_agent_bp)
# Der Schreib-Agent teilt sich Anbieterauswahl und Wissenswerkzeuge mit dem
# Einkaufslisten-Agenten und laedt deshalb hinter ihm.
import write_agent_routes as _write_agent_routes
app.register_blueprint(_write_agent_routes.write_agent_bp)
# Die semantische Suche liest ueber die Brain-Fassade und registriert ihren
# Indexlauf bei background_runs; sie haengt deshalb hinter allen Brain-Modulen.
import semantic_routes as _semantic_routes
app.register_blueprint(_semantic_routes.semantic_bp)


# Die einmalige Uebernahme von AUTH_USER/AUTH_PASS in users.json ist Altlast
# und liegt deshalb in einem eigenen Modul.
import legacy_auth_migration as _legacy_auth_migration
_migrate_auth_user = _legacy_auth_migration.migrate


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_auth_user()
    brain_module.start_index_worker(initial=True)
    share_api_module.start_worker()
    # Serverseitige Laeufe ueberleben keinen Neustart: was noch als "laeuft"
    # dasteht, ist tot und wuerde sonst fuer immer den naechsten Lauf blockieren.
    background_runs.abort_stale_runs()
    # threaded=True keeps one slow request (an MCP tool call, a database query)
    # from blocking every other caller. This stays a single process, so the
    # in-memory rate limits, background workers and user store keep their
    # existing lock-based guarantees.
    app.run(host="0.0.0.0", port=4098, debug=False, threaded=True)
else:
    # WSGI imports do not execute the block above; start the elected index worker
    # after this module has finished defining its user and configuration helpers.
    brain_module.start_index_worker(initial=True)
    share_api_module.start_worker()
    background_runs.abort_stale_runs()
