"""Podcasts fuer den Player: Abos, Episodenlisten, Fortschritt je Episode.

Abonniert wird per RSS-URL. Den Feed liest der Server (``screensaver_remote``)
und legt das Ergebnis je Feed unter ``DATA_DIR/cache/screensaver/podcasts/``
ab - jederzeit loeschbar, nach ``FEED_TTL`` beim naechsten Abruf erneuert, bei
einem Fehler gilt der alte Stand weiter. Die Episoden selbst laedt niemand vorab:
``/episode/<id>`` reicht sie mit Range an den Browser durch, damit die
Wiedergabe sofort startet und Spulen geht.

Je Konto liegen unter ``<user>/screensaver/``:

* ``podcasts.json`` - die Abos samt Tempo je Podcast,
* ``episodes.json`` - Position, Dauer und „gehoert“ je Episode (ab 95 %).

Die Kennung einer Episode ist ein Hash aus Feed und GUID: sie bleibt stabil,
auch wenn der Anbieter die Audio-URL tauscht. Ausgeliefert wird nur eine
Episode aus einem Feed, den das Konto abonniert hat; eine URL aus der Anfrage
wird nie verwendet.
"""

import email.utils
import html
import re
import time
import xml.etree.ElementTree as ET

from flask import Blueprint, Response, jsonify, request

import screensaver
import screensaver_remote as remote


podcasts_bp = Blueprint("screensaver_podcasts", __name__, url_prefix="/podcasts")

FEED_FOLDER = "cache/screensaver/podcasts"
SUBSCRIPTIONS_FILE = "screensaver/podcasts.json"
PROGRESS_FILE = "screensaver/episodes.json"
FEED_TTL = 1800
FEED_LIMIT = 15 * 1024 * 1024
MAX_FEEDS = 50
MAX_EPISODES = 500
PROGRESS_LIMIT = 5000
DONE_AT = 0.95
RATE_MIN, RATE_MAX = 0.8, 2.0
SUMMARY_LIMIT = 280
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
_ID_RE = re.compile(r"^[0-9a-f]{40}$")
_RANGE_RE = re.compile(r"^bytes=\d*-\d*$")
_TAG_RE = re.compile(r"<[^>]+>")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav")


# ─── Feed lesen ──────────────────────────────────────────────────────

def feed_id(url):
    return screensaver._digest(f"feed:{url.strip()}")


def _text(element, limit=200):
    if element is None or element.text is None:
        return ""
    return " ".join(element.text.split())[:limit]


def _summary(element):
    raw = html.unescape(_TAG_RE.sub(" ", (element.text or "") if element is not None else ""))
    text = " ".join(raw.split())
    return text if len(text) <= SUMMARY_LIMIT else text[:SUMMARY_LIMIT - 1].rstrip() + "…"


def _duration(value):
    """``itunes:duration`` als Sekunden: ``3600``, ``59:30`` oder ``1:02:03``."""
    parts = (value or "").strip().split(":")
    try:
        numbers = [float(part) for part in parts if part != ""]
    except ValueError:
        return 0
    if not numbers or len(numbers) > 3 or any(number < 0 for number in numbers):
        return 0
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return int(seconds) if seconds < 7 * 24 * 3600 else 0


def _published(value):
    try:
        moment = email.utils.parsedate_to_datetime((value or "").strip())
    except (TypeError, ValueError, IndexError):
        return 0
    try:
        return int(moment.timestamp())
    except (OverflowError, OSError, ValueError):
        return 0


def _is_audio(url, kind):
    if kind.startswith("audio/"):
        return True
    if kind and not kind.startswith(("application/octet-stream", "binary/")):
        return False  # video/*, application/pdf ...
    return urlpath(url).lower().endswith(AUDIO_EXTENSIONS)


def urlpath(url):
    return url.split("?", 1)[0].split("#", 1)[0]


def parse_feed(raw, url):
    """RSS 2.0 (mit iTunes-Erweiterung) in ein schlankes Woerterbuch."""
    # Entity-Deklarationen braucht kein Podcast-Feed; ohne sie gibt es auch
    # keine Entity-Expansion.
    if b"<!ENTITY" in raw[:65536].upper():
        raise remote.RemoteError("Feed enthält unzulässige Deklarationen")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise remote.RemoteError("Kein gültiger RSS-Feed") from exc
    channel = root.find("channel")
    if channel is None:
        raise remote.RemoteError("Kein Podcast-Feed (RSS) unter dieser Adresse")
    identity = feed_id(url)
    image = channel.find(f"{ITUNES}image")
    cover = (image.get("href") or "").strip() if image is not None else ""
    if not cover:
        cover = _text(channel.find("image/url"), 1000)
    episodes, seen = [], set()
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        media = (enclosure.get("url") or "").strip()
        if not media or len(media) > remote.MAX_URL or not _is_audio(media, (enclosure.get("type") or "").lower()):
            continue
        guid = _text(item.find("guid"), 500) or media
        episode_id = screensaver._digest(f"episode:{identity}:{guid}")
        if episode_id in seen:
            continue
        seen.add(episode_id)
        picture = item.find(f"{ITUNES}image")
        episodes.append({
            "id": episode_id,
            "title": _text(item.find("title")) or "Ohne Titel",
            "url": media,
            "type": (enclosure.get("type") or "").lower()[:60],
            "published": _published(_text(item.find("pubDate"))),
            "duration": _duration(_text(item.find(f"{ITUNES}duration"))),
            "image": (picture.get("href") or "").strip()[:remote.MAX_URL] if picture is not None else "",
            "summary": _summary(item.find(f"{ITUNES}summary") if item.find(f"{ITUNES}summary") is not None
                                else item.find("description")),
        })
    episodes.sort(key=lambda entry: entry["published"], reverse=True)
    return {
        "url": url,
        "title": _text(channel.find("title")) or urlpath(url).rsplit("/", 1)[-1] or "Podcast",
        "author": _text(channel.find(f"{ITUNES}author")),
        "image": cover[:remote.MAX_URL],
        "episodes": episodes[:MAX_EPISODES],
        "fetched": int(time.time()),
    }


def _feed_path(identity):
    return screensaver._data_dir() / FEED_FOLDER / f"{identity}.json"


def load_feed(url, fresh=False):
    """Feed aus dem Cache oder frisch; ein Fehler beim Erneuern laesst den alten stehen."""
    identity = feed_id(url)
    cached = screensaver._read_json(_feed_path(identity))
    if cached and not fresh and time.time() - (cached.get("fetched") or 0) < FEED_TTL:
        return cached
    try:
        raw, _type, _final = remote.fetch(url, FEED_LIMIT, headers={
            "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"})
        feed = parse_feed(raw, url)
    except remote.RemoteError:
        if cached:
            return cached
        raise
    try:
        screensaver._write_json(_feed_path(identity), feed)
    except OSError:
        pass
    return feed


# ─── Abos und Fortschritt ────────────────────────────────────────────

def _subscriptions_path(user_id):
    return screensaver._user_root(user_id) / SUBSCRIPTIONS_FILE


def _rate(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 1.0
    return round(max(RATE_MIN, min(RATE_MAX, float(value))) * 20) / 20


def subscriptions(user_id):
    stored = screensaver._read_json(_subscriptions_path(user_id)) or {}
    feeds = []
    for entry in stored.get("feeds") or []:
        if isinstance(entry, dict) and isinstance(entry.get("url"), str) and _ID_RE.match(str(entry.get("id") or "")):
            feeds.append({"id": entry["id"], "url": entry["url"], "title": str(entry.get("title") or "")[:200],
                          "rate": _rate(entry.get("rate"))})
    return feeds


def _save_subscriptions(user_id, feeds):
    screensaver._write_json(_subscriptions_path(user_id), {"feeds": feeds})


def subscription(user_id, identity):
    return next((entry for entry in subscriptions(user_id) if entry["id"] == identity), None)


def _progress_path(user_id):
    return screensaver._user_root(user_id) / PROGRESS_FILE


def progress(user_id):
    stored = screensaver._read_json(_progress_path(user_id)) or {}
    episodes = stored.get("episodes")
    return episodes if isinstance(episodes, dict) else {}


def save_progress(user_id, episode_id, position, duration):
    """Position merken; ab 95 % gilt die Episode als gehoert (und beginnt neu)."""
    if not _ID_RE.match(episode_id or ""):
        return None
    entries = progress(user_id)
    duration = float(duration) if isinstance(duration, (int, float)) and 0 < duration < 7 * 24 * 3600 else 0.0
    position = max(0.0, float(position or 0))
    previous = entries.get(episode_id) if isinstance(entries.get(episode_id), dict) else {}
    done = bool(duration and position >= duration * DONE_AT) or (bool(previous.get("done")) and position < 1)
    entries[episode_id] = {
        "position": 0.0 if done else round(position, 1),
        "duration": round(duration or float(previous.get("duration") or 0), 1),
        "done": done,
        "updated": int(time.time()),
    }
    if len(entries) > PROGRESS_LIMIT:
        oldest = sorted(entries, key=lambda key: entries[key].get("updated", 0) if isinstance(entries[key], dict) else 0)
        for key in oldest[:len(entries) - PROGRESS_LIMIT]:
            entries.pop(key, None)
    screensaver._write_json(_progress_path(user_id), {"episodes": entries})
    return entries[episode_id]


def mark(user_id, episode_id, done):
    entries = progress(user_id)
    entry = entries.get(episode_id) if isinstance(entries.get(episode_id), dict) else {}
    entries[episode_id] = {**entry, "position": 0.0, "done": bool(done), "updated": int(time.time())}
    screensaver._write_json(_progress_path(user_id), {"episodes": entries})


# ─── Fuer den Player ─────────────────────────────────────────────────

def _art_url(art_url):
    return f"/api/screensaver/player/art/{remote.art_id(art_url)}" if art_url else ""


def tracks_for(user_id, identity):
    """Episoden eines abonnierten Podcasts, neueste zuerst; ``(titel, rate, fehler)``."""
    entry = subscription(user_id, identity) if identity else None
    if entry is None:
        return [], 1.0, "missing" if identity else ""
    try:
        feed = load_feed(entry["url"])
    except remote.RemoteError as exc:
        return [], entry["rate"], str(exc)
    marks = progress(user_id)
    title = feed.get("title") or entry["title"]
    items = []
    for episode in feed.get("episodes") or []:
        state = marks.get(episode["id"]) if isinstance(marks.get(episode["id"]), dict) else {}
        items.append({
            "id": episode["id"],
            "src": f"/api/screensaver/player/podcasts/episode/{episode['id']}",
            "cover": _art_url(episode.get("image") or feed.get("image")),
            "title": episode.get("title", ""),
            "artist": feed.get("author", ""),
            "album": title,
            "source": title,
            "source_id": identity,
            "published": episode.get("published", 0),
            "duration": state.get("duration") or episode.get("duration", 0),
            "position": state.get("position", 0),
            "done": bool(state.get("done")),
            "summary": episode.get("summary", ""),
        })
    return items, entry["rate"], ""


def known_art(user_id, art):
    """URL zu einer Bildkennung, wenn sie in einem abonnierten Feed vorkommt."""
    for entry in subscriptions(user_id):
        feed = screensaver._read_json(_feed_path(feed_id(entry["url"]))) or {}
        for url in [feed.get("image")] + [episode.get("image") for episode in feed.get("episodes") or []]:
            if url and remote.art_id(url) == art:
                return url
    return None


def _episode(user_id, episode_id):
    for entry in subscriptions(user_id):
        feed = screensaver._read_json(_feed_path(feed_id(entry["url"])))
        if feed is None:
            try:
                feed = load_feed(entry["url"])
            except remote.RemoteError:
                continue
        for episode in feed.get("episodes") or []:
            if episode.get("id") == episode_id:
                return episode
    return None


def _public(user_id, entry):
    feed = screensaver._read_json(_feed_path(feed_id(entry["url"]))) or {}
    marks = progress(user_id)
    episodes = feed.get("episodes") or []
    unheard = sum(1 for episode in episodes if not (marks.get(episode.get("id")) or {}).get("done"))
    return {
        "id": entry["id"], "url": entry["url"], "title": feed.get("title") or entry["title"],
        "author": feed.get("author", ""), "cover": _art_url(feed.get("image")),
        "count": len(episodes), "unheard": unheard, "rate": entry["rate"],
    }


# ─── Routen ──────────────────────────────────────────────────────────

@podcasts_bp.route("", methods=["GET"])
def list_feeds():
    user, error = screensaver._browser_user()
    if error:
        return error
    return jsonify({"feeds": [_public(user["id"], entry) for entry in subscriptions(user["id"])]})


@podcasts_bp.route("", methods=["POST"])
def subscribe():
    user, error = screensaver._guarded_user()
    if error:
        return error
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    feeds = subscriptions(user["id"])
    if len(feeds) >= MAX_FEEDS:
        return jsonify({"error": f"Höchstens {MAX_FEEDS} Podcasts."}), 400
    try:
        remote.check_url(url)
        identity = feed_id(url)
        if any(entry["id"] == identity for entry in feeds):
            return jsonify({"error": "Diesen Podcast hast du schon abonniert."}), 409
        feed = load_feed(url, fresh=True)
    except remote.RemoteError as exc:
        return jsonify({"error": str(exc)}), 400
    if not feed.get("episodes"):
        return jsonify({"error": "Der Feed enthält keine Audio-Episoden."}), 400
    entry = {"id": identity, "url": url, "title": feed.get("title", ""), "rate": 1.0}
    _save_subscriptions(user["id"], feeds + [entry])
    return jsonify({"feed": _public(user["id"], entry)}), 201


@podcasts_bp.route("/<identity>", methods=["PATCH"])
def update_feed(identity):
    """Tempo merken (``rate``); gilt fuer alle Episoden dieses Podcasts."""
    user, error = screensaver._guarded_user()
    if error:
        return error
    feeds = subscriptions(user["id"])
    entry = next((feed for feed in feeds if feed["id"] == identity), None)
    if entry is None:
        return jsonify({"error": "Podcast nicht gefunden."}), 404
    data = request.get_json(silent=True) or {}
    if "rate" in data:
        entry["rate"] = _rate(data["rate"])
    _save_subscriptions(user["id"], feeds)
    return jsonify({"feed": _public(user["id"], entry)})


@podcasts_bp.route("/<identity>", methods=["DELETE"])
def unsubscribe(identity):
    user, error = screensaver._guarded_user()
    if error:
        return error
    feeds = subscriptions(user["id"])
    remaining = [entry for entry in feeds if entry["id"] != identity]
    if len(remaining) == len(feeds):
        return jsonify({"error": "Podcast nicht gefunden."}), 404
    _save_subscriptions(user["id"], remaining)
    return jsonify({"ok": True})


@podcasts_bp.route("/<identity>/refresh", methods=["POST"])
def refresh(identity):
    user, error = screensaver._guarded_user()
    if error:
        return error
    entry = subscription(user["id"], identity)
    if entry is None:
        return jsonify({"error": "Podcast nicht gefunden."}), 404
    try:
        load_feed(entry["url"], fresh=True)
    except remote.RemoteError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"feed": _public(user["id"], entry)})


@podcasts_bp.route("/episode/<episode_id>/heard", methods=["POST"])
def heard(episode_id):
    user, error = screensaver._guarded_user()
    if error:
        return error
    if not _ID_RE.match(episode_id or "") or _episode(user["id"], episode_id) is None:
        return jsonify({"error": "Unbekannte Episode"}), 404
    mark(user["id"], episode_id, (request.get_json(silent=True) or {}).get("done") is not False)
    return jsonify({"ok": True})


@podcasts_bp.route("/episode/<episode_id>", methods=["GET"])
def episode_audio(episode_id):
    """Episode vom Anbieter durchreichen - mit Range, damit Spulen geht."""
    user, error = screensaver._browser_user()
    if error:
        return error
    episode = _episode(user["id"], episode_id) if _ID_RE.match(episode_id or "") else None
    if episode is None:
        return jsonify({"error": "Unbekannte Episode"}), 404
    headers = {"Accept": "audio/*, */*;q=0.5"}
    wanted = (request.headers.get("Range") or "").strip()
    if _RANGE_RE.match(wanted) and wanted != "bytes=-":
        headers["Range"] = wanted
    if not remote.SLOTS.acquire(user["id"]):
        return jsonify({"error": "Zu viele gleichzeitige Streams."}), 429
    try:
        upstream = remote.open_url(episode["url"], headers=headers)
    except remote.RemoteError as exc:
        remote.SLOTS.release(user["id"])
        return jsonify({"error": str(exc)}), 502
    if upstream.status not in (200, 206):
        status = upstream.status
        upstream.close()
        remote.SLOTS.release(user["id"])
        return jsonify({"error": f"Anbieter antwortet mit {status}"}), 416 if status == 416 else 502
    kind = upstream.content_type()
    if not kind.startswith("audio/"):
        kind = episode.get("type") if str(episode.get("type") or "").startswith("audio/") else "audio/mpeg"
    response = Response(remote.relay(upstream), status=upstream.status, mimetype=kind)
    for name in ("Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag"):
        value = upstream.headers.get(name)
        if value:
            response.headers[name] = value
    response.headers["Cache-Control"] = "private, no-store"
    user_id = user["id"]

    def _close():
        upstream.close()
        remote.SLOTS.release(user_id)

    response.call_on_close(_close)
    return response


__all__ = ["podcasts_bp", "tracks_for", "subscriptions", "subscription", "save_progress", "known_art",
           "parse_feed", "feed_id"]
