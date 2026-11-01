"""Bildschirmschoner-Presets: ein komplettes Setup unter einem Namen.

Ein Preset friert den aktuellen Stand ein, mehr kann es nicht: die
Bildschirmschoner-Einstellungen (``user["screensaver"]``), den Ablauf des
Fragen-Modus (``flow`` aus ``screensaver_questions``) und die Musik samt
Wiedergabe-Einstellungen (``screensaver_player.PRESET_FIELDS``). Laden macht es wieder
zur aktuellen Einstellung; was danach geaendert wird, landet nur dort. Das
Preset bleibt sauber, bis es jemand ausdruecklich ueberschreibt - ``modified``
zeigt solange die Abweichung an.

Die Presets liegen pro Konto in ``user["screensaver_presets"]``, das zuletzt
geladene in ``user["screensaver_preset"]``. Jeder Bereich ist ein eigener
Schluessel (``screensaver``, ``questions``, ``player``): ein Preset aus
einer aelteren Fassung, dem ein Bereich oder Feld fehlt, laesst beim Laden die
aktuelle Einstellung dafuer stehen.

Die Kennung entsteht einmal aus dem Namen und bleibt beim Umbenennen gleich -
``?screensaver=<id>`` als Lesezeichen oder Kurzbefehl soll nicht brechen.
"""

import re
import sys
import time

from flask import Blueprint, jsonify, request

import screensaver
import screensaver_player
import screensaver_questions


presets_bp = Blueprint("screensaver_presets", __name__, url_prefix="/api/screensaver/presets")

MAX_PRESETS = 12
MAX_LABEL = 40
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _slug(label):
    text = re.sub(r"[^a-z0-9]+", "-", label.lower().translate(_UMLAUTS)).strip("-")[:48].strip("-")
    return text or "preset"


def _label(value):
    return " ".join(str(value or "").split())[:MAX_LABEL].strip()


def _presets(user):
    raw = user.get("screensaver_presets")
    return [entry for entry in raw if isinstance(entry, dict) and _ID_RE.match(str(entry.get("id") or ""))] \
        if isinstance(raw, list) else []


def _snapshot(user):
    return {
        "screensaver": screensaver.load_settings(user),
        "questions": screensaver_questions.load(user["id"])["flow"],
        "player": screensaver_player.preset_snapshot(user),
    }


def _differs(stored, current):
    """Abweichung nur in Feldern, die das Preset kennt."""
    return any(stored.get(key) != value for key, value in current.items() if key in stored)


def _modified(user, preset):
    if not preset:
        return False
    current = _snapshot(user)
    return any(isinstance(preset.get(area), dict) and _differs(preset[area], current[area]) for area in current)


def _public(user):
    presets = _presets(user)
    active = user.get("screensaver_preset")
    preset = next((entry for entry in presets if entry["id"] == active), None)
    return {
        "presets": [{"id": entry["id"], "label": entry.get("label") or entry["id"]} for entry in presets],
        "active": preset["id"] if preset else "",
        "modified": _modified(user, preset),
        "max": MAX_PRESETS,
    }


def _save(user, presets, active=None):
    user["screensaver_presets"] = presets
    if active is not None:
        user["screensaver_preset"] = active
    _main().update_user(user)


def _guard(write=False):
    user, error = screensaver._browser_user()
    if error:
        return None, error
    if write:
        csrf = screensaver._csrf_error()
        if csrf is not None:
            return None, csrf
    return user, None


def _find(user, preset_id):
    presets = _presets(user)
    index = next((i for i, entry in enumerate(presets) if entry["id"] == preset_id), None)
    return presets, index


def apply_preset(user, preset):
    """Preset zur aktuellen Einstellung machen; liefert ``(settings, flow, player, missing)``.

    Quellen, die es nicht mehr gibt (ein geloeschter Ordner), fallen fuer
    diesen Lauf weg und werden gemeldet - das Preset selbst behaelt sie. Ein
    Preset aus der Zeit vor dem Player laesst die Musik, wie sie ist.
    """
    current = screensaver.load_settings(user)
    wanted = screensaver.validate_settings({**current, **(preset.get("screensaver") or {})})
    known = {source["id"] for source in screensaver.list_sources(user["id"])}
    missing = [source for source in wanted["sources"] if source not in known]
    if missing:
        wanted = screensaver.validate_settings({**wanted, "sources": [s for s in wanted["sources"] if s in known]})
    user["screensaver"] = wanted
    player = screensaver_player.load_settings(user)
    if isinstance(preset.get("player"), dict):
        player, missing_audio = screensaver_player.apply_preset(user, preset["player"])
        missing += [source for source in missing_audio if source not in missing]
    flow = screensaver_questions.load(user["id"])["flow"]
    if isinstance(preset.get("questions"), dict):
        flow = screensaver_questions.save(user["id"], {"flow": preset["questions"]})["flow"]
    user["screensaver_preset"] = preset["id"]
    _main().update_user(user)
    return wanted, flow, player, missing


@presets_bp.route("", methods=["GET"])
def list_presets():
    user, error = _guard()
    if error:
        return error
    return jsonify(_public(user))


@presets_bp.route("", methods=["POST"])
def create_preset():
    """Den aktuellen Stand unter einem neuen Namen sichern."""
    user, error = _guard(write=True)
    if error:
        return error
    label = _label((request.get_json(silent=True) or {}).get("label"))
    if not label:
        return jsonify({"error": "Bitte einen Namen angeben."}), 400
    presets = _presets(user)
    if len(presets) >= MAX_PRESETS:
        return jsonify({"error": f"Höchstens {MAX_PRESETS} Presets."}), 400
    if any((entry.get("label") or "").lower() == label.lower() for entry in presets):
        return jsonify({"error": "Ein Preset mit diesem Namen gibt es schon."}), 409
    base = _slug(label)
    preset_id, number = base, 2
    taken = {entry["id"] for entry in presets}
    while preset_id in taken:
        preset_id = f"{base[:44]}-{number}"
        number += 1
    now = int(time.time())
    presets.append({"id": preset_id, "label": label, "created": now, "updated": now, **_snapshot(user)})
    _save(user, presets, active=preset_id)
    return jsonify({**_public(user), "preset": preset_id}), 201


@presets_bp.route("/<preset_id>", methods=["PATCH"])
def update_preset(preset_id):
    """Umbenennen (``label``) und/oder mit dem aktuellen Stand ueberschreiben (``capture``)."""
    user, error = _guard(write=True)
    if error:
        return error
    presets, index = _find(user, preset_id)
    if index is None:
        return jsonify({"error": "Preset nicht gefunden."}), 404
    data = request.get_json(silent=True) or {}
    preset = dict(presets[index])
    if "label" in data:
        label = _label(data.get("label"))
        if not label:
            return jsonify({"error": "Bitte einen Namen angeben."}), 400
        if any((entry.get("label") or "").lower() == label.lower() for i, entry in enumerate(presets) if i != index):
            return jsonify({"error": "Ein Preset mit diesem Namen gibt es schon."}), 409
        preset["label"] = label
    active = None
    if data.get("capture") is True:
        preset.update(_snapshot(user))
        active = preset_id
    preset["updated"] = int(time.time())
    presets[index] = preset
    _save(user, presets, active=active)
    return jsonify(_public(user))


@presets_bp.route("/<preset_id>", methods=["DELETE"])
def delete_preset(preset_id):
    user, error = _guard(write=True)
    if error:
        return error
    presets, index = _find(user, preset_id)
    if index is None:
        return jsonify({"error": "Preset nicht gefunden."}), 404
    presets.pop(index)
    _save(user, presets, active="" if user.get("screensaver_preset") == preset_id else None)
    return jsonify(_public(user))


@presets_bp.route("/order", methods=["POST"])
def reorder_presets():
    """Reihenfolge = Tastenbelegung Strg+Alt+1 bis 9."""
    user, error = _guard(write=True)
    if error:
        return error
    ids = (request.get_json(silent=True) or {}).get("ids")
    presets = _presets(user)
    if not isinstance(ids, list) or sorted(map(str, ids)) != sorted(entry["id"] for entry in presets):
        return jsonify({"error": "Reihenfolge passt nicht zu den Presets."}), 400
    by_id = {entry["id"]: entry for entry in presets}
    _save(user, [by_id[str(preset_id)] for preset_id in ids])
    return jsonify(_public(user))


@presets_bp.route("/<preset_id>/apply", methods=["POST"])
def apply(preset_id):
    user, error = _guard(write=True)
    if error:
        return error
    presets, index = _find(user, preset_id)
    if index is None:
        return jsonify({"error": "Preset nicht gefunden."}), 404
    settings, flow, player, missing = apply_preset(user, presets[index])
    return jsonify({**_public(user), "preset": preset_id, "settings": settings, "flow": flow,
                    "player": player, "player_from_preset": isinstance(presets[index].get("player"), dict),
                    "missing": missing})


__all__ = ["presets_bp", "apply_preset", "MAX_PRESETS"]
