"""Abrufe fremder Hosts fuer den Player: Podcast-Feeds, Episoden, Radiostreams, Bilder.

Der Browser spricht nie selbst mit einem fremden Host - die CSP bleibt bei
``media-src 'self'`` und ``img-src 'self'``. Weil die URLs vom Nutzer kommen,
gilt fuer jeden Abruf:

* nur ``http``/``https``, keine Zugangsdaten in der URL, Ports 80, 443 oder ab
  1024 (Icecast laeuft gern auf 8000);
* nur oeffentliche Adressen - geprueft bei jeder Weiterleitung und gegen genau
  die Adresse, mit der danach verbunden wird. Ein zweites DNS-Lookup, das auf
  ein internes Ziel umgebogen werden koennte, gibt es nicht;
* kein Proxy aus der Umgebung, feste Timeouts, Groessengrenzen beim Lesen.

Streams laufen ueber ``relay``: das Werkzeug-Gewinde liest stueckweise vom
fremden Host und schreibt an den Browser, bis einer von beiden aufhoert. Jeder
offene Stream belegt einen Platz in ``SLOTS`` - je Konto und insgesamt
begrenzt, weil der Server mit ``threaded=True`` je Stream einen Thread haelt.
"""

import http.client
import ipaddress
import socket
import ssl
import threading
import urllib.parse

import screensaver
import screensaver_tags


USER_AGENT = "Journl/1.0 (self-hosted personal journal)"
TIMEOUT = 15
MAX_REDIRECTS = 8
MAX_URL = 2000
CHUNK = 32 * 1024
ART_FOLDER = "cache/screensaver/art"
ART_LIMIT = 5 * 1024 * 1024
ART_CACHE_LIMIT = 1000
_SAFE_PATH = "/?&=%:@!$'()*+,;~-._[]"


class RemoteError(ValueError):
    """Abruf nicht erlaubt oder fehlgeschlagen; die Meldung ist fuer den Nutzer."""


# ─── Pruefung ────────────────────────────────────────────────────────

def check_url(url):
    """Die URL zerlegt, wenn sie formal erlaubt ist - sonst ``RemoteError``."""
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL:
        raise RemoteError("Ungültige URL")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise RemoteError("Nur http- und https-Adressen")
    if parsed.username or parsed.password:
        raise RemoteError("Zugangsdaten in der URL sind nicht erlaubt")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RemoteError("Ungültiger Port") from exc
    if port is not None and port not in (80, 443) and port < 1024:
        raise RemoteError("Dieser Port ist nicht erlaubt")
    return parsed


def _public(raw):
    address = ipaddress.ip_address(raw.split("%", 1)[0])
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def resolve(host):
    """Erste Adresse des Hosts - nur, wenn **alle** seine Adressen oeffentlich sind."""
    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise RemoteError("Host nicht gefunden") from exc
    addresses = [record[4][0] for record in records]
    if not addresses:
        raise RemoteError("Host nicht gefunden")
    try:
        if not all(_public(raw) for raw in addresses):
            raise RemoteError("Interne oder lokale Adressen sind nicht erlaubt")
    except ValueError as exc:
        if isinstance(exc, RemoteError):
            raise
        raise RemoteError("Host nicht gefunden") from exc
    return addresses[0]


# ─── Verbindung ──────────────────────────────────────────────────────

class _HTTP(http.client.HTTPConnection):
    """Verbindet mit der geprueften Adresse, nicht mit einem neuen Lookup."""

    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _HTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        # Zertifikat und SNI gelten dem Hostnamen, verbunden wird mit der Adresse.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class Upstream:
    """Offene Antwort eines fremden Hosts; ``close`` schliesst auch die Verbindung."""

    def __init__(self, connection, response, url):
        self._connection = connection
        self._response = response
        self.url = url
        self.status = response.status
        self.headers = response.headers

    def content_type(self):
        return (self.headers.get_content_type() or "").lower()

    def read1(self, size=CHUNK):
        return self._response.read1(size)

    def read_limited(self, limit):
        length = self.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > limit:
            raise RemoteError("Antwort ist zu groß")
        try:
            data = self._response.read(limit + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise RemoteError("Abruf abgebrochen") from exc
        if len(data) > limit:
            raise RemoteError("Antwort ist zu groß")
        return data

    def close(self):
        try:
            self._response.close()
        finally:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def open_url(url, headers=None, timeout=TIMEOUT):
    """GET mit eigener Behandlung der Weiterleitungen; liefert ``Upstream``."""
    for _ in range(MAX_REDIRECTS + 1):
        parsed = check_url(url)
        address = resolve(parsed.hostname)
        kind = _HTTPS if parsed.scheme.lower() == "https" else _HTTP
        connection = kind(parsed.hostname, parsed.port, address, timeout)
        target = urllib.parse.quote(parsed.path or "/", safe=_SAFE_PATH)
        if parsed.query:
            target += "?" + urllib.parse.quote(parsed.query, safe=_SAFE_PATH)
        try:
            connection.request("GET", target, headers={
                "User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {}),
            })
            response = connection.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            connection.close()
            raise RemoteError("Server nicht erreichbar") from exc
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            response.close()
            connection.close()
            if not location:
                raise RemoteError("Weiterleitung ohne Ziel")
            url = urllib.parse.urljoin(url, location.strip())
            continue
        return Upstream(connection, response, url)
    raise RemoteError("Zu viele Weiterleitungen")


def fetch(url, limit, headers=None):
    """Ganze Antwort (Status 200) als ``(daten, content_type, endgueltige_url)``."""
    with open_url(url, headers=headers) as upstream:
        if upstream.status != 200:
            raise RemoteError(f"Server antwortet mit {upstream.status}")
        return upstream.read_limited(limit), upstream.content_type(), upstream.url


# ─── Streams ─────────────────────────────────────────────────────────

class StreamSlots:
    """Offene Streams je Konto und insgesamt zaehlen."""

    def __init__(self, per_user, total):
        self.per_user = per_user
        self.total = total
        self._counts = {}
        self._lock = threading.Lock()

    def acquire(self, user_id):
        key = str(user_id)
        with self._lock:
            if self._counts.get(key, 0) >= self.per_user or sum(self._counts.values()) >= self.total:
                return False
            self._counts[key] = self._counts.get(key, 0) + 1
            return True

    def release(self, user_id):
        key = str(user_id)
        with self._lock:
            count = self._counts.get(key, 0) - 1
            if count > 0:
                self._counts[key] = count
            else:
                self._counts.pop(key, None)

    def open(self, user_id=None):
        with self._lock:
            return self._counts.get(str(user_id), 0) if user_id is not None else sum(self._counts.values())


# Crossfade braucht zwei gleichzeitig, der Browser haelt beim Spulen kurz
# einen dritten offen.
SLOTS = StreamSlots(per_user=4, total=16)


def relay(upstream):
    """Bytes vom fremden Host, bis er endet oder der Browser auflegt.

    Aufraeumen (Verbindung schliessen, Platz freigeben) gehoert in
    ``response.call_on_close`` - ein Generator, der nie gestartet wurde, kaeme
    sonst nie in sein ``finally``."""
    while True:
        try:
            data = upstream.read1(CHUNK)
        except (OSError, http.client.HTTPException):
            return
        if not data:
            return
        yield data


# ─── Bilder ──────────────────────────────────────────────────────────

def cached_image(url, art_id):
    """Cover/Logo einmal holen und unter ``cache/screensaver/art/`` ablegen.

    Liefert ``(datei, mimetype)`` oder ``None``; angenommen wird nur, was nach
    JPEG, PNG, GIF oder WebP aussieht - egal, was der Server behauptet."""
    folder = screensaver._data_dir() / ART_FOLDER
    for mimetype, suffix in (("image/jpeg", ".jpg"), ("image/png", ".png"), ("image/gif", ".gif"), ("image/webp", ".webp")):
        cached = folder / f"{art_id}{suffix}"
        if cached.is_file():
            return cached, mimetype
    try:
        data, _type, _url = fetch(url, ART_LIMIT, headers={"Accept": "image/*"})
    except RemoteError:
        return None
    kind = screensaver_tags.image_type(data)
    if kind is None:
        return None
    target = folder / f"{art_id}{kind[1]}"
    try:
        screensaver._atomic_write(target, data)
    except OSError:
        return None
    screensaver_tags.prune(folder, ART_CACHE_LIMIT)
    return target, kind[0]


def art_id(url):
    return screensaver._digest(f"art:{url}")


__all__ = ["RemoteError", "check_url", "resolve", "open_url", "fetch", "SLOTS", "relay",
           "cached_image", "art_id", "StreamSlots"]
