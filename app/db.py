"""Small synchronous PostgreSQL boundary for Phase 1.

No route or legacy file writer uses this module for business data yet.  Imports of
third-party database packages stay inside functions so a disabled DB cannot break
existing file-only unit tests.
"""

import os
import threading
from urllib.parse import quote_plus


EXPECTED_SCHEMA_REVISION = "0010_agent_attachments"

_health_engine = None
_health_engine_url = None
_health_engine_lock = threading.Lock()


def is_enabled():
    return os.environ.get("JOURNL_DB_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def agent_jobs_enabled():
    """Enable DB-native agent jobs only when the base DB is enabled too."""
    return is_enabled() and os.environ.get("JOURNL_DB_AGENT_JOBS", "false").strip().lower() in {"1", "true", "yes", "on"}


def database_url():
    explicit = os.environ.get("JOURNL_DATABASE_URL", "").strip()
    if explicit:
        return explicit
    user = os.environ.get("POSTGRES_USER", "journl").strip()
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "journl").strip()
    host = os.environ.get("POSTGRES_HOST", "postgres-dev").strip()
    port = os.environ.get("POSTGRES_PORT", "5432").strip()
    if not password:
        raise RuntimeError("POSTGRES_PASSWORD fehlt")
    return "postgresql+psycopg://{}:{}@{}:{}/{}".format(
        quote_plus(user), quote_plus(password), host, quote_plus(port), quote_plus(database)
    )


def _health_engine_instance():
    """Reuse one pooled engine: /api/health runs on every app start.

    Building and disposing an engine per call made the PWA's first request pay
    for a fresh connection every time. pool_pre_ping keeps stale connections
    from leaking back out of the pool. The engine is keyed by its URL so a
    reconfigured target is never answered from the previous connection.
    """
    global _health_engine, _health_engine_url
    url = database_url()
    if _health_engine is None or _health_engine_url != url:
        with _health_engine_lock:
            if _health_engine is None or _health_engine_url != url:
                from sqlalchemy import create_engine

                previous = _health_engine
                _health_engine = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=1)
                _health_engine_url = url
                if previous is not None:
                    previous.dispose()
    return _health_engine


def database_health():
    """Return a secret-free status object without changing database state."""
    if not is_enabled():
        return {"enabled": False, "status": "disabled", "schema_revision": None, "healthy": True}
    try:
        from sqlalchemy import text

        with _health_engine_instance().connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.scalar(text("SELECT version_num FROM journl_alembic_version"))
    except Exception:
        return {"enabled": True, "status": "unavailable", "schema_revision": None, "healthy": False}
    compatible = revision == EXPECTED_SCHEMA_REVISION
    return {
        "enabled": True,
        "status": "healthy" if compatible else "schema_incompatible",
        "schema_revision": revision,
        "healthy": compatible,
    }
