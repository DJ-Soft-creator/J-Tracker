"""Internetradio fuer den Player: kuratierte Sender, eigene Streams, Proxy.

Die Wiedergabe laeuft ueber ``/radio/<id>/stream``: der Server holt den Stream
(``screensaver_remote``) und reicht ihn durch, weil die CSP bei
``media-src 'self'`` bleibt und der Analyser fuer die Lichteffekte nur
Same-Origin-Audio hoert. Range wird nicht weitergegeben - live gibt es nichts
zu spulen, und manche Server (SomaFM) lehnen Range ab.

Kuratiert sind ruhige, werbefreie MP3-Streams; eigene Sender liegen je Konto
in ``<user>/screensaver/stations.json``. Eine ``.m3u``/``.pls``-Adresse wird
beim Abspielen aufgeloest; HLS (``.m3u8``) koennen Chrome und Firefox ohne
Zusatzbibliothek nicht abspielen und wird deshalb abgelehnt.
"""

import re

from flask import Blueprint, Response, jsonify, request

import screensaver
import screensaver_remote as remote


radio_bp = Blueprint("screensaver_radio", __name__, url_prefix="/radio")

STATIONS_FILE = "screensaver/stations.json"
MAX_STATIONS = 30
MAX_LABEL = 60
PLAYLIST_LIMIT = 64 * 1024
_ID_RE = re.compile(r"^[0-9a-f]{40}$")
PLAYLIST_TYPES = ("audio/x-mpegurl", "audio/mpegurl", "application/x-mpegurl", "audio/x-scpls",
                  "application/pls+xml", "application/vnd.apple.mpegurl")
STREAM_TYPES = ("audio/", "application/ogg")

# (Schluessel, Name, Richtung, Anbieter, Stream, Logo)
CURATED = (
    ("somafm-groove-salad", "Groove Salad", "Lofi · Downtempo", "SomaFM",
     "https://ice1.somafm.com/groovesalad-128-mp3", "https://somafm.com/img3/groovesalad-400.jpg"),
    ("somafm-fluid", "Fluid", "Lofi · Instrumental Hip-Hop", "SomaFM",
     "https://ice1.somafm.com/fluid-128-mp3", "https://somafm.com/img3/fluid-400.jpg"),
    ("somafm-drone-zone", "Drone Zone", "Ambient", "SomaFM",
     "https://ice1.somafm.com/dronezone-128-mp3", "https://somafm.com/img3/dronezone-400.jpg"),
    ("somafm-deep-space-one", "Deep Space One", "Ambient · Space", "SomaFM",
     "https://ice1.somafm.com/deepspaceone-128-mp3", "https://somafm.com/img3/deepspaceone-400.jpg"),
    ("radio-swiss-classic", "Radio Swiss Classic", "Klassik", "SRG SSR",
     "https://stream.srg-ssr.ch/m/rsc_de/mp3_128", ""),
    ("radio-swiss-jazz", "Radio Swiss Jazz", "Jazz", "SRG SSR",
     "https://stream.srg-ssr.ch/m/rsj/mp3_128", ""),
    ("br-klassik", "BR-Klassik", "Klassik", "BR",
     "https://dispatcher.rndfnk.com/br/brklassik/live/mp3/mid", ""),
    ("ndr-kultur", "NDR Kultur", "Klassik · Kultur", "NDR",
     "https://icecast.ndr.de/ndr/ndrkultur/live/mp3/128/stream.mp3", ""),
    ("swr-kultur", "SWR Kultur", "Klassik · Kultur", "SWR",
     "https://liveradio.swr.de/sw282p3/swr2/play.mp3", ""),
)


def _curated_id(key):
    return screensaver._digest(f"station:{key}")


def _custom_id(url):
    return screensaver._digest(f"station:custom:{url}")


def _label(value):
    return " ".join(str(value or "").split())[:MAX_LABEL].strip()


def _art_url(url):
    return f"/api/screensaver/player/art/{remote.art_id(url)}" if url else ""


def _stations_path(user_id):
    return screensaver._user_root(user_id) / STATIONS_FILE


def custom_stations(user_id):
    stored = screensaver._read_json(_stations_path(user_id)) or {}
    result = []
    for entry in stored.get("stations") or []:
        if isinstance(entry, dict) and _ID_RE.match(str(entry.get("id") or "")) and isinstance(entry.get("url"), str):
            result.append({"id": entry["id"], "label": _label(entry.get("label")) or "Eigener Sender",
                           "url": entry["url"], "logo": str(entry.get("logo") or "")})
    return result


def stations(user_id):
    """Alle Sender eines Kontos, kuratierte zuerst, mit internen URLs."""
    curated = [{"id": _curated_id(key), "label": label, "genre": genre, "provider": provider,
                "url": url, "logo": logo, "custom": False}
               for key, label, genre, provider, url, logo in CURATED]
    own = [{**entry, "genre": "Eigener Sender", "provider": "", "custom": True} for entry in custom_stations(user_id)]
    return curated + own


def station(user_id, station_id):
    return next((entry for entry in stations(user_id) if entry["id"] == station_id), None)


def _public(entry):
    return {"id": entry["id"], "label": entry["label"], "genre": entry["genre"], "provider": entry["provider"],
            "cover": _art_url(entry.get("logo")), "custom": entry["custom"],
            "url": entry["url"] if entry["custom"] else ""}


def tracks_for(user_id):
    return [{
        "id": entry["id"],
        "src": f"/api/screensaver/player/radio/{entry['id']}/stream",
        "cover": _art_url(entry.get("logo")),
        "title": entry["label"],
        "artist": entry["genre"],
        "album": entry["provider"],
        "source": entry["provider"] or entry["genre"],
        "source_id": entry["id"],
        "live": True,
    } for entry in stations(user_id)]


def known_art(user_id, art):
    for entry in stations(user_id):
        if entry.get("logo") and remote.art_id(entry["logo"]) == art:
            return entry["logo"]
    return None


# ─── Stream ──────────────────────────────────────────────────────────

def _playlist_target(data):
    """Erste Stream-URL aus M3U oder PLS; HLS wird abgelehnt."""
    text = data.decode("utf-8", errors="replace")
    if "#EXT-X-" in text:
        raise remote.RemoteError("HLS-Streams (.m3u8) werden nicht unterstützt")
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if line.lower().startswith(("http://", "https://")):
            return line
    raise remote.RemoteError("Die Playlist enthält keinen Stream")


def open_stream(url):
    """Stream oeffnen; eine Playlist dazwischen wird einmal aufgeloest."""
    for _ in range(2):
        upstream = remote.open_url(url, headers={"Accept": "audio/*, */*;q=0.5"})
        if upstream.status != 200:
            status = upstream.status
            upstream.close()
            raise remote.RemoteError(f"Sender antwortet mit {status}")
        kind = upstream.content_type()
        path = upstream.url.split("?", 1)[0].lower()
        if kind in PLAYLIST_TYPES or path.endswith((".m3u", ".pls", ".m3u8")):
            try:
                url = _playlist_target(upstream.read_limited(PLAYLIST_LIMIT))
            finally:
                upstream.close()
            continue
        if not kind.startswith(STREAM_TYPES) and kind not in ("application/octet-stream", ""):
            upstream.close()
            raise remote.RemoteError("Unter dieser Adresse liegt kein Audiostream")
        return upstream
    raise remote.RemoteError("Playlist verweist auf eine weitere Playlist")


# ─── Routen ──────────────────────────────────────────────────────────

@radio_bp.route("", methods=["GET"])
def list_stations():
    user, error = screensaver._browser_user()
    if error:
        return error
    return jsonify({"stations": [_public(entry) for entry in stations(user["id"])]})


@radio_bp.route("", methods=["POST"])
def add_station():
    """Eigenen Sender anlegen; der Stream wird dabei einmal kurz angetestet."""
    user, error = screensaver._guarded_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    logo = str(data.get("logo") or "").strip()
    label = _label(data.get("label"))
    own = custom_stations(user["id"])
    if len(own) >= MAX_STATIONS:
        return jsonify({"error": f"Höchstens {MAX_STATIONS} eigene Sender."}), 400
    try:
        remote.check_url(url)
        if logo:
            remote.check_url(logo)
        if any(entry["url"] == url for entry in stations(user["id"])):
            return jsonify({"error": "Diesen Sender gibt es schon."}), 409
        open_stream(url).close()
    except remote.RemoteError as exc:
        return jsonify({"error": str(exc)}), 400
    entry = {"id": _custom_id(url), "label": label or url.split("//", 1)[-1].split("/", 1)[0],
             "url": url, "logo": logo}
    screensaver._write_json(_stations_path(user["id"]), {"stations": own + [entry]})
    return jsonify({"station": _public({**entry, "genre": "Eigener Sender", "provider": "", "custom": True})}), 201


@radio_bp.route("/<station_id>", methods=["DELETE"])
def remove_station(station_id):
    user, error = screensaver._guarded_user()
    if error:
        return error
    own = custom_stations(user["id"])
    remaining = [entry for entry in own if entry["id"] != station_id]
    if len(remaining) == len(own):
        return jsonify({"error": "Nur eigene Sender lassen sich löschen."}), 404
    screensaver._write_json(_stations_path(user["id"]), {"stations": remaining})
    return jsonify({"ok": True})


@radio_bp.route("/<station_id>/stream", methods=["GET"])
def stream(station_id):
    user, error = screensaver._browser_user()
    if error:
        return error
    entry = station(user["id"], station_id) if _ID_RE.match(station_id or "") else None
    if entry is None:
        return jsonify({"error": "Unbekannter Sender"}), 404
    if not remote.SLOTS.acquire(user["id"]):
        return jsonify({"error": "Zu viele gleichzeitige Streams."}), 429
    try:
        upstream = open_stream(entry["url"])
    except remote.RemoteError as exc:
        remote.SLOTS.release(user["id"])
        return jsonify({"error": str(exc)}), 502
    kind = upstream.content_type()
    response = Response(remote.relay(upstream), mimetype=kind if kind.startswith(STREAM_TYPES) else "audio/mpeg")
    response.headers["Cache-Control"] = "no-store"
    user_id = user["id"]

    def _close():
        upstream.close()
        remote.SLOTS.release(user_id)

    response.call_on_close(_close)
    return response


__all__ = ["radio_bp", "stations", "station", "tracks_for", "known_art", "CURATED"]
