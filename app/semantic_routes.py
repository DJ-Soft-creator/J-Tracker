"""HTTP-Oberflaeche der semantischen Suche (`/api/brain/semantic/...`).

AI maintenance contract: nur Request-Validierung und Serialisierung. Einstellungen
in semantic_settings.py, Index in semantic_index.py, Suche in semantic_search.py.

Admin-Grenze: Verbindung, Nachtlauf, Status und Indexlauf gelten fuer alle Konten
und bleiben Administratoren vorbehalten. Jede Person schaltet nur ihre eigene
Suche an und waehlt ihre Quellen. Der API-Schluessel verlaesst den Server nie.
"""

import sys

from flask import Blueprint, jsonify, request

import agent_projects
import background_runs
import embeddings
import semantic_index
import semantic_search
import semantic_settings
import vector_store

semantic_bp = Blueprint("semantic", __name__, url_prefix="/api/brain/semantic")


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _guard(*, write=False, admin=False):
    """Resolve the caller, or return the response that must be sent instead."""
    main = _main()
    user = main._current_user() if main else None
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if admin and user.get("admin") is not True:
        return None, (jsonify({"error": "Nur Administratoren dürfen das ändern."}), 403)
    if write:
        error = main.csrf_protect(lambda: None)()
        if error:
            return None, error
    return user, None


def _no_store(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@semantic_bp.route("/settings", methods=["GET"])
def settings_get():
    user, error = _guard()
    if error:
        return error
    is_admin = user.get("admin") is True
    global_settings = semantic_settings.public_global(semantic_settings.load_global())
    payload = {
        "user": semantic_settings.load_user(user["id"]),
        "sources": list(semantic_settings.SOURCES),
        "configured": global_settings["configured"],
        "vector_store": vector_store.available(),
        "dims": embeddings.DIMS,
        "is_admin": is_admin,
    }
    if is_admin:
        payload["global"] = global_settings
    return _no_store(payload)


@semantic_bp.route("/settings/user", methods=["POST"])
def settings_user_set():
    user, error = _guard(write=True)
    if error:
        return error
    try:
        saved = semantic_settings.save_user(user["id"], request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "user": saved})


@semantic_bp.route("/settings/global", methods=["POST"])
def settings_global_set():
    _user, error = _guard(write=True, admin=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    previous_model = semantic_settings.load_global()["model"]
    try:
        saved = semantic_settings.save_global(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "ok": True, "global": semantic_settings.public_global(saved),
        # Vektoren eines anderen Modells sind nicht vergleichbar; der naechste
        # Lauf bettet deshalb alles neu ein. Die Oberflaeche sagt das an.
        "model_changed": bool(previous_model and previous_model != saved["model"]),
    })


@semantic_bp.route("/models", methods=["GET"])
def models():
    """Lebende Modellliste des Endpunkts — nie eine geratene."""
    _user, error = _guard(admin=True)
    if error:
        return error
    base_url = str(request.args.get("base_url") or "") or semantic_settings.load_global()["base_url"]
    try:
        found = agent_projects.discover_models("openai", base_url)
    except agent_projects.AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 502
    return _no_store({"models": found, "base_url": base_url})


@semantic_bp.route("/test", methods=["POST"])
def test():
    """Echter Embedding-Aufruf mit der gespeicherten oder der eingegebenen Verbindung."""
    _user, error = _guard(write=True, admin=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    stored = semantic_settings.load_global()
    connection = {
        "base_url": str(data.get("base_url") or stored["base_url"]),
        "model": str(data.get("model") or stored["model"]),
        "api_key": str(data["api_key"]) if "api_key" in data else stored["api_key"],
    }
    try:
        result = embeddings.probe(connection)
    except embeddings.EmbeddingError as exc:
        return _no_store({"ok": False, "error": str(exc)}, 502)
    return _no_store(result)


@semantic_bp.route("/status", methods=["GET"])
def status():
    user, error = _guard(admin=True)
    if error:
        return error
    try:
        result = semantic_index.status()
    except vector_store.VectorStoreError as exc:
        return _no_store({"available": True, "error": str(exc)}, 502)
    runs = background_runs.listing(user["id"], semantic_index.RUN_KIND)
    result["last_run"] = {**background_runs.public_run(runs[0]), "log": []} if runs else None
    result["state"] = semantic_index.load_state()
    return _no_store(result)


@semantic_bp.route("/index/run", methods=["POST"])
def index_run():
    user, error = _guard(write=True, admin=True)
    if error:
        return error
    params = {"full": bool((request.get_json(silent=True) or {}).get("full"))}
    try:
        record = background_runs.start(
            user["id"], semantic_index.RUN_KIND, params, label=semantic_index.run_label(params),
        )
    except background_runs.RunConflict as exc:
        running = background_runs.active(user["id"], semantic_index.RUN_KIND)
        return jsonify({"error": str(exc), "run": background_runs.public_run(running) if running else None}), 409
    except background_runs.RunError as exc:
        return jsonify({"error": str(exc)}), 400
    return _no_store({"run": background_runs.public_run(record)}, 202)


@semantic_bp.route("/search", methods=["GET"])
def search():
    """Hybride Suche fuer das eigene Konto. Ohne Freischaltung nur die Wortseite."""
    user, error = _guard()
    if error:
        return error
    query = str(request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Bitte einen Suchbegriff angeben."}), 400
    mode = request.args.get("mode", "hybrid")
    if mode not in {"hybrid", "word", "semantic"}:
        return jsonify({"error": "Unbekannter Suchmodus."}), 400
    personal = semantic_settings.load_user(user["id"])
    if not personal["enabled"]:
        mode = "word"
    sources = [item for item in request.args.getlist("source") if item in semantic_settings.SOURCES]
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        return jsonify({"error": "Ungültiges Limit."}), 400
    payload = semantic_search.search(
        user["id"], query, sources=sources or None, mode=mode, limit=limit,
        start_date=request.args.get("start") or None, end_date=request.args.get("end") or None,
    )
    if not personal["enabled"]:
        payload["semantic"] = {"status": "off", "reason": "Semantische Suche ist für dein Konto aus."}
    return _no_store(payload)



def _cards(user_id, results, documents, sources, limit, keep=None):
    """Treffer als Brain-Karten (``brain._serialise_result``) plus Herkunft und Aehnlichkeit."""
    brain = sys.modules["brain"]
    cards = []
    for item, document, block in semantic_search.blocks_for(results, documents, sources):
        if keep and not keep(document, block):
            continue
        card = brain._serialise_result(document, block, user_id)
        card["origin"] = item["origin"]
        card["similarity"] = item.get("vector_score")
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards


@semantic_bp.route("/cards", methods=["GET"])
def cards():
    """Hybride Treffer als Brain-Karten: dieselbe Form wie ``/api/brain/search``.

    Gleiche Parameter wie die Brain-Suche (``q``, ``tags``, ``exclude_tags``,
    ``any_tags``, ``start_date``, ``end_date``, ``kind``), sortiert nach Rang
    statt Datum. Die Hashtag-Filter greifen nach der Fusion auf dieselbe Weise
    wie in der Wortsuche. Faellt die semantische Seite aus, kommen keine Karten:
    der Client behaelt dann seine Wortliste und nennt den Grund.
    """
    user, error = _guard()
    if error:
        return error
    query = str(request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Bitte einen Suchbegriff angeben."}), 400
    personal = semantic_settings.load_user(user["id"])
    if not personal["enabled"]:
        return _no_store({"results": [], "semantic": {"status": "off", "reason": "Semantische Suche ist für dein Konto aus."}})
    brain = sys.modules["brain"]
    start, end, date_error = brain._date_range()
    if date_error:
        return jsonify({"error": date_error}), 400
    kind = request.args.get("kind", "all")
    if kind not in {"all", "journal"}:
        return jsonify({"error": "Invalid content kind"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit", "50")), 200))
    except ValueError:
        return jsonify({"error": "Ungültiges Limit."}), 400
    sources = ["journal"] if kind == "journal" else personal["sources"]
    documents, _pending = brain._visible_documents(user["id"])
    payload = semantic_search.search(
        user["id"], query, sources=sources, limit=200, documents=documents,
        start_date=start.isoformat() if start else None, end_date=end.isoformat() if end else None,
    )
    if payload["semantic"]["status"] != "ok":
        return _no_store({"results": [], "semantic": payload["semantic"]})
    selected, excluded, any_tags = brain._selected_tags(), brain._excluded_tags(), brain._any_tags()
    filtered = bool(selected or excluded or any_tags)
    catalog = brain.tagging_module.catalog_view(user["id"]) if filtered else None
    resolve = brain._tag_resolver(catalog) if filtered else None
    keep = None
    if filtered:
        def keep(document, block):
            approved = brain._filterable_document_tags(document, block.get("tags", []), catalog, resolve)
            return brain._tags_pass(approved, selected, excluded, any_tags)
    results = _cards(user["id"], payload["results"], documents, sources, limit, keep)
    return _no_store({
        "results": results, "semantic": payload["semantic"], "order": "relevance",
        "total": len(results), "has_more": False, "corrections": [],
    })



@semantic_bp.route("/similar", methods=["GET"])
def similar():
    """„Ähnliche Einträge" einer Karte: gespeicherter Vektor des Blocks, kein Modellaufruf."""
    user, error = _guard()
    if error:
        return error
    personal = semantic_settings.load_user(user["id"])
    if not personal["enabled"]:
        return jsonify({"error": "Semantische Suche ist für dein Konto aus."}), 409
    doc_id = str(request.args.get("doc_id") or "")
    fingerprint = str(request.args.get("fingerprint") or "")
    documents, _pending = sys.modules["brain"]._visible_documents(user["id"])
    found = next((
        (document, block) for document in documents if document.get("doc_id") == doc_id
        for block in document.get("blocks") or [] if block.get("fingerprint") == fingerprint
    ), None)
    source = semantic_index.source_of(found[0]) if found else None
    if not source:
        return jsonify({"error": "Diesen Eintrag gibt es nicht mehr."}), 404
    document, block = found
    anchor = block.get("stable_anchor") or "line:{}".format(block.get("start_line", 1))
    sources = personal["sources"]
    try:
        # ``scope``: Privat und Familie koennen denselben Pfad tragen, der Ausgangsblock
        # ist der aus genau dieser Quelle.
        results = semantic_search.similar(user["id"], document.get("path", ""), anchor, sources=sources,
                                          scope=[source], limit=20, documents=documents)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    except (embeddings.EmbeddingError, vector_store.VectorStoreError) as exc:
        return jsonify({"error": str(exc)}), 503
    return _no_store({"results": _cards(user["id"], results, documents, sources, 20), "has_more": False})


__all__ = ["semantic_bp"]
