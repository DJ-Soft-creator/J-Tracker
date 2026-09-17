"""Sammelmappen: Tagesablage fuer unbenutzte Medien und Themenmappen.

Zwei Arten, eine Mechanik (``docs/project/Anforderung-Sammelmappen.md``):

* **Tagesablage** — was an einem Tag hereinkam und nicht verwendet wurde,
  wandert am Tagesende in ``<user>/YYYY/MM/DD/media/Tagesablage/``. Damit
  verschwindet es aus der Screenshot-Inbox (E-12), bleibt aber rueckwirkend
  auffindbar.
* **Themenmappe** — ein benannter, tagesuebergreifender Behaelter unter
  ``<user>/mappen/<slug>.md`` mit JSON-Sidecar (E-44). Er sammelt Verweise auf
  Journalstellen und Medien, nicht deren Kopien.

Verbindliche Invariante zur DB-Migration: **referenzierte Medien werden nie
verschoben.** Ein gueltiger ``jt:media``-Marker verlangt ein hashgleiches
Attachment unter dem datierten ``media/``-Pfad
(``docs/db/phase-10-media-inventory.md``); eingesammelt wird ausschliesslich,
was in keinem Journal referenziert ist.
"""

import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file


buckets_bp = Blueprint("buckets", __name__, url_prefix="/api/buckets")

SCHEMA_VERSION = 1
DAY_BUCKET = "Tagesablage"
FOLDER = "mappen"
MAX_TITLE_LENGTH = 80
MAX_NOTE_LENGTH = 500
MAX_ITEMS = 500
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ITEM_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
_ANCHOR_RE = re.compile(r"^([0-9a-f]{1,8})-(\d{4}-\d{2}-\d{2})-(\d{1,4})$")
_ENTRY_ID_RE = re.compile(r"^[0-9a-f]{12}$")
EXCERPT_LENGTH = 400


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _now():
    main = _main()
    return main.get_tz_aware_now()[0] if main else datetime.now().astimezone()


def _iso(value):
    return value.isoformat(timespec="seconds")


def _user_root(user_id):
    return _main().DATA_DIR / str(user_id)


def _bucket_dir(user_id):
    return _user_root(user_id) / FOLDER


def _browser_user():
    user = _main()._current_user()
    return (user, None) if user else (None, (jsonify({"error": "Unauthorized"}), 401))


def _csrf_error():
    return _main().csrf_protect(lambda: None)()


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o660)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_sidecar(path, payload):
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_json(path):
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def slugify(title):
    # Der Bindestrich bleibt erhalten: "Ticket-Kette" soll nicht zu
    # "ticketkette" verschmelzen.
    cleaned = re.sub(r"[^0-9A-Za-zäöüÄÖÜß -]+", "", title or "")
    cleaned = cleaned.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    cleaned = cleaned.replace("Ä", "ae").replace("Ö", "oe").replace("Ü", "ue").replace("ß", "ss")
    cleaned = re.sub(r"[\s-]+", "-", cleaned.strip()).lower()
    return cleaned[:60].strip("-")


def _validated_slug(value):
    return value if isinstance(value, str) and _SLUG_RE.match(value) else None


def _paths(user_id, slug):
    directory = _bucket_dir(user_id)
    return directory / f"{slug}.md", directory / f"{slug}.json"


def _public(item):
    return {
        "slug": item.get("slug"),
        "title": item.get("title") or item.get("slug"),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
        "status": item.get("status") or "offen",
        "items": len(item.get("items") or []),
    }


# ─── Verweis auf eine Journalstelle aufloesen ────────────────────────
# Der Anker des Heute-Tabs lautet ``<hash>-<YYYY-MM-DD>-<index>``; der Hash ist
# der JS-``simpleHash`` des Eintragstexts (``*-app-core.js``). Aufgeloest wird
# **live aus dem Markdown**, damit die Mappe eine Referenz bleibt und nie eine
# Kopie: aendert sich der Eintrag, aendert sich hier die Vorschau mit.
def _js_code_units(text):
    for character in text:
        code = ord(character)
        if code > 0xFFFF:
            code -= 0x10000
            yield 0xD800 + (code >> 10)
            yield 0xDC00 + (code & 0x3FF)
        else:
            yield code


def anchor_hash(text):
    """Der 32-Bit-Hash aus ``simpleHash`` — bitgleich zur Fassung im Browser."""
    value = 0
    for unit in _js_code_units(text or ""):
        value = (value * 31 + unit) & 0xFFFFFFFF
    if value >= 0x80000000:
        value -= 0x100000000
    return format(abs(value), "x")


def _day_entries(user_id, day, cache):
    """Die Eintraege eines Journaltags in der Reihenfolge des Heute-Tabs."""
    if day not in cache:
        main = _main()
        try:
            cache[day] = main._get_today_entries(user_id=user_id, journal_date=day) or []
        except Exception:  # noqa: BLE001 — ein unlesbarer Tag darf die Mappe nicht kippen
            cache[day] = []
    return cache[day]


def _brain_block(user_id, doc_id, fingerprint):
    """Den Brain-Block hinter einem Verweis lesen — ueber die Brain-Fassade."""
    brain = sys.modules.get("brain")
    resolve = getattr(brain, "_resolve_document", None) if brain else None
    if not callable(resolve):
        return None
    try:
        document, _, _, _ = resolve(user_id, doc_id)
    except Exception:  # noqa: BLE001 — ein defektes Dokument darf die Mappe nicht kippen
        return None
    if not document:
        return None
    for block in document.get("blocks") or []:
        if fingerprint in (block.get("fingerprint"), block.get("legacy_fingerprint")):
            return {"content": block.get("text") or "", "file": document.get("path") or "",
                    "media": 0}
    return None


def _entry_id():
    return uuid.uuid4().hex[:12]


def _markdown_line(entry):
    """Die Zeile, mit der ein Verweis in der Markdown-Mappe steht."""
    label = (entry.get("path") or entry.get("doc_id") or "Brain-Block") \
        if entry.get("kind") == "brain" else f"[[{entry.get('date')}]]"
    note = entry.get("note")
    return f"- {label}" + (f" — {note}" if note else "") + "\n"


def _resolve_reference(user_id, entry, cache):
    """Vorschautext und Fundstatus zu einem gespeicherten Verweis."""
    if entry.get("kind") == "brain":
        return _brain_block(user_id, entry.get("doc_id") or "", entry.get("fingerprint") or "")
    day = entry.get("date") or ""
    anchor = str(entry.get("anchor") or "")
    if not _DAY_RE.match(day):
        return None
    entries = _day_entries(user_id, day, cache)
    if not entries:
        return None
    match = _ANCHOR_RE.match(anchor)
    if not match:
        return None
    wanted, index = match.group(1), int(match.group(3))
    # Der Index zeigt auf die Stelle von damals, der Hash auf den Text. Erst den
    # Index pruefen, dann den ganzen Tag durchsehen — so ueberlebt der Verweis
    # spaetere Eintraege ueber oder unter ihm.
    candidates = [entries[index]] if 0 <= index < len(entries) else []
    candidates += [item for position, item in enumerate(entries) if position != index]
    for candidate in candidates:
        content = candidate.get("content") or ""
        if anchor_hash(content) == wanted:
            return {"content": content, "file": candidate.get("file") or "",
                    "media": len(candidate.get("media") or [])}
    return None


def _reference_payload(user_id, entries):
    """Gespeicherte Verweise um Vorschautext, Datei und Fundstatus ergaenzen."""
    cache = {}
    resolved = []
    for entry in entries:
        item = dict(entry)
        found = _resolve_reference(user_id, entry, cache)
        if found:
            text = found["content"].strip()
            item["excerpt"] = text[:EXCERPT_LENGTH]
            item["truncated"] = len(text) > EXCERPT_LENGTH
            item["file"] = found["file"]
            item["media"] = found["media"]
            item["found"] = True
        else:
            item["excerpt"] = ""
            item["truncated"] = False
            item["found"] = False
        resolved.append(item)
    return resolved


# ─── Themenmappen ────────────────────────────────────────────────────
@buckets_bp.route("", methods=["GET"])
def list_buckets():
    user, error = _browser_user()
    if error:
        return error
    directory = _bucket_dir(user["id"])
    items = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            if not _validated_slug(path.stem):
                continue
            entry = _read_json(path)
            if entry:
                items.append(_public(entry))
    items.sort(key=lambda entry: entry.get("updated_at") or "", reverse=True)
    response = jsonify({"items": items})
    response.headers["Cache-Control"] = "no-store"
    return response


@buckets_bp.route("", methods=["POST"])
def create_bucket():
    user, error = _browser_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf is not None:
        return csrf
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()[:MAX_TITLE_LENGTH]
    if not title:
        return jsonify({"error": "Bitte einen Namen angeben"}), 400
    slug = slugify(title)
    if not _validated_slug(slug):
        return jsonify({"error": "Aus diesem Namen lässt sich kein Ordnername bilden"}), 400
    markdown_path, sidecar_path = _paths(user["id"], slug)
    if sidecar_path.exists():
        return jsonify({"error": "Diese Mappe gibt es schon", "slug": slug}), 409

    now = _now()
    _atomic_text(markdown_path, f"# {title}\n\nAngelegt: {_iso(now)}\n")
    _write_sidecar(sidecar_path, {
        "schema": SCHEMA_VERSION, "slug": slug, "title": title, "status": "offen",
        "created_at": _iso(now), "updated_at": _iso(now), "items": [],
    })
    return jsonify({"ok": True, "slug": slug, "title": title}), 201


@buckets_bp.route("/<slug>", methods=["GET"])
def read_bucket(slug):
    user, error = _browser_user()
    if error:
        return error
    valid = _validated_slug(slug)
    if not valid:
        return jsonify({"error": "Unbekannte Mappe"}), 404
    markdown_path, sidecar_path = _paths(user["id"], valid)
    item = _read_json(sidecar_path)
    if item is None:
        return jsonify({"error": "Unbekannte Mappe"}), 404
    body = ""
    if markdown_path.is_file() and not markdown_path.is_symlink():
        try:
            body = markdown_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            body = ""
    entries = [entry for entry in (item.get("items") or [])[-MAX_ITEMS:] if isinstance(entry, dict)]
    # Verweise aus der ersten Fassung haben noch keine Id. Ohne sie liesse sich
    # kein einzelner Verweis loeschen, also bekommen sie hier eine — einmalig
    # und im selben Zug gespeichert.
    if any(not _ENTRY_ID_RE.match(str(entry.get("id") or "")) for entry in entries):
        for entry in entries:
            if not _ENTRY_ID_RE.match(str(entry.get("id") or "")):
                entry["id"] = _entry_id()
        item["items"] = entries
        _write_sidecar(sidecar_path, item)
    payload = _public(item)
    payload["entries"] = _reference_payload(user["id"], entries)
    payload["text"] = body
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@buckets_bp.route("/<slug>/items", methods=["POST"])
def add_item(slug):
    """Eine Journalstelle einer Mappe zuordnen — als Verweis, nicht als Kopie."""
    user, error = _browser_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf is not None:
        return csrf
    valid = _validated_slug(slug)
    if not valid:
        return jsonify({"error": "Unbekannte Mappe"}), 404
    markdown_path, sidecar_path = _paths(user["id"], valid)
    item = _read_json(sidecar_path)
    if item is None:
        return jsonify({"error": "Unbekannte Mappe"}), 404

    data = request.get_json(silent=True) or {}
    kind = "brain" if str(data.get("kind") or "").strip() == "brain" else "journal"
    day = str(data.get("date") or "").strip()
    if kind == "brain" and not day:
        day = _now().strftime("%Y-%m-%d")
    if not _DAY_RE.match(day):
        return jsonify({"error": "Ungültiges Datum"}), 400
    note = str(data.get("note") or "").strip()[:MAX_NOTE_LENGTH]
    anchor = str(data.get("anchor") or "").strip()[:200]

    entries = item.get("items") if isinstance(item.get("items"), list) else []
    if kind == "brain":
        # Ein Brain-Treffer wird ueber Dokument und Block adressiert, nicht ueber
        # die Stelle im Tagesjournal.
        doc_id = str(data.get("doc_id") or "").strip()[:400]
        fingerprint = str(data.get("fingerprint") or "").strip()[:120]
        if not doc_id or not fingerprint:
            return jsonify({"error": "Zu diesem Treffer fehlt die Kennung"}), 400
        if any(entry.get("doc_id") == doc_id and entry.get("fingerprint") == fingerprint
               for entry in entries if isinstance(entry, dict)):
            return jsonify({"ok": True, "duplicate": True, "items": len(entries)})
        entry = {"kind": "brain", "id": _entry_id(), "date": day, "doc_id": doc_id,
                 "fingerprint": fingerprint, "path": str(data.get("path") or "").strip()[:400],
                 "note": note, "added_at": _iso(_now())}
    else:
        if any(entry.get("date") == day and entry.get("anchor") == anchor for entry in entries
               if isinstance(entry, dict)):
            return jsonify({"ok": True, "duplicate": True, "items": len(entries)})
        entry = {"kind": "journal", "id": _entry_id(), "date": day, "anchor": anchor,
                 "note": note, "added_at": _iso(_now())}
    entries.append(entry)
    item["items"] = entries[-MAX_ITEMS:]
    item["updated_at"] = _iso(_now())
    _write_sidecar(sidecar_path, item)

    # Der Verweis steht auch im Markdown, damit die Mappe ohne die App lesbar
    # bleibt — die maßgebliche Quelle bleibt aber das Journal selbst.
    try:
        with markdown_path.open("a", encoding="utf-8") as handle:
            handle.write(_markdown_line(entry))
    except OSError:
        pass
    return jsonify({"ok": True, "items": len(item["items"]), "id": entry["id"]})


@buckets_bp.route("/<slug>/items/<item_id>", methods=["DELETE"])
def delete_item(slug, item_id):
    """Einen Verweis aus der Mappe nehmen — das Journal bleibt unberuehrt."""
    user, error = _browser_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf is not None:
        return csrf
    valid = _validated_slug(slug)
    if not valid or not _ENTRY_ID_RE.match(item_id or ""):
        return jsonify({"error": "Unbekannter Verweis"}), 404
    markdown_path, sidecar_path = _paths(user["id"], valid)
    item = _read_json(sidecar_path)
    if item is None:
        return jsonify({"error": "Unbekannte Mappe"}), 404
    entries = [entry for entry in (item.get("items") or []) if isinstance(entry, dict)]
    removed = next((entry for entry in entries if entry.get("id") == item_id), None)
    if removed is None:
        return jsonify({"error": "Unbekannter Verweis"}), 404
    item["items"] = [entry for entry in entries if entry is not removed]
    item["updated_at"] = _iso(_now())
    _write_sidecar(sidecar_path, item)

    # Dieselbe Zeile auch aus der Markdown-Mappe nehmen, damit beide Fassungen
    # dasselbe erzaehlen. Nur die erste Fundstelle, alles Weitere bleibt stehen.
    try:
        if markdown_path.is_file() and not markdown_path.is_symlink():
            line = _markdown_line(removed)
            body = markdown_path.read_text(encoding="utf-8")
            if line in body:
                _atomic_text(markdown_path, body.replace(line, "", 1))
    except OSError:
        pass
    return jsonify({"ok": True, "items": len(item["items"])})


@buckets_bp.route("/<slug>", methods=["DELETE"])
def delete_bucket(slug):
    """Eine Themenmappe loeschen. Verwiesene Journalstellen bleiben bestehen."""
    user, error = _browser_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf is not None:
        return csrf
    valid = _validated_slug(slug)
    if not valid:
        return jsonify({"error": "Unbekannte Mappe"}), 404
    markdown_path, sidecar_path = _paths(user["id"], valid)
    if not sidecar_path.is_file() or sidecar_path.is_symlink():
        return jsonify({"error": "Unbekannte Mappe"}), 404
    try:
        sidecar_path.unlink(missing_ok=True)
        if not markdown_path.is_symlink():
            markdown_path.unlink(missing_ok=True)
    except OSError:
        return jsonify({"error": "Die Mappe ließ sich nicht löschen"}), 500
    return jsonify({"ok": True, "slug": valid})


# ─── Tagesablage ─────────────────────────────────────────────────────
def _day_bucket_dir(user_id, day):
    year, month, date = day.split("-")
    return _user_root(user_id) / year / month / date / "media" / DAY_BUCKET


def _media_file(directory, item, *, preview):
    """Bild oder Vorschau eines eingesammelten Mediums — nur im Tagesordner.

    Der Sammler verschiebt die Dateien, laesst ``source_path``/``preview_path``
    im Sidecar aber auf den alten Inbox-Pfad zeigen. Massgeblich ist deshalb der
    **Dateiname** neben dem Sidecar, nie der gespeicherte Pfad.
    """
    relative = item.get("preview_path" if preview else "source_path")
    if not isinstance(relative, str) or not relative:
        return None
    candidate = directory / Path(relative).name
    if not candidate.is_file() or candidate.is_symlink():
        return None
    try:
        candidate.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return None
    return candidate


@buckets_bp.route("/tagesablage/<day>", methods=["GET"])
def read_day_bucket(day):
    """Was an einem vergangenen Tag unbenutzt liegen blieb."""
    user, error = _browser_user()
    if error:
        return error
    if not _DAY_RE.match(day or ""):
        return jsonify({"error": "Ungültiges Datum"}), 400
    directory = _day_bucket_dir(user["id"], day)
    items = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            entry = _read_json(path)
            if not entry:
                continue
            items.append({
                "id": entry.get("id"),
                "type": entry.get("type") or "image",
                "captured_at": entry.get("captured_at") or "",
                "collected_at": entry.get("collected_at") or "",
                "origin": entry.get("origin") or "",
                "original_filename": entry.get("original_filename") or "",
                "mime_type": entry.get("mime_type") or "",
                "size": entry.get("size") or 0,
                "has_preview": bool(_media_file(path.parent, entry, preview=True)),
                "has_file": bool(_media_file(path.parent, entry, preview=False)),
            })
    response = jsonify({"date": day, "items": items})
    response.headers["Cache-Control"] = "no-store"
    return response


@buckets_bp.route("/tagesablage", methods=["GET"])
def list_day_buckets():
    """Alle Tage, an denen etwas in der Ablage liegt — neueste zuerst."""
    user, error = _browser_user()
    if error:
        return error
    root = _user_root(user["id"])
    days = []
    for path in root.glob(f"[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/media/{DAY_BUCKET}"):
        if not path.is_dir():
            continue
        count = sum(1 for _ in path.glob("*.json"))
        if not count:
            continue
        parts = path.parts
        days.append({"date": f"{parts[-5]}-{parts[-4]}-{parts[-3]}", "items": count})
    days.sort(key=lambda entry: entry["date"], reverse=True)
    response = jsonify({"days": days})
    response.headers["Cache-Control"] = "no-store"
    return response


@buckets_bp.route("/tagesablage/<day>/<item_id>/preview", methods=["GET"])
def day_bucket_preview(day, item_id):
    """Ein eingesammeltes Medium anzeigen — Vorschau, mit ``?full=1`` das Original."""
    user, error = _browser_user()
    if error:
        return error
    if not _DAY_RE.match(day or "") or not _ITEM_RE.match(item_id or ""):
        return jsonify({"error": "Unbekanntes Medium"}), 404
    directory = _day_bucket_dir(user["id"], day)
    if not directory.is_dir():
        return jsonify({"error": "Unbekanntes Medium"}), 404
    full = request.args.get("full") in ("1", "true", "yes")
    for path in sorted(directory.glob("*.json")):
        entry = _read_json(path)
        if not entry or entry.get("id") != item_id:
            continue
        file_path = (not full and _media_file(directory, entry, preview=True)) \
            or _media_file(directory, entry, preview=False) \
            or _media_file(directory, entry, preview=True)
        if not file_path:
            return jsonify({"error": "Datei nicht gefunden"}), 404
        mimetype = "image/jpeg" if file_path.name.endswith("_preview.jpg") \
            else (entry.get("mime_type") or None)
        response = send_file(file_path, mimetype=mimetype, conditional=True)
        response.headers["Cache-Control"] = "private, max-age=300"
        return response
    return jsonify({"error": "Unbekanntes Medium"}), 404
