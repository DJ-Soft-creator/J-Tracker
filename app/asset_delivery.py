"""Auslieferung der statischen Frontend-Dateien: gzip und Cache-Header.

AI maintenance contract:
Hier steht ausschliesslich, WIE eine fertige Antwort das Haus verlaesst -
keine Routen, keine Inhalte. Aus main.py ausgelagert, damit die Datei unter
der 1000-Zeilen-Grenze bleibt.
"""

import gzip

from flask import request


# Textantworten werden komprimiert und versionierte Assets duerfen dauerhaft im
# Browsercache liegen. Beides zielt auf den Kaltstart der PWA: das Frontend sind
# rund 800 kB Text, die sonst bei jedem ersten Start ungepackt uebers Mobilfunk-
# netz gehen. Binaerdaten (Fotos, Sprachnachrichten) bleiben unangetastet.
_COMPRESSIBLE_MIMETYPES = frozenset({
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "image/svg+xml",
    "text/plain",
    "text/markdown",
})
_COMPRESS_MIN_BYTES = 1024
_STATIC_IMMUTABLE_MAX_AGE = 31536000


def register(app, is_dev):
    """Haengt den Auslieferungs-Handler an die App."""

    @app.after_request
    def _set_asset_delivery_headers(response):
        """Cache-Header fuer versionierte Assets setzen und Text gzip-komprimieren."""
        # Der ?v=-Parameter ist der Cache-Buster: unter dieser URL aendert sich die
        # Datei nie, also darf der Browser sie behalten, statt bei jedem Start neu
        # zu validieren. In DEV bleibt es bei Revalidierung, damit ein vergessener
        # v-Bump auf dem Testgeraet keinen eingefrorenen Stand hinterlaesst.
        if (
            not is_dev
            and request.path.startswith("/static/")
            and request.args.get("v")
            and response.status_code == 200
        ):
            response.headers["Cache-Control"] = f"public, max-age={_STATIC_IMMUTABLE_MAX_AGE}, immutable"

        if (
            response.status_code != 200
            or request.method not in ("GET", "HEAD")
            or response.headers.get("Content-Encoding")
            or response.mimetype not in _COMPRESSIBLE_MIMETYPES
            or "gzip" not in request.headers.get("Accept-Encoding", "")
        ):
            return response
        response.headers.add("Vary", "Accept-Encoding")
        # Echte Generator-Antworten duerfen nicht eingesammelt werden; eine
        # Datei aus send_file dagegen schon - sie zaehlt nur deshalb als
        # "streamed", weil sie als Datei-Wrapper am Puffer vorbei ginge.
        if response.is_streamed and not response.direct_passthrough:
            return response
        response.direct_passthrough = False
        payload = response.get_data()
        if len(payload) < _COMPRESS_MIN_BYTES:
            return response
        packed = gzip.compress(payload, 6)
        if len(packed) >= len(payload):
            return response
        response.set_data(packed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(packed))
        etag = response.headers.get("ETag")
        if etag:
            # Gepackt und ungepackt sind verschiedene Repraesentationen.
            response.headers["ETag"] = etag[:-1] + '-gzip"' if etag.endswith('"') else etag + "-gzip"
        return response

    return _set_asset_delivery_headers
