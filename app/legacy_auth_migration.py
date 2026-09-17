"""Einmalige Uebernahme von ``AUTH_USER``/``AUTH_PASS`` in ``users.json`` (T11).

AI maintenance contract: reine Altlast. Dieser Pfad laeuft genau einmal — bei
einer Installation, die noch aus der Zeit vor der Mehrbenutzer-Ablage stammt —
und danach nie wieder. Er gehoert deshalb nicht in die Fassade, sondern
hierher, wo er beim Lesen von main.py niemanden mehr aufhaelt.

Er greift ausschliesslich ueber die Fassade auf Benutzerablage und Sperre zu:
die Schreibprimitive fuer ``users.json`` haben genau einen Besitzer.
"""

import logging
import sys
import uuid

logger = logging.getLogger(__name__)


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def migrate():
    """Laeuft, wenn users.json fehlt oder leer ist **und** die Alt-Variablen gesetzt sind.

    Das Passwort wird im Klartext abgelegt (so spezifiziert) — der Nutzer wird
    aufgefordert, es in den Einstellungen zu wechseln. Sobald Benutzer
    existieren, werden AUTH_USER/AUTH_PASS ignoriert.
    """
    main = _main()
    if main is None:  # pragma: no cover - only reachable without the facade
        return
    auth_user = getattr(main, "_AUTH_USER", "")
    auth_pass = getattr(main, "_AUTH_PASS", "")
    if not auth_user or not auth_pass:
        return
    with main._users_lock:
        data = main._read_users_file()
        if data.get("users"):
            return  # users already exist — ignore legacy env
        now_iso = main.get_tz_aware_now()[0].isoformat()
        data["users"] = [{
            "id": str(uuid.uuid4()),
            "username": auth_user.strip(),
            "password": auth_pass.strip(),  # plaintext intentionally (per spec)
            "admin": True,
            "consent": True,
            "consent_at": now_iso,
            "trusted_devices": [],
            "created_at": now_iso,
            "failed_login_attempts": [],
            "locked_until": None,
            "last_login_at": None,
            "failed_since_last_login": 0,
        }]
        main._write_users_file(data)
    logger.warning(
        "Migrated AUTH_USER/AUTH_PASS to users.json as first user. "
        "Password is stored in plaintext — please change it via settings."
    )


__all__ = ["migrate"]
