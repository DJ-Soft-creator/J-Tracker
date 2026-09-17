"""Fingerprint der Quellen, aus denen app/static/tailwind.css gebaut wird.

Build-Skript und Test benutzen dieselbe Funktion: weicht der Fingerprint der
Arbeitskopie von dem in tailwind.css.sources ab, wurde eine Klasse geaendert,
ohne das CSS neu zu bauen - der Test schlaegt dann fehl, statt dass im Browser
still ein Style fehlt.
"""
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "scripts" / "css"
CSS_PATH = REPO_ROOT / "app" / "static" / "tailwind.css"
SOURCES_PATH = REPO_ROOT / "app" / "static" / "tailwind.css.sources"
TAILWIND_VERSION = "3.4.17"


def source_files():
    """Alle Dateien, die der Tailwind-Scan liest, plus die Build-Eingaben."""
    files = list((REPO_ROOT / "app" / "templates").rglob("*.html"))
    files += [p for p in (REPO_ROOT / "app" / "static").rglob("*.js")]
    files += [BUILD_DIR / "tailwind.config.js", BUILD_DIR / "tailwind.input.css"]
    return sorted(files)


def sources_hash():
    digest = hashlib.sha256()
    digest.update(TAILWIND_VERSION.encode("utf-8"))
    for path in source_files():
        digest.update(str(path.relative_to(REPO_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    print(sources_hash())
