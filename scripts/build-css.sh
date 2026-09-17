#!/usr/bin/env bash
# Baut app/static/tailwind.css aus den Klassen in Templates und Frontend-Skripten.
#
# Warum ueberhaupt ein Build: frueher lud die App cdn.tailwindcss.com - 407 kB
# JavaScript, das bei JEDEM Start im Browser erst das CSS erzeugt hat. Das
# fertige Stylesheet ist rund 38 kB (7 kB gzip) und kostet zur Laufzeit nichts.
#
# Nach jeder Aenderung an Klassen in app/templates/ oder app/static/*.js neu
# laufen lassen und die erzeugten Dateien mitcommitten. tests/test_pwa.py
# vergleicht den Quellen-Fingerprint und wird sonst rot.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$REPO_ROOT/scripts/css"
TAILWIND_VERSION="$(sed -n 's/^TAILWIND_VERSION = "\(.*\)"$/\1/p' "$BUILD_DIR/sources_hash.py")"
OUT="$REPO_ROOT/app/static/tailwind.css"

command -v npx >/dev/null || { echo "npx fehlt - Node.js auf dem Host installieren." >&2; exit 1; }

echo "Tailwind $TAILWIND_VERSION baut $OUT ..."
npx --yes "tailwindcss@$TAILWIND_VERSION" \
  --config "$BUILD_DIR/tailwind.config.js" \
  --input "$BUILD_DIR/tailwind.input.css" \
  --output "$OUT" \
  --minify

python3 "$BUILD_DIR/sources_hash.py" > "$REPO_ROOT/app/static/tailwind.css.sources"
echo "Fertig: $(wc -c < "$OUT") Bytes, Fingerprint aktualisiert."
echo "Nicht vergessen: v= fuer tailwind.css in beiden Templates erhoehen."
