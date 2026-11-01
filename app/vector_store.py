"""Qdrant als Ablage der semantischen Vektoren — die einzige Stelle, die Qdrant kennt.

AI maintenance contract: kein Flask, kein Modell, keine Fachlogik. Spricht die
REST-API von Qdrant ueber ``urllib`` (kein zusaetzliches Paket). Adresse und
Schluessel kommen aus der Umgebung (``QDRANT_URL``, ``QDRANT_API_KEY``), nie aus
einem Request: der Dienst haengt nur im internen Docker-Netz.

Isolation: jede Person hat ihre eigene Collection (``journl_u_<user-id>``), die
Familie ``journl_family``. Den Namen bildet ausschliesslich ``user_collection``
aus einer serverseitig bekannten Nutzer-ID. Ein vergessener Filter kann deshalb
nichts Fremdes liefern; Familientreffer prueft der Aufrufer zusaetzlich gegen
die Sichtbarkeit.

Qdrant ist rebuildbar: Markdown bleibt die Quelle, ein Verlust kostet nur einen
Neuaufbau.
"""

import json
import os
import re
import uuid
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


FAMILY_COLLECTION = "journl_family"
USER_PREFIX = "journl_u_"
TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
SCROLL_PAGE = 512
# Felder, nach denen gefiltert wird. Ohne Payload-Index filtert Qdrant erst
# nach der Vektorsuche, und ein enger Zeitraum lieferte zu wenige Treffer.
PAYLOAD_INDEXES = {
    "source": "keyword", "day": "integer", "tags": "keyword", "path": "keyword", "model": "keyword",
}

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class VectorStoreError(RuntimeError):
    """Fehler, den die Oberflaeche wortwoertlich zeigen darf."""


def base_url():
    return str(os.environ.get("QDRANT_URL") or "").strip().rstrip("/")


def available():
    return bool(base_url())


def user_collection(user_id):
    user_id = str(user_id or "")
    if not _USER_ID_RE.fullmatch(user_id):
        raise VectorStoreError("Ungültige Nutzer-ID für die Vektorablage.")
    return USER_PREFIX + user_id


def _call(method, path, payload=None, *, missing_ok=False):
    root = base_url()
    if not root:
        raise VectorStoreError("Die Vektor-DB ist nicht eingerichtet (QDRANT_URL fehlt).")
    headers = {"Accept": "application/json"}
    api_key = str(os.environ.get("QDRANT_API_KEY") or "").strip()
    if api_key:
        headers["api-key"] = api_key
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urlrequest.Request(root + path, data=data, method=method, headers=headers)
    opener = urlrequest.build_opener(urlrequest.ProxyHandler({}))
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urlerror.HTTPError as exc:
        if missing_ok and exc.code == 404:
            return None
        detail = ""
        try:
            detail = json.loads(exc.read(4096).decode("utf-8")).get("status", {}).get("error", "")
        except (ValueError, AttributeError, OSError):
            pass
        raise VectorStoreError("Vektor-DB antwortet mit HTTP {}{}".format(
            exc.code, ": " + detail if detail else "",
        )) from exc
    except (urlerror.URLError, OSError) as exc:
        raise VectorStoreError("Vektor-DB nicht erreichbar.") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise VectorStoreError("Antwort der Vektor-DB ist unerwartet gross.")
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VectorStoreError("Vektor-DB hat kein gültiges JSON geliefert.") from exc
    return body.get("result", body) if isinstance(body, dict) else body


def _path(name, suffix=""):
    if not re.fullmatch(r"journl_[A-Za-z0-9_-]{1,80}", name or ""):
        raise VectorStoreError("Ungültiger Collection-Name.")
    return "/collections/" + urlparse.quote(name) + suffix


def health():
    """Version und Erreichbarkeit — fuer die Statuszeile der Einstellungen."""
    result = _call("GET", "/")
    version = result.get("version", "") if isinstance(result, dict) else ""
    return {"ok": True, "version": str(version)}


def collection_info(name):
    """``None``, wenn es die Collection nicht gibt, sonst Breite und Punktzahl."""
    result = _call("GET", _path(name), missing_ok=True)
    if not isinstance(result, dict):
        return None
    vectors = (result.get("config") or {}).get("params", {}).get("vectors") or {}
    return {
        "dims": vectors.get("size"),
        "points": result.get("points_count") or 0,
        "status": result.get("status", ""),
    }


def ensure_collection(name, dims):
    """Collection anlegen oder pruefen. Eine andere Breite ist ein Fehler, kein Mischen."""
    info = collection_info(name)
    if info is None:
        _call("PUT", _path(name), {"vectors": {"size": int(dims), "distance": "Cosine"}})
        for field, schema in PAYLOAD_INDEXES.items():
            _call("PUT", _path(name, "/index?wait=true"), {"field_name": field, "field_schema": schema})
        return {"dims": int(dims), "points": 0, "created": True}
    if info["dims"] != int(dims):
        raise VectorStoreError(
            "Die Collection {} hat {} statt {} Dimensionen – bitte komplett neu aufbauen.".format(
                name, info["dims"], dims,
            )
        )
    return {**info, "created": False}


def delete_collection(name):
    _call("DELETE", _path(name), missing_ok=True)


def upsert(name, points):
    """``points``: ``[{"id": uuid, "vector": [...], "payload": {...}}]``."""
    if points:
        _call("PUT", _path(name, "/points?wait=true"), {"points": points})


def delete_points(name, point_ids):
    ids = list(point_ids)
    for start in range(0, len(ids), SCROLL_PAGE):
        _call("POST", _path(name, "/points/delete?wait=true"), {"points": ids[start:start + SCROLL_PAGE]})


def stored_hashes(name):
    """``{point_id: (semantic_hash, model)}`` aller Punkte — fuer den Abgleich."""
    found = {}
    offset = None
    while True:
        body = {
            "limit": SCROLL_PAGE,
            "with_payload": {"include": ["semantic_hash", "model"]},
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        result = _call("POST", _path(name, "/points/scroll"), body, missing_ok=True)
        if not isinstance(result, dict):
            return found
        for point in result.get("points") or []:
            payload = point.get("payload") or {}
            found[str(point.get("id"))] = (payload.get("semantic_hash", ""), payload.get("model", ""))
        offset = result.get("next_page_offset")
        if offset is None:
            return found


def day_number(value):
    """``"2026-03-14"`` -> ``20260314``; Unlesbares wird 0 (vor jedem Zeitraum)."""
    digits = str(value or "")[:10].replace("-", "")
    return int(digits) if len(digits) == 8 and digits.isdigit() else 0


def _filter(sources=None, start_date=None, end_date=None, tags=None, model=None):
    must = []
    if sources:
        must.append({"key": "source", "match": {"any": list(sources)}})
    if start_date or end_date:
        # ``day`` ist das Datum als Zahl JJJJMMTT: ein Bereichsfilter braucht
        # einen Zahlenindex, Text vergleicht Qdrant nur exakt.
        bounds = {}
        if start_date:
            bounds["gte"] = day_number(start_date)
        if end_date:
            bounds["lte"] = day_number(end_date)
        must.append({"key": "day", "range": bounds})
    for tag in tags or []:
        must.append({"key": "tags", "match": {"value": tag}})
    if model:
        must.append({"key": "model", "match": {"value": model}})
    return {"must": must} if must else None


def point_vector(name, point_id):
    """Der gespeicherte Vektor eines Punkts, ``None`` wenn es ihn (noch) nicht gibt."""
    result = _call("GET", _path(name, "/points/" + str(uuid.UUID(str(point_id)))), missing_ok=True)
    vector = result.get("vector") if isinstance(result, dict) else None
    return [float(value) for value in vector] if isinstance(vector, list) and vector else None


def search(name, vector, *, limit=50, sources=None, start_date=None, end_date=None,
           tags=None, model=None, min_score=None):
    """Aehnlichste Punkte einer Collection, absteigend nach Kosinus."""
    body = {"query": vector, "limit": int(limit), "with_payload": True, "with_vector": False}
    query_filter = _filter(sources, start_date, end_date, tags, model)
    if query_filter:
        body["filter"] = query_filter
    if min_score is not None:
        body["score_threshold"] = float(min_score)
    result = _call("POST", _path(name, "/points/query"), body, missing_ok=True)
    points = result.get("points") if isinstance(result, dict) else None
    return [
        {"id": str(point.get("id")), "score": float(point.get("score") or 0), "payload": point.get("payload") or {}}
        for point in points or []
    ]
