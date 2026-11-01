"""Quellen einer Journal-Analyse: Tagesjournal plus freigegebene Dateien.

Gelesen wird ausschließlich innerhalb des eigenen Nutzerordners oder des
Familienordners (JA-09). Absolute Pfade, ``..`` und Symlinks werden abgewiesen
— die Quellenangabe kommt aus der Job-Definition, nicht aus einer Anfrage, aber
die Grenze bleibt dieselbe.

Eine Zusatzquelle darf Datums-Platzhalter tragen (``{YYYY}``, ``{MM}``,
``{DD}``, ``{day}``); sie werden je analysiertem Tag ersetzt. So hängt an jedem
Tag die passende Tagesdatei, z. B.
``W-Tracker-data/{YYYY}/{MM}/{DD}/Weedtracker_{day}.md``.
"""

import hashlib
import re
from datetime import date, datetime
from pathlib import Path


JOURNAL_PREFIX = "Journal_"
FAMILY_DIRNAME = "family"
# `familie/` ist die deutschsprachige Eingabe, `family/` der Ordnername auf der
# Platte. Beide werden akzeptiert, zeigen aber auf denselben Bereich.
FAMILY_PREFIXES = ("familie/", "family/")

MAX_SOURCE_CHARS = 60000
MAX_TOTAL_CHARS = 200000

_PLACEHOLDER = re.compile(r"\{([A-Za-z]+)\}")
PLACEHOLDERS = {
    "YYYY": lambda day: day.strftime("%Y"),
    "MM": lambda day: day.strftime("%m"),
    "DD": lambda day: day.strftime("%d"),
    "day": lambda day: day.strftime("%Y-%m-%d"),
}


def _day_string(day):
    if hasattr(day, "strftime"):
        return day.strftime("%Y-%m-%d")
    text = str(day)
    datetime.strptime(text, "%Y-%m-%d")  # validiert nebenbei
    return text


def user_dir(user_id, data_root):
    return Path(data_root) / str(user_id)


def family_dir(data_root):
    return Path(data_root) / FAMILY_DIRNAME


def journal_path(user_id, day, data_root):
    day_string = _day_string(day)
    date_value = date.fromisoformat(day_string)
    return (user_dir(user_id, data_root) / date_value.strftime("%Y")
            / date_value.strftime("%m") / date_value.strftime("%d")
            / f"{JOURNAL_PREFIX}{day_string}.md")


def _read_text(path, limit):
    if not path.is_file() or path.is_symlink():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > limit:
        text = text[:limit] + "\n\n[... gekürzt ...]\n"
    return text


def _safe_relative(value):
    if not isinstance(value, str):
        raise ValueError("Ungültige Quelle")
    text = value.strip()
    if not text or text.startswith(("/", "\\")) or "\x00" in text:
        raise ValueError("Ungültige Quelle")
    parts = [part for part in text.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError("Ungültige Quelle")
    return parts


def is_per_day(value):
    """Ob die Quelle Datums-Platzhalter trägt, also je Tag eine andere Datei ist."""
    return bool(_PLACEHOLDER.search(str(value or "")))


def normalise_source(value):
    """Quellenangabe aus dem Formular prüfen und vereinheitlichen.

    Führende ``/`` werden entfernt: Der Pfad gilt ohnehin relativ zum eigenen
    Ordner, und ``/W-Tracker-data/...`` ist so gemeint. Unbekannte Platzhalter
    und ``..`` werden beim Speichern abgewiesen, nicht erst im Lauf.
    """
    text = str(value or "").strip().replace("\\", "/").lstrip("/")
    unknown = sorted({name for name in _PLACEHOLDER.findall(text) if name not in PLACEHOLDERS})
    if unknown:
        raise ValueError("Unbekannter Platzhalter: " + ", ".join("{" + name + "}" for name in unknown)
                         + " (erlaubt: {YYYY}, {MM}, {DD}, {day})")
    _safe_relative(text)
    return text


def resolve_source(value, day):
    """Datums-Platzhalter für ``day`` einsetzen; unbekannte bleiben stehen."""
    date_value = date.fromisoformat(_day_string(day))

    def replace(match):
        render = PLACEHOLDERS.get(match.group(1))
        return render(date_value) if render else match.group(0)

    return _PLACEHOLDER.sub(replace, str(value))


def read_extra_source(user_id, value, data_root):
    """Eine freigegebene Datei lesen. Rückgabe ``(name, text)`` oder ``None``."""
    parts = _safe_relative(value)
    is_family = False
    if len(parts) >= 2 and f"{parts[0]}/" in FAMILY_PREFIXES:
        is_family = True
        parts = parts[1:]
    base = family_dir(data_root) if is_family else user_dir(user_id, data_root)
    candidate = base.joinpath(*parts)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(base.resolve())
    except (ValueError, OSError):
        raise ValueError("Quelle liegt außerhalb des erlaubten Bereichs")
    text = _read_text(candidate, MAX_SOURCE_CHARS)
    if text is None:
        return None
    label = ("familie/" if is_family else "") + "/".join(parts)
    return label, text


def collect_sources(user_id, day, extra_sources, data_root):
    """Tagesjournal und Zusatzquellen einsammeln.

    Fehlende Quellen sind kein Fehler: ein Tag ohne Journal ergibt einen leeren
    Journaltext. Rückgabe enthält immer ``journal`` und ``extras``.
    """
    path = journal_path(user_id, day, data_root)
    journal = _read_text(path, MAX_SOURCE_CHARS) or ""
    extras = []
    for value in extra_sources or []:
        result = read_extra_source(user_id, resolve_source(value, day), data_root)
        if result is not None:
            extras.append({"source": result[0], "text": result[1], "per_day": is_per_day(value)})
    return {
        "day": _day_string(day),
        "journal_path": path.relative_to(Path(data_root)).as_posix(),
        "journal": journal,
        "extras": extras,
    }


# Technische Bloecke, die nicht vom Nutzer stammen: der Hashtag-Footer (wird
# bei jeder Tag-Aenderung neu geschrieben) und die Bloecke, die die Analyse
# selbst ins Journal schreibt. Sie duerfen den Tag nicht als „geaendert“
# markieren — sonst loeste die Ausgabe ins Journal beim naechsten Lauf eine
# erneute Analyse aus, endlos.
_TECHNICAL_BLOCKS = re.compile(
    r"<!-- jt:hashtag-index:start.*?<!-- jt:hashtag-index:end -->"
    r"|<!-- jt:analysis(?:-run)?:start.*?<!-- jt:analysis(?:-run)?:end -->",
    re.DOTALL,
)
# Kennzeichnet das Format von ``content_hash``. Aeltere Laeufe tragen einen
# reinen SHA-256-Hex (64 Zeichen) ueber alle Quellen; den kann man nicht mit
# dem neuen vergleichen. Die Spalte ist ``char(64)``, deshalb wird gekuerzt.
CONTENT_HASH_PREFIX = "c2:"


def user_content(text):
    """Nur das, was der Nutzer geschrieben hat: ohne technische Bloecke, getrimmt."""
    return _TECHNICAL_BLOCKS.sub("", text or "").strip()


def is_empty(sources):
    """Ob der Tag nichts zum Analysieren hat (kein Nutzertext, keine Zusatzquelle)."""
    if user_content(sources.get("journal")):
        return False
    return not any(user_content(extra.get("text")) for extra in sources.get("extras") or [])


def content_hash(sources):
    """Hash über den Nutzertext des Tagesjournals (Geändert-Prüfung).

    Feste Zusatzquellen zählen bewusst nicht: Ein Familiendokument ändert sich
    laufend und gilt für jeden Tag des Zeitraums gleich. Zählte es mit, würde
    jede Änderung daran alle schon analysierten Tage erneut durch das Modell
    schicken. Tagesquellen (mit Datums-Platzhalter) gehören dagegen genau zu
    diesem Tag — ändert sich die Tagesauswertung, läuft der Tag neu. Ohne
    Tagesquellen bleibt der Hash identisch zum bisherigen.
    """
    text = user_content(sources.get("journal"))
    for extra in sources.get("extras") or []:
        if extra.get("per_day"):
            text += f"\n\x00{extra.get('source')}\n{user_content(extra.get('text'))}"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return CONTENT_HASH_PREFIX + digest[:64 - len(CONTENT_HASH_PREFIX)]


def content_changed(stored_hash, sources):
    """Ob der Tag seit dem gespeicherten Lauf geändert wurde.

    Ein Hash im alten Format (oder keiner) gilt als unverändert: Sonst liefe
    nach dem Update jeder bereits analysierte Tag noch einmal durchs Modell.
    """
    stored = str(stored_hash or "").strip()
    if not stored.startswith(CONTENT_HASH_PREFIX):
        return False
    return stored != content_hash(sources)


def render_sources(sources, total_limit=MAX_TOTAL_CHARS):
    """Quellen als Textblock für den Prompt, mit harten Obergrenzen."""
    chunks = []
    journal = sources.get("journal") or ""
    if journal:
        chunks.append(f"### Journal {sources.get('day')}\n{journal}")
    for extra in sources.get("extras") or []:
        chunks.append(f"### Quelle: {extra.get('source')}\n{extra.get('text')}")
    text = "\n\n".join(chunks)
    if len(text) > total_limit:
        text = text[:total_limit] + "\n\n[... gekürzt ...]\n"
    return text


__all__ = [
    "FAMILY_DIRNAME", "MAX_SOURCE_CHARS", "MAX_TOTAL_CHARS", "user_dir",
    "family_dir", "journal_path", "read_extra_source", "collect_sources",
    "render_sources", "content_hash", "content_changed", "is_empty",
    "user_content", "CONTENT_HASH_PREFIX", "PLACEHOLDERS", "is_per_day",
    "normalise_source", "resolve_source",
]