"""Meldet dem Webinterface, welche Modelle auf dem Host tatsaechlich nutzbar sind.

Die Weboberflaeche laeuft im Container und kann weder ``pi`` noch ``claude``
aufrufen. Der Host-Worker schreibt deshalb in regelmaessigen Abstaenden eine
kleine Uebersicht nach ``<data_root>/host_targets.json``, die die App liest.

Bewusst nur lesend und ohne Nebenwirkung: Faellt der Aufruf aus, bleibt die
letzte Fassung stehen und die Oberflaeche zeigt eben den vorherigen Stand.
"""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path


FILENAME = "host_targets.json"
REFRESH_SECONDS = 1800
LIST_TIMEOUT = 60
MAX_MODELS = 400
# `pi --list-models` schreibt eine Tabelle: provider, model, dann Kennzahlen.
_ROW = re.compile(r"^(?P<provider>[A-Za-z0-9_.-]+)\s+(?P<model>\S+)\s+\S+\s+\S+\s+(?P<thinking>yes|no)\s")


def _target_path(data_root):
    return Path(data_root) / FILENAME


def _is_fresh(path):
    try:
        return (time.time() - path.stat().st_mtime) < REFRESH_SECONDS
    except OSError:
        return False


def pi_models(*, run=subprocess.run):
    """Modelle aus ``pi --list-models``; leere Liste, wenn pi fehlt."""
    try:
        completed = run(["pi", "--list-models"], capture_output=True, text=True,
                        timeout=LIST_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    models = []
    for line in (completed.stdout or "").splitlines():
        match = _ROW.match(line.strip())
        if not match or match.group("provider") == "provider":
            continue
        models.append({
            "id": f"{match.group('provider')}/{match.group('model')}",
            "provider": match.group("provider"),
            "thinking": match.group("thinking") == "yes",
        })
        if len(models) >= MAX_MODELS:
            break
    return models


def _atomic_json(path, payload):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o660)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh(data_root, *, run=subprocess.run, force=False):
    """Uebersicht schreiben, wenn die alte zu alt ist. Gibt den Pfad zurueck."""
    path = _target_path(data_root)
    if not force and _is_fresh(path):
        return None
    payload = {
        "schema": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tools": {
            "pi": {"available": bool(_which("pi", run=run)), "models": pi_models(run=run)},
            "claude": {"available": bool(_which("claude", run=run))},
            "opencode": {"available": bool(_which("opencode", run=run))},
        },
    }
    try:
        _atomic_json(path, payload)
    except OSError:
        return None
    return path


def _which(name, *, run=subprocess.run):
    try:
        completed = run(["which", name], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip() if completed.returncode == 0 else ""
