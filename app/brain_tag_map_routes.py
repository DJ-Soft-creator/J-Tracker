"""Hashtag-Map HTTP API: Daten der Map und gespeicherte Anordnungen.

AI maintenance contract: nur Anfrage pruefen und Antwort bauen. Die Berechnung
steht in ``brain_tag_map`` (rein, ohne Datei- oder HTTP-Zugriff), Sichtbarkeit
und Hashtag-Aufloesung kommen wie bei den Tag-Filtern aus der brain-Fassade.
Eigenes Modul, weil brain_routes.py an der 1000-Zeilen-Grenze steht.

Helfer werden bei jedem Aufruf ueber ``_brain`` geholt (nicht beim Import
kopiert), damit Tests, die die Fassade umbiegen, auch hier greifen.
"""

import json
import math
import sys

from flask import jsonify, request

import brain_tag_map as tag_map
from scheduling import read_text_file, update_text_file

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before routes are imported")
brain_bp = _brain.brain_bp

LAYOUT_FILE = "tag_map_layout.json"
LAYOUT_VIEWS = {"cluster", "overview"}
LAYOUT_MAX_TAGS = 600
# Relativ zur Flaeche; die Uebersicht darf ueber den Rand hinausragen.
LAYOUT_RANGE = (-1.0, 2.0)


@brain_bp.route("/tag-map", methods=["GET"])
def brain_tag_map_view():
    """Hashtag-Map: Haeufigkeit, Verbindungen und Verlauf der sichtbaren Hashtags.

    Dieselbe Sichtbarkeit und Hashtag-Aufloesung wie die Tag-Filter. ``family=0``
    laesst die Familien-Dokumente weg (Schalter in der Map), ``match`` und
    ``include`` (kommagetrennt, die Filter-Chips) holen Filtertreffer jenseits
    der Kappung herein; dazu Freigabe-Status, alle Namen (``names``) und
    Vorschlaege. In der Map ausgeblendete Hashtags (Katalogfeld
    ``map_excluded``) fehlen ganz - samt ihrer Verbindungen - und werden in
    ``excluded`` genannt, damit die Map dezent darauf hinweisen kann.
    """
    user, error = _brain._require_user()
    if error:
        return error
    start, end, date_error = _brain._date_range()
    if date_error:
        return jsonify({"error": date_error}), 400
    try:
        max_tags = max(10, min(tag_map.MAX_TAGS_LIMIT, int(request.args.get("limit", tag_map.DEFAULT_MAX_TAGS))))
    except ValueError:
        max_tags = tag_map.DEFAULT_MAX_TAGS
    documents, pending = _brain._visible_documents(user["id"])
    if request.args.get("family") == "0":
        documents = [document for document in documents if document.get("source") != "family"]
    catalog = _brain.tagging_module.catalog_view(user["id"])
    resolve = _brain._tag_resolver(catalog)
    personal = catalog["personal"]
    excluded = set(personal.get("map_excluded", []))
    result = tag_map.build_tag_map(
        documents,
        lambda document, block: _brain._filterable_document_tags(document, block.get("tags", []), catalog, resolve) - excluded,
        start=start, end=end, max_tags=max_tags, max_edges=tag_map.edges_for(max_tags),
        daily=tag_map.is_daily(start, end), match=request.args.get("match", "")[:80],
        include={_brain.tagging_module.normalise_tag(name) for name in request.args.get("include", "").split(",")[:50]},
        approved=set(catalog["canonical"]), skip=set(personal.get("map_hidden", [])) | set(catalog["ai"]),
    )
    # Freigegeben wird dort, wo der Vorschlag steht; ein reiner Familien-
    # Vorschlag braucht Admin-Rechte (wie in der Hashtag-Verwaltung).
    family_only = set(catalog["family"].get("proposals", [])) - set(personal.get("proposals", []))
    for item in result["suggestions"] + result["tags"]:
        item["scope"] = "family" if item["name"] in family_only else "personal"
    response = jsonify({**result, "excluded": sorted(excluded), "index_pending": pending})
    response.headers["Cache-Control"] = "no-store"
    return response


def _layout_path(user_id):
    return _brain.DATA_DIR / user_id / LAYOUT_FILE


def _parse_layouts(content):
    try:
        layouts = json.loads(content or "{}")
    except ValueError:
        return {}
    if not isinstance(layouts, dict):
        return {}
    return {view: value for view, value in layouts.items() if view in LAYOUT_VIEWS and isinstance(value, dict)}


def _read_layouts(user_id):
    try:
        return _parse_layouts(read_text_file(_layout_path(user_id)))
    except OSError:
        return {}


def _clean_positions(raw):
    """Nur Hashtag → [x, y] relativ zur Flaeche (1 = ganze Breite/Hoehe, auf
    LAYOUT_RANGE begrenzt), hoechstens LAYOUT_MAX_TAGS - so bleibt die Datei
    klein und passt auf jede Bildschirmgroesse."""
    if not isinstance(raw, dict) or len(raw) > LAYOUT_MAX_TAGS:
        raise ValueError("Ungültige Anordnung")
    positions = {}
    for name, point in raw.items():
        tag = _brain.tagging_module.normalise_tag(name)
        if not tag or not isinstance(point, list) or len(point) != 2:
            raise ValueError("Ungültige Anordnung")
        x, y = point
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (x, y)):
            raise ValueError("Ungültige Anordnung")
        low, high = LAYOUT_RANGE
        positions[tag] = [round(min(high, max(low, float(x))), 4), round(min(high, max(low, float(y))), 4)]
    return positions


@brain_bp.route("/tag-map/layout", methods=["GET", "PUT", "DELETE"])
def brain_tag_map_layout():
    """Selbst angeordnete Knoten der Map (Cluster, Uebersicht), pro Nutzer.

    GET liefert alle gespeicherten Anordnungen, PUT ``{view, positions}``
    ersetzt die einer Sicht, DELETE ``?view=`` verwirft sie wieder.
    """
    user, error = _brain._require_user()
    if error:
        return error
    if request.method == "GET":
        return jsonify({"layouts": _read_layouts(user["id"])})
    csrf_error = _brain._csrf_error()
    if csrf_error:
        return csrf_error
    data = request.get_json(silent=True) or {}
    view = data.get("view") if request.method == "PUT" else request.args.get("view")
    if view not in LAYOUT_VIEWS:
        return jsonify({"error": "Unbekannte Ansicht"}), 400
    positions = None
    if request.method == "PUT":
        try:
            positions = _clean_positions(data.get("positions"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    # Lesen und Schreiben unter einer Sperre: zwei Geraete, die gleichzeitig
    # speichern, ueberschreiben sich nicht gegenseitig die andere Sicht.
    def update(content):
        layouts = _parse_layouts(content)
        if positions is None:
            layouts.pop(view, None)
        else:
            layouts[view] = positions
        return json.dumps(layouts, ensure_ascii=False, sort_keys=True) + "\n", layouts

    path = _layout_path(user["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    layouts = update_text_file(path, update)
    return jsonify({"ok": True, "layouts": layouts})


__all__ = ["brain_tag_map_view", "brain_tag_map_layout"]
