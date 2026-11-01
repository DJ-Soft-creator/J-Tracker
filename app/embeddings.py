"""Text-Einbettungen ueber einen OpenAI-kompatiblen ``/v1/embeddings``-Endpunkt.

AI maintenance contract: kein Flask, keine Ablage, keine Einstellungen. Das
Modul nimmt eine Verbindung (``base_url``, ``model``, ``api_key``) und Texte und
liefert normierte Vektoren der festen Breite ``DIMS``. Gateway-Fakten (Profil,
Zeitverhalten, Verbote) stehen in ``docs/features/embedding-gateway-llama-swap.md``.

Drei Regeln, die die Trefferqualitaet tragen:

- Suchanfragen bekommen genau eine feste Instruct-Zeile, Dokumente gehen roh
  hinein (Qwen3-Embedding ist asymmetrisch trainiert).
- llama-server ignoriert ``dimensions``. Gekuerzt wird hier: die ersten
  ``DIMS`` Komponenten nehmen und neu normieren (Matryoshka). Ein Index mischt
  nie zwei Breiten.
- Die Basis-URL wird bei jedem Aufruf erneut auf das lokale Netz begrenzt
  (``agent_projects.validated_base_url``): Journaltexte verlassen das LAN nicht.
"""

import json
import math
import threading
import time
from collections import OrderedDict
from urllib import error as urlerror
from urllib import request as urlrequest

import agent_projects


DIMS = 1024
QUERY_INSTRUCT = (
    "Instruct: Given a personal journal archive, retrieve entries that answer the query\n"
    "Query: "
)
# Kaltstart des 4-GB-Modells dauert gemessen 24 s; ein Chatprofil auf diesem
# Endpunkt haengt ~90 s und endet mit 502. 60 s trennen beides.
TIMEOUT_SECONDS = 60
RETRIES = 2
RETRY_PAUSE_SECONDS = 2
BATCH_SIZE = 8
# Harte Backend-Grenze sind 8192 Token je Request; 6000 Zeichen liegen auch bei
# dichtem Text weit darunter. Laengere Texte zerlegt semantic_index vorher.
MAX_TEXT_CHARS = 6000
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
QUERY_CACHE_SIZE = 256


class EmbeddingError(RuntimeError):
    """Fehler, den die Oberflaeche wortwoertlich zeigen darf."""


def truncate(vector, dims=DIMS):
    """Auf ``dims`` Komponenten kuerzen und L2-renormieren."""
    if not isinstance(vector, list) or len(vector) < dims:
        raise EmbeddingError(
            "Das Modell liefert {} statt mindestens {} Dimensionen.".format(
                len(vector) if isinstance(vector, list) else 0, dims,
            )
        )
    head = [float(value) for value in vector[:dims]]
    norm = math.sqrt(sum(value * value for value in head))
    if not norm:
        raise EmbeddingError("Das Modell hat einen Nullvektor geliefert.")
    return [value / norm for value in head]


def _endpoint(connection):
    try:
        base = agent_projects.validated_base_url(connection.get("base_url"))
    except agent_projects.AgentProjectError as exc:
        raise EmbeddingError(str(exc)) from exc
    model = str(connection.get("model") or "").strip()
    if not agent_projects.valid_model_name(model):
        raise EmbeddingError("Kein Embedding-Modell eingestellt.")
    return base + "/v1/embeddings", model


def _post(url, payload, api_key, timeout):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    data = json.dumps(payload).encode("utf-8")
    request = urlrequest.Request(url, data=data, method="POST", headers=headers)
    # Kein Proxy: das Ziel ist ausdruecklich ein Rechner im lokalen Netz.
    opener = urlrequest.build_opener(urlrequest.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise EmbeddingError("Die Antwort des Embedding-Endpunkts ist unerwartet gross.")
    return json.loads(raw.decode("utf-8"))


def _describe(exc):
    if isinstance(exc, urlerror.HTTPError):
        if exc.code == 502:
            return "HTTP 502 – ist das gewählte Profil wirklich ein Embedding-Modell?"
        return "HTTP {}".format(exc.code)
    if isinstance(exc, urlerror.URLError):
        return str(exc.reason)
    return str(exc) or exc.__class__.__name__


def _request_vectors(connection, inputs, *, timeout=TIMEOUT_SECONDS, retries=RETRIES):
    url, model = _endpoint(connection)
    api_key = str(connection.get("api_key") or "")
    payload = None
    for attempt in range(retries + 1):
        try:
            payload = _post(url, {"model": model, "input": inputs}, api_key, timeout)
            break
        except urlerror.HTTPError as exc:
            # 4xx ist eine falsche Anfrage, kein schlafender Rechner.
            if exc.code < 500 or attempt == retries:
                raise EmbeddingError("Embedding-Endpunkt nicht nutzbar: {}".format(_describe(exc))) from exc
        except (urlerror.URLError, OSError) as exc:
            if attempt == retries:
                raise EmbeddingError("Embedding-Endpunkt nicht nutzbar: {}".format(_describe(exc))) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EmbeddingError("Der Endpunkt hat kein gültiges JSON geliefert.") from exc
        time.sleep(RETRY_PAUSE_SECONDS)
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list) or len(items) != len(inputs):
        raise EmbeddingError("Der Endpunkt hat nicht für jeden Text einen Vektor geliefert.")
    ordered = sorted(items, key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0)
    vectors = []
    for item in ordered:
        vector = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(vector, list):
            raise EmbeddingError("Der Endpunkt hat ein ungültiges Embedding geliefert.")
        vectors.append(vector)
    return vectors


def embed_documents(connection, texts, *, batch_size=BATCH_SIZE):
    """Dokumenttexte (ohne Praefix) in normierte ``DIMS``-Vektoren uebersetzen."""
    vectors = []
    size = max(1, min(int(batch_size or BATCH_SIZE), 32))
    for start in range(0, len(texts), size):
        batch = [str(text)[:MAX_TEXT_CHARS] for text in texts[start:start + size]]
        vectors.extend(truncate(vector) for vector in _request_vectors(connection, batch))
    return vectors


_query_cache = OrderedDict()
_query_lock = threading.Lock()


def embed_query(connection, query):
    """Eine Suchanfrage mit Instruct-Zeile einbetten; gleiche Anfragen kommen aus dem Cache."""
    text = str(query or "").strip()
    if not text:
        raise EmbeddingError("Leere Suchanfrage.")
    key = (str(connection.get("base_url") or ""), str(connection.get("model") or ""), text)
    with _query_lock:
        if key in _query_cache:
            _query_cache.move_to_end(key)
            return _query_cache[key]
    vector = truncate(_request_vectors(connection, [QUERY_INSTRUCT + text[:MAX_TEXT_CHARS]])[0])
    with _query_lock:
        _query_cache[key] = vector
        while len(_query_cache) > QUERY_CACHE_SIZE:
            _query_cache.popitem(last=False)
    return vector


def probe(connection):
    """Ein Testaufruf: liefert das Profil wirklich Embeddings, und wie breit?

    ``/v1/models`` meldet keine Faehigkeiten (llama-swap v246), deshalb ist nur
    ein echter Aufruf eine verlaessliche Pruefung. Kein Retry: die Oberflaeche
    wartet darauf.
    """
    started = time.monotonic()
    raw = _request_vectors(connection, ["Probe"], retries=0)[0]
    truncate(raw)
    return {
        "ok": True,
        "native_dims": len(raw),
        "dims": DIMS,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
