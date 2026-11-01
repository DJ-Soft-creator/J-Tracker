"""Ortszeit-Zusatzzeile fuer Journal-Eintraege, die auf Reisen entstehen.

Tagesdatei und Zeitstempel bleiben in Serverzeit, damit Sortierung und "Heute"
unveraendert gelten. Schickt die PWA eine abweichende Zeitzone mit, erklaert
eine letzte Zeile im Block, wann der Eintrag vor Ort geschrieben wurde.
"""

from zoneinfo import ZoneInfo

_WEEKDAYS_DE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_ENTRY_CLOSING = "\n\n___\n\n"


def note(now, client_timezone):
    """Describe ``now`` in the writer's own zone when its offset differs.

    Gleicher Offset (z. B. Paris statt Berlin) braucht keine Erklaerung. Keine
    Sekunden und kein ``_``: sonst laesen Zeit- und Kursivregeln der Parser die
    Zeile als Zeitstempel bzw. Hervorhebung.
    """
    if not isinstance(client_timezone, str) or not 0 < len(client_timezone) <= 64:
        return ""
    try:
        local = now.astimezone(ZoneInfo(client_timezone))
    except (ValueError, KeyError, OSError):
        return ""
    minutes = int((local.utcoffset() - now.utcoffset()).total_seconds() // 60)
    if not minutes:
        return ""
    sign = "+" if minutes > 0 else "−"
    hours, rest = divmod(abs(minutes), 60)
    offset = f"{sign}{hours}:{rest:02d} h" if rest else f"{sign}{hours} h"
    place = client_timezone.rsplit("/", 1)[-1].replace("_", " ")
    return (f"*Ortszeit: {_WEEKDAYS_DE[local.weekday()]} {local.strftime('%d.%m. %H:%M')}"
            f" · {place} ({offset})*")


def append(entry, line):
    """Put ``line`` last inside the entry's closing ``___`` separator."""
    if not line or not entry.endswith(_ENTRY_CLOSING):
        return entry
    return entry[:-len(_ENTRY_CLOSING)] + "\n" + line + _ENTRY_CLOSING
