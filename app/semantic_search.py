"""Hybride Suche: Worttreffer und Vektortreffer, per Reciprocal Rank Fusion gemischt.

AI maintenance contract: kein Flask. Eine Funktion ``search`` fuer Brain-Tab,
MCP, Brain-Chat und ``scripts/semantic-eval.py`` — keine zweite Fassung je
Oberflaeche.

Sichtbarkeit: Die Wortseite arbeitet auf ``brain._visible_documents`` (dieselbe
ACL wie die Brain-Suche). Die Vektorseite fragt nur die eigene Collection und
``journl_family``; jeder Vektortreffer muss danach noch auf ein Dokument zeigen,
das in genau dieser sichtbaren Menge steht. Ein Familiendokument, das der
Nutzer nicht sehen darf, oder ein Vektor einer geloeschten Datei faellt dabei
heraus.

Die Wortseite ist hier bewusst eine ODER-Rangfolge mit IDF-Gewicht und kein
Aufruf der Brain-Wortsuche: jene filtert UND-verknuepft und sortiert nach Datum,
eine Frage in Alltagssprache faende dort fast nie etwas, und ohne Rang gibt es
nichts zu fusionieren.
"""

import math
import re
import sys

import embeddings
import semantic_index
import semantic_settings
import vector_store

RRF_K = 60
CANDIDATES = 50
# Kurze Fuellwoerter tragen in einer ODER-Suche nichts zum Rang bei.
_STOPWORDS = {
    "und", "oder", "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "ich", "mir", "mich", "mein", "meine", "wir", "uns", "war", "waren", "ist", "sind", "hat",
    "habe", "hatte", "wann", "wie", "was", "wo", "wer", "warum", "mit", "von", "fuer", "für",
    "auf", "aus", "bei", "nach", "zum", "zur", "über", "ueber", "nicht", "auch", "noch", "schon",
    "the", "and", "was", "were", "when", "what", "how", "with", "for", "from", "about", "did",
}
_TERM_RE = re.compile(r"[#\w][\w-]{2,}", re.UNICODE)


def _brain():
    return sys.modules.get("brain")


def _key(source, path, anchor):
    scope = "f" if source == "family" else "p"
    return "{}:{}#{}".format(scope, path, anchor)


def query_terms(query):
    normalise = _brain()._normalise
    terms = []
    for raw in _TERM_RE.findall(query or ""):
        term = normalise(raw)
        if term.lstrip("#") not in _STOPWORDS and term not in terms:
            terms.append(term)
    return terms


def _candidates(documents, sources, start_date, end_date):
    for document in documents:
        source = semantic_index.source_of(document)
        if source not in sources:
            continue
        date = str(document.get("date") or "")[:10]
        if (start_date and date < start_date) or (end_date and date > end_date):
            continue
        for block in document.get("blocks") or []:
            yield source, document, block


def word_ranking(documents, query, sources, start_date=None, end_date=None, limit=CANDIDATES):
    """ODER-Rangfolge ueber den vorbereiteten Suchtext, seltene Begriffe zaehlen mehr."""
    terms = query_terms(query)
    if not terms:
        return []
    normalise = _brain()._normalise
    hits = []
    frequency = dict.fromkeys(terms, 0)
    for source, document, block in _candidates(documents, sources, start_date, end_date):
        haystack = block.get("search_base")
        if haystack is None:
            haystack = normalise(block.get("text", ""))
        present = [term for term in terms if term in haystack]
        if not present:
            continue
        for term in present:
            frequency[term] += 1
        hits.append((source, document, block, present))
    total = max(len(hits), 1)
    idf = {term: math.log(1 + total / (1 + count)) for term, count in frequency.items()}
    scored = [
        (sum(idf[term] for term in present), source, document, block)
        for source, document, block, present in hits
    ]
    scored.sort(key=lambda item: -item[0])
    ranking = []
    for score, source, document, block in scored[:limit]:
        anchor = block.get("stable_anchor") or "line:{}".format(block.get("start_line", 1))
        ranking.append({
            "key": _key(source, document.get("path", ""), anchor),
            "source": source, "path": document.get("path", ""), "anchor": anchor,
            "date": str(document.get("date") or "")[:10], "start_line": block.get("start_line", 1),
            "heading": semantic_index.heading_of(block.get("text")), "word_score": round(score, 4),
        })
    return ranking


def vector_ranking(user_id, vector, sources, visible_keys, *, start_date=None, end_date=None,
                   model=None, min_score=None, limit=CANDIDATES):
    """Vektortreffer der eigenen Collection und der Familie, nur sichtbare, bester Chunk je Block."""
    queries = []
    personal = [source for source in sources if source != "family"]
    if personal:
        queries.append((vector_store.user_collection(user_id), personal))
    if "family" in sources:
        queries.append((vector_store.FAMILY_COLLECTION, ["family"]))
    best = {}
    for name, wanted in queries:
        for hit in vector_store.search(
            name, vector, limit=limit * 2, sources=wanted, start_date=start_date,
            end_date=end_date, model=model, min_score=min_score,
        ):
            payload = hit["payload"]
            key = _key(payload.get("source"), payload.get("path", ""), payload.get("anchor", ""))
            if key not in visible_keys:
                continue
            if key not in best or hit["score"] > best[key]["vector_score"]:
                best[key] = {
                    "key": key, "source": payload.get("source"), "path": payload.get("path", ""),
                    "anchor": payload.get("anchor", ""), "date": payload.get("date", ""),
                    "start_line": payload.get("start_line", 1), "heading": payload.get("heading", ""),
                    "vector_score": round(hit["score"], 4),
                }
    return sorted(best.values(), key=lambda item: -item["vector_score"])[:limit]


def blocks_for(results, documents, sources):
    """(Treffer, Dokument, Block) je Treffer, der noch sichtbar in den Dateien steht."""
    index = {
        _key(source, document.get("path", ""),
             block.get("stable_anchor") or "line:{}".format(block.get("start_line", 1))): (document, block)
        for source, document, block in _candidates(documents, sources, None, None)
    }
    return [(item, *index[item["key"]]) for item in results if item["key"] in index]


def similar(user_id, path, anchor, *, sources=None, scope=None, limit=10, documents=None):
    """Bloecke, die einem vorhandenen Block aehneln — mit seinem gespeicherten Vektor, ohne Modellaufruf.

    ``path``/``anchor`` muessen auf einen sichtbaren Block zeigen; der Block
    selbst erscheint nicht in der Liste. ``scope`` begrenzt, woher der Ausgangsblock
    stammen darf (MCP-Bereich), ``sources``, wo gesucht wird. ``ValueError``, wenn es
    ihn nicht gibt oder er noch nicht eingebettet ist.
    """
    personal = semantic_settings.load_user(user_id)
    sources = [source for source in (sources or personal["sources"]) if source in semantic_settings.SOURCES]
    if documents is None:
        documents, _pending = _brain()._visible_documents(user_id)
    target = None
    visible_keys = set()
    for source, document, block in _candidates(documents, semantic_settings.SOURCES, None, None):
        block_anchor = block.get("stable_anchor") or "line:{}".format(block.get("start_line", 1))
        key = _key(source, document.get("path", ""), block_anchor)
        if source in sources:
            visible_keys.add(key)
        if document.get("path") == path and block_anchor == anchor and source in (scope or semantic_settings.SOURCES):
            target = (source, document, key)
    if target is None:
        raise ValueError("Diesen Block gibt es nicht oder er ist nicht sichtbar.")
    source, document, own_key = target
    settings = semantic_settings.load_global()
    connection = semantic_settings.connection(settings)
    if connection is None or not vector_store.available():
        raise vector_store.VectorStoreError("Die semantische Suche ist nicht eingerichtet.")
    collection = vector_store.FAMILY_COLLECTION if source == "family" else vector_store.user_collection(user_id)
    vector = vector_store.point_vector(collection, semantic_index.point_id(document, anchor, 0))
    if vector is None:
        raise ValueError("Dieser Block ist noch nicht eingebettet — der nächste Abgleich holt das nach.")
    visible_keys.discard(own_key)
    hits = vector_ranking(user_id, vector, sources, visible_keys, model=connection["model"],
                          min_score=settings["min_score"], limit=limit + 1)
    return fuse(hits)[:limit]


def fuse(*rankings, k=RRF_K):
    """Reciprocal Rank Fusion: Summe 1/(k+Rang) ueber alle Listen, Herkunft bleibt sichtbar."""
    merged = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            entry = merged.setdefault(item["key"], {**item, "rrf": 0.0, "origins": []})
            entry.update({key: value for key, value in item.items() if key.endswith("_score")})
            entry["rrf"] += 1.0 / (k + rank)
    for entry in merged.values():
        has_word = "word_score" in entry
        has_vector = "vector_score" in entry
        entry["origin"] = "both" if has_word and has_vector else ("word" if has_word else "semantic")
        entry.pop("origins", None)
        entry["rrf"] = round(entry["rrf"], 6)
    return sorted(merged.values(), key=lambda item: -item["rrf"])


def search(user_id, query, *, sources=None, start_date=None, end_date=None, limit=20,
           mode="hybrid", documents=None):
    """Treffer als Liste plus Zustand der semantischen Seite.

    ``mode``: ``hybrid`` (Standard), ``word`` oder ``semantic``. Faellt die
    semantische Seite aus, kommt die Wortseite trotzdem — mit Grund in
    ``semantic.reason``, nie still.
    """
    personal = semantic_settings.load_user(user_id)
    sources = [source for source in (sources or personal["sources"]) if source in semantic_settings.SOURCES]
    if documents is None:
        documents, _pending = _brain()._visible_documents(user_id)
    visible_keys = {
        _key(source, document.get("path", ""),
             block.get("stable_anchor") or "line:{}".format(block.get("start_line", 1)))
        for source, document, block in _candidates(documents, sources, None, None)
    }
    words = [] if mode == "semantic" else word_ranking(documents, query, sources, start_date, end_date)
    semantic = {"status": "off", "reason": ""}
    vectors = []
    if mode != "word":
        settings = semantic_settings.load_global()
        connection = semantic_settings.connection(settings)
        if connection is None:
            semantic = {"status": "unavailable", "reason": "Kein Embedding-Modell eingestellt."}
        elif not vector_store.available():
            semantic = {"status": "unavailable", "reason": "Die Vektor-DB ist nicht eingerichtet."}
        else:
            try:
                vector = embeddings.embed_query(connection, query)
                vectors = vector_ranking(
                    user_id, vector, sources, visible_keys, start_date=start_date, end_date=end_date,
                    model=connection["model"], min_score=settings["min_score"],
                )
                semantic = {"status": "ok", "reason": "" if vectors else "Nichts hinreichend Ähnliches gefunden."}
            except (embeddings.EmbeddingError, vector_store.VectorStoreError) as exc:
                semantic = {"status": "unavailable", "reason": str(exc)}
    if mode == "word":
        results = fuse(words)
    elif mode == "semantic":
        results = fuse(vectors)
    else:
        results = fuse(words, vectors)
    return {"results": results[:max(1, min(int(limit), 200))], "semantic": semantic, "sources": sources}
