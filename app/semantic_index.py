"""Der semantische Index: Bloecke zerlegen, abgleichen, einbetten, ablegen.

AI maintenance contract: kein Flask. Quelle sind die Markdown-Dateien, gelesen
ueber dieselben Scanner wie der Brain-Index (``brain._scan_*``) — nie der
Index selbst, damit ein veralteter Index keinen veralteten Vektor erzeugt.
Ablage nur ueber ``vector_store``, Modellaufrufe nur ueber ``embeddings``.

Abgleich ohne eigenen Zustand: jeder Punkt traegt ``semantic_hash`` des
eingebetteten Textes und den Modellnamen. Neu eingebettet wird nur, was fehlt
oder sich geaendert hat; was nicht mehr in den Dateien steht, wird geloescht.
Ein abgebrochener Lauf kostet deshalb nichts ausser der angefangenen Charge.

Der Lauf ist global (Admin): eine Collection je Person mit eingeschalteter
semantischer Suche plus ``journl_family``. Er laeuft als ``background_runs``-Lauf
im Konto des Admins, der ihn startet.
"""

import json
import logging
import re
import sys
import uuid
from datetime import timedelta

import background_runs
import embeddings
import semantic_settings
import vector_store
from markdown.hashing import semantic_hash
from scheduling import write_text_file

logger = logging.getLogger(__name__)

RUN_KIND = "semantic_index"
RUN_RETENTION_DAYS = 30
POINT_NAMESPACE = uuid.UUID("6f1c2d4e-8a5b-4c3d-9e7f-1a2b3c4d5e6f")

# ~600–700 Token deutscher Text; Gateway-Empfehlung 300–800 Token, ~10 % Ueberlappung.
CHUNK_CHARS = 2400
CHUNK_OVERLAP = 240
HEADING_CHARS = 200

SOURCE_LABELS = {"journal": "Journal", "notes": "Notiz", "family": "Familie", "agent": "Agent"}
_KIND_SOURCE = {"journal": "journal", "project": "notes", "note": "notes", "agent": "agent", "chat": "agent"}

# Kennzahlen der DEV-Demodaten: ~6 % der Bloecke tragen jt:media-Marker, ~9 %
# UUID-/Hash-Ketten. Beides ist fuer die Bedeutung Rauschen und wuerde kurze
# Bloecke (Median ~70 Zeichen) im Vektor dominieren.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HASH_RE = re.compile(
    r"\b(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,})\b", re.IGNORECASE)
_BLANK_RE = re.compile(r"\n{3,}")


def _brain():
    return sys.modules.get("brain")


def _core():
    return sys.modules.get("main") or sys.modules.get("__main__")


# ─── Zerlegen ─────────────────────────────────────────────────────────
def source_of(document):
    """Quellengruppe eines Indexdokuments, ``None`` fuer nicht eingebettete Arten."""
    if document.get("source") == "family":
        return "family"
    if document.get("source") != "personal":
        return None
    return _KIND_SOURCE.get(document.get("kind"))


def chunk_text(text, limit=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
    """Langen Text an Absatz-, sonst Zeilen-, sonst Wortgrenzen teilen, mit Ueberlappung."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            window = text[start:end]
            for separator in ("\n\n", "\n", " "):
                cut = window.rfind(separator)
                if cut > limit // 2:
                    end = start + cut
                    break
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def clean_text(text):
    """Was eingebettet wird: ohne HTML-Kommentare (Medien-, Anker-Marker) und Hashketten."""
    text = _HASH_RE.sub("", _COMMENT_RE.sub("", text or ""))
    return _BLANK_RE.sub("\n\n", text).strip()


def heading_of(text):
    first = (text or "").strip().split("\n", 1)[0].strip()
    return first[:HEADING_CHARS] if first.startswith("#") else ""


def _tags(block):
    return sorted(set(block.get("tags") or []) | set(block.get("tags_auto") or []) | set(block.get("ai_tags") or []))


def embedding_text(source, date, tags, chunk):
    """Kurzer Kopf vor dem Text: damit finden auch Fragen nach Zeit oder Thema die Stelle."""
    head = " · ".join(part for part in (
        SOURCE_LABELS.get(source, source), date or "", " ".join("#" + tag for tag in tags[:12]),
    ) if part)
    return head + "\n\n" + chunk


def point_id(document, anchor, chunk_number):
    key = "{}:{}#{}#{}".format(document.get("source"), document.get("doc_id"), anchor, chunk_number)
    return str(uuid.uuid5(POINT_NAMESPACE, key))


def planned_points(documents, model):
    """Alle Punkte, die es nach den Dateien geben muss — noch ohne Vektor."""
    planned = {}
    for document in documents:
        source = source_of(document)
        if not source:
            continue
        for block in document.get("blocks") or []:
            anchor = block.get("stable_anchor") or "line:{}".format(block.get("start_line", 1))
            tags = _tags(block)
            date = str(document.get("date") or "")[:10]
            for number, chunk in enumerate(chunk_text(clean_text(block.get("text")))):
                text = embedding_text(source, date, tags, chunk)
                planned[point_id(document, anchor, number)] = {
                    "text": text,
                    "payload": {
                        "source": source,
                        "kind": document.get("kind", ""),
                        "doc_id": document.get("doc_id", ""),
                        "path": document.get("path", ""),
                        "anchor": anchor,
                        "start_line": block.get("start_line", 1),
                        "chunk": number,
                        "heading": heading_of(block.get("text")),
                        "date": date,
                        "day": vector_store.day_number(date),
                        "tags": tags,
                        "semantic_hash": semantic_hash(text),
                        "model": model,
                        "dims": embeddings.DIMS,
                    },
                }
    return planned


# ─── Abgleich ─────────────────────────────────────────────────────────
def sync_collection(name, documents, connection, *, batch_size, cancelled=lambda: False,
                    progress=lambda **_: None, full=False):
    """Eine Collection an die Dateien angleichen. Gibt die Zaehler zurueck."""
    if full:
        vector_store.delete_collection(name)
    vector_store.ensure_collection(name, embeddings.DIMS)
    planned = planned_points(documents, connection["model"])
    stored = vector_store.stored_hashes(name)
    stale = [
        pid for pid, item in planned.items()
        if stored.get(pid) != (item["payload"]["semantic_hash"], connection["model"])
    ]
    removed = [pid for pid in stored if pid not in planned]
    vector_store.delete_points(name, removed)
    counts = {"planned": len(planned), "embedded": 0, "unchanged": len(planned) - len(stale),
              "removed": len(removed), "cancelled": False}
    step = max(1, int(batch_size)) * 4
    for start in range(0, len(stale), step):
        if cancelled():
            counts["cancelled"] = True
            break
        ids = stale[start:start + step]
        vectors = embeddings.embed_documents(
            connection, [planned[pid]["text"] for pid in ids], batch_size=batch_size,
        )
        vector_store.upsert(name, [
            {"id": pid, "vector": vector, "payload": planned[pid]["payload"]}
            for pid, vector in zip(ids, vectors)
        ])
        counts["embedded"] += len(ids)
        progress(**counts, pending=len(stale) - counts["embedded"])
    return counts


def personal_documents(user_id):
    brain = _brain()
    return brain._decorate_documents(user_id, brain._scan_personal_documents(user_id))


def family_documents():
    return _brain()._scan_family_documents()


def enabled_users():
    users = _core()._read_users_file().get("users", [])
    return [user["id"] for user in users if user.get("id") and semantic_settings.load_user(user["id"])["enabled"]]


def status():
    """Punktzahlen je Collection fuer die Einstellungen — ohne Inhalte."""
    if not vector_store.available():
        return {"available": False}
    result = {"available": True, "health": vector_store.health(), "collections": {}}
    for user_id in enabled_users():
        name = vector_store.user_collection(user_id)
        result["collections"][user_id] = vector_store.collection_info(name)
    result["collections"]["family"] = vector_store.collection_info(vector_store.FAMILY_COLLECTION)
    return result


# ─── Lauf ─────────────────────────────────────────────────────────────
def run_handler(handle, params):
    settings = semantic_settings.load_global()
    connection = semantic_settings.connection(settings)
    if connection is None:
        raise background_runs.RunError("Bitte zuerst Endpunkt und Embedding-Modell speichern.")
    if not vector_store.available():
        raise background_runs.RunError("Die Vektor-DB ist nicht eingerichtet (QDRANT_URL fehlt).")
    full = bool(params.get("full"))
    users = enabled_users()
    targets = [(vector_store.user_collection(user_id), user_id) for user_id in users]
    if users:
        targets.append((vector_store.FAMILY_COLLECTION, None))
    totals = {"collections": 0, "planned": 0, "embedded": 0, "unchanged": 0, "removed": 0}
    stopped = ""
    for name, user_id in targets:
        if handle.cancelled:
            stopped = "Vom Nutzer abgebrochen."
            break
        documents = personal_documents(user_id) if user_id else family_documents()
        label = "Familie" if user_id is None else "Konto"
        try:
            counts = sync_collection(
                name, documents, connection, batch_size=settings["batch_size"], full=full,
                cancelled=lambda: handle.cancelled,
                progress=lambda **values: handle.progress(collection=label, **values),
            )
        except (embeddings.EmbeddingError, vector_store.VectorStoreError) as exc:
            raise background_runs.RunError(str(exc)) from exc
        totals["collections"] += 1
        for key in ("planned", "embedded", "unchanged", "removed"):
            totals[key] += counts[key]
        # Nur Zahlen ins Protokoll: es liegt im Konto des Admins, die Inhalte nicht.
        handle.log({"collection": label, **{key: counts[key] for key in ("planned", "embedded", "unchanged", "removed")}})
        if counts["cancelled"]:
            stopped = "Vom Nutzer abgebrochen."
            break
    logger.info("Semantic index run: %s", totals)
    return {**totals, "users": len(users), "full": full, "stopped": stopped}


def run_label(params):
    return "Semantischer Index – {}".format("Neuaufbau" if params.get("full") else "abgleichen")


# ─── Nachtlauf ────────────────────────────────────────────────────────
# Einmal je Tag ab der eingestellten Uhrzeit. Schlaeft der Mac mini, wird nach
# RETRY_MINUTES erneut versucht, hoechstens MAX_ATTEMPTS Mal; danach wartet der
# Lauf auf den naechsten Tag. Der Abgleich ueber den Hash holt alles nach.
STATE_NAME = "semantic-search-state.json"
MAX_ATTEMPTS = 3
RETRY_MINUTES = 30


def _state_path():
    return _core().DATA_DIR / STATE_NAME


def load_state():
    return semantic_settings._load(_state_path())


def job_due(settings, state, now):
    if not settings.get("job_enabled"):
        return False
    if now.strftime("%H:%M") < settings["schedule_time"]:
        return False
    today = now.date().isoformat()
    if state.get("done_on") == today:
        return False
    if state.get("day") == today:
        if int(state.get("attempts") or 0) >= MAX_ATTEMPTS:
            return False
        retry_after = str(state.get("retry_after") or "")
        if retry_after and now.isoformat(timespec="seconds") < retry_after:
            return False
    return True


def record_attempt(now, ok):
    today = now.date().isoformat()
    state = load_state()
    attempts = int(state.get("attempts") or 0) + 1 if state.get("day") == today else 1
    state = {
        "day": today, "attempts": attempts,
        "done_on": today if ok else state.get("done_on", ""),
        "retry_after": "" if ok else (now + timedelta(minutes=RETRY_MINUTES)).isoformat(timespec="seconds"),
        "last_run_at": now.isoformat(timespec="seconds"),
    }
    write_text_file(_state_path(), json.dumps(state, indent=2) + "\n")
    return state


def job_owner():
    """Das Konto, in dessen Protokoll der Nachtlauf landet: der erste Admin."""
    for user in _core()._read_users_file().get("users", []):
        if user.get("admin") is True and user.get("id"):
            return user["id"]
    return None


def run_scheduled(now):
    """Vom Takt-Sidecar aufgerufen. ``True``, wenn ein Lauf gestartet wurde."""
    settings = semantic_settings.load_global()
    if not job_due(settings, load_state(), now):
        return False
    owner = job_owner()
    if not owner:
        logger.warning("Semantischer Index: kein Admin-Konto für den Nachtlauf gefunden.")
        return False
    try:
        record = background_runs.start(
            owner, RUN_KIND, {"full": False}, label=run_label({}), trigger="schedule", inline=True,
        )
    except background_runs.RunConflict:
        logger.info("Semantischer Index: ein Lauf ist schon aktiv, der Termin wartet.")
        return False
    ok = record.get("status") == "done"
    record_attempt(now, ok)
    logger.info("Semantischer Index (Nachtlauf): %s %s", record.get("status"), record.get("error") or "")
    return True


background_runs.register(RUN_KIND, run_handler, retention_days=RUN_RETENTION_DAYS)
