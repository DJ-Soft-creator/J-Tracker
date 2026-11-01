"""Feste Erkennung von Erinnerungen im Journaltext — ohne KI.

"Erinnere mich morgen 10 Uhr daran, bei X anzurufen" oder "Erinnerung:
nächste Woche Mittwoch 13 Uhr Y" sind eindeutig genug, um sie ohne Modell zu
verstehen. Die KI-Analyse blieb hier unzuverlässig (lokales Modell, Wochentage
selbst ausrechnen, Erinnerungen als „schon bekannt" verworfen) und lief erst
am Folgetag — „heute 15 Uhr" war dann vorbei. Dieses Modul liefert deshalb
den sicheren Teil: Sätze mit einem Erinnerungswort *und* einem auflösbaren
Termin. Alles Weichere („Zahnarzt am Freitag" ohne Erinnerungswort) bleibt
der KI-Analyse.

Relative Angaben gelten vom Journaltag aus, nicht vom Lesezeitpunkt. Die
Zeitpunkte sind naiv (Server-Lokalzeit); die Zeitzone setzt der Aufrufer.
Reine Funktionen ohne Datei- oder DB-Zugriff.
"""

import re
from datetime import date, datetime, time, timedelta


DEFAULT_TIME = time(9, 0)
MAX_TEXT = 200

WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")
WEEKDAY_LABELS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
WEEKDAY_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "oktober": 10, "okt": 10,
    "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}
_NUMBERS = {
    "ein": 1, "einem": 1, "einen": 1, "einer": 1, "eine": 1, "zwei": 2, "drei": 3,
    "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
    "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12, "vierzehn": 14,
}
# Tageszeit ohne Uhrzeit — und bei "um 8 abends" der Hinweis auf den Nachmittag.
_DAYPARTS = {"früh": 8, "frueh": 8, "morgen": 8, "morgens": 8, "vormittag": 10,
             "vormittags": 10, "mittag": 12, "mittags": 12, "nachmittag": 15,
             "nachmittags": 15, "abend": 18, "abends": 18}
_AFTERNOON = {"nachmittag", "nachmittags", "abend", "abends"}

_WD = r"(" + "|".join(WEEKDAYS) + r")\b"
_NEXT = r"(?:n(?:ä|ae)chste[nrs]?|kommende[nrs]?)"
_MONTH = r"(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b\.?"
_NUMBER = r"(\d{1,3}|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r")"
_LEAD = r"(?:(?:am|ab|bis|zum|den|für|fuer)\s+)?"
_TIME_LEAD = r"(?:(?:um|ab|gegen|bis|so\s+um)\s+)?"

# Imperativ, Infinitiv und Substantiv — nicht "erinnert"/"erinnerte": Wer
# schreibt, dass er sich an etwas erinnert hat, will keine Meldung.
TRIGGER = re.compile(r"\b(?:erinnere|erinnern|erinnerungen|erinnerung|nicht\s+vergessen|reminder)\b",
                     re.IGNORECASE)
_FILLER = re.compile(
    r"\b(?:bitte\s+)?(?:erinnere\s+(?:mich|uns)|erinnern|erinnerungen|erinnerung|"
    r"nicht\s+vergessen|reminder)\b(?:\s+(?:bitte|daran|dran))*\s*:?",
    re.IGNORECASE)
_LEADING_WORDS = re.compile(r"^(?:(?:bitte|daran|dran|dass|an|um|am|zu|und|noch|mich)\b[\s,:]*)+",
                            re.IGNORECASE)
_TRAILING_WORDS = re.compile(r"(?:[\s,]+(?:bitte|daran|dran|mich))+$", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^\s*(?:#{1,6}\s+|>\s*)?(?:[-*+]\s+|\d+[.)]\s+)?(?:\[[ ]\]\s+)?")
_DONE_TASK = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)\[[xX]\]")
_HASHTAG = re.compile(r"(?<![\w&])#[\w-]+")
# Satzende: ! ? ; oder ein Punkt hinter einem Wort — nicht hinter einer Zahl,
# sonst zerrisse "am 24.10. Mutter anrufen" mitten im Datum.
_SENTENCE_END = re.compile(r"(?<=[!?;])\s+|(?<=[A-Za-zÄÖÜäöüß]{2}\.)\s+")


def _week_start(day):
    return day - timedelta(days=day.weekday())


def _next_weekday(day, weekday):
    """Nächstes Vorkommen nach ``day`` — derselbe Wochentag meint nächste Woche."""
    return day + timedelta(days=(weekday - day.weekday() - 1) % 7 + 1)


def _add_months(day, months):
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    for candidate in range(day.day, 27, -1):
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    return date(year, month, min(day.day, 28))


def _number(value):
    value = value.lower()
    return int(value) if value.isdigit() else _NUMBERS.get(value)


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _year_for(day_value, month, base):
    """Datum ohne Jahr: das nächste Vorkommen ab dem Journaltag."""
    candidate = _safe_date(base.year, month, day_value)
    if candidate is not None and candidate < base:
        candidate = _safe_date(base.year + 1, month, day_value)
    return candidate


def _full_year(value):
    year = int(value)
    return year + 2000 if year < 100 else year


# Jede Regel: (Muster, Funktion(match, journaltag) -> (datum, uhrzeit|None) | None).
# Reihenfolge = Vorrang; die spezifischere Angabe gewinnt.
def _date_rules(relative):
    rules = []

    def iso(match, base):
        found = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        clock = None
        if match.group(4):
            clock = _clock(int(match.group(4)), int(match.group(5)))
        return (found, clock) if found else None

    rules.append((r"\b(\d{4})-(\d{2})-(\d{2})(?:[t\s](\d{1,2}):(\d{2}))?\b", iso))

    def numeric(match, base):
        day_value, month = int(match.group(1)), int(match.group(2))
        if match.group(3):
            found = _safe_date(_full_year(match.group(3)), month, day_value)
        else:
            found = _year_for(day_value, month, base)
        return (found, None) if found else None

    rules.append((_LEAD + r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?(?!\d)(?!\s*uhr)", numeric))

    def named(match, base):
        day_value, month = int(match.group(1)), _MONTHS[match.group(2).lower()]
        if match.group(3):
            found = _safe_date(int(match.group(3)), month, day_value)
        else:
            found = _year_for(day_value, month, base)
        return (found, None) if found else None

    rules.append((_LEAD + r"\b(\d{1,2})\.?\s*" + _MONTH + r"(?:\s+(\d{4}))?", named))
    if relative:
        rules.extend(_relative_rules())
    return [(re.compile(pattern, re.IGNORECASE), handler) for pattern, handler in rules]


def _relative_rules():
    rules = []

    def week_with_day(match, base):
        later = 2 if match.group(1) or match.group(4) else 1
        weekday = WEEKDAYS.index((match.group(2) or match.group(3)).lower())
        return _week_start(base) + timedelta(days=7 * later + weekday), None

    rules.append((r"(?:(?:am|den)\s+)?(?:(?:(über|ueber)?" + _NEXT + r"\s+woche\s+(?:am\s+)?" + _WD + r")|"
                  r"(?:" + _WD + r"\s+(?:der\s+|in\s+der\s+)?(über|ueber)?" + _NEXT + r"\s+woche\b))",
                  week_with_day))

    def week_only(match, base):
        later = 2 if match.group(1) else 1
        return _week_start(base) + timedelta(days=7 * later), None

    rules.append((r"(?:(?:in\s+der|die)\s+)?\b(über|ueber)?" + _NEXT + r"\s+woche\b", week_only))

    def in_units(match, base):
        count = _number(match.group(1))
        if not count:
            return None
        unit = match.group(2).lower()
        if unit.startswith("tag"):
            return base + timedelta(days=count), None
        if unit.startswith("woche"):
            return base + timedelta(weeks=count), None
        return _add_months(base, count), None

    rules.append((r"\bin\s+" + _NUMBER + r"\s+(tag(?:en)?|woche(?:n)?|monat(?:en)?)\b", in_units))
    rules.append((r"\b(?:über|ueber)morgen\b", lambda match, base: (base + timedelta(days=2), None)))
    # "morgen" = der Folgetag; "am Morgen"/"heute Morgen" ist die Tageszeit.
    rules.append((r"(?<!heute )(?<!am )(?<!jeden )(?<!guten )(?<!den )(?<!diesen )\bmorgen\b(?!s)",
                  lambda match, base: (base + timedelta(days=1), None)))
    rules.append((r"\bheute\b", lambda match, base: (base, None)))

    def weekend(match, base):
        return _next_weekday(base, 5), None

    rules.append((r"\b(?:am|zum|übers|uebers|dieses|nächstes|naechstes|kommendes)\s+wochenende\b", weekend))

    def weekday(match, base):
        return _next_weekday(base, WEEKDAYS.index(match.group(1).lower())), None

    rules.append((r"(?:(?:am|diesen|" + _NEXT + r")\s+)?\b" + _WD, weekday))
    return rules


_RULES = {True: _date_rules(True), False: _date_rules(False)}


def _clock(hour, minute=0):
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return time(hour, minute)
    return None


_TIME_RULES = [
    (re.compile(_TIME_LEAD + r"\b(\d{1,2})[:.](\d{2})\s*(?:uhr|h)\b", re.IGNORECASE),
     lambda m: _clock(int(m.group(1)), int(m.group(2)))),
    (re.compile(_TIME_LEAD + r"\b(\d{1,2}):(\d{2})\b(?:\s*uhr\b)?", re.IGNORECASE),
     lambda m: _clock(int(m.group(1)), int(m.group(2)))),
    (re.compile(_TIME_LEAD + r"\b(\d{1,2})\s*uhr\b(?:\s+(\d{1,2})\b(?!\s*(?:min|tag|woche)))?", re.IGNORECASE),
     lambda m: _clock(int(m.group(1)), int(m.group(2) or 0))),
    (re.compile(_TIME_LEAD + r"\bhalb\s+(\d{1,2})\b", re.IGNORECASE),
     lambda m: _clock((int(m.group(1)) - 1) % 24, 30)),
    (re.compile(r"\bum\s+(\d{1,2})\b(?![.:]\d)(?!\s*(?:tage|wochen|monate|minuten|stunden|prozent|%|euro|€))",
                re.IGNORECASE),
     lambda m: _clock(int(m.group(1)))),
]
_DAYPART = re.compile(
    r"(?:(?:am|heute|morgen|gegen)\s+)?\b(früh|frueh|morgens|vormittags?|mittags?|nachmittags?|abends?)\b"
    r"|(?<=heute )\bmorgen\b|(?<=am )\bmorgen\b",
    re.IGNORECASE)


def _mask(text, spans):
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    return "".join(chars)


def parse_moment(text, day, *, relative=True, default_time=DEFAULT_TIME):
    """Termin in ``text``, gerechnet vom Journaltag ``day``.

    Rückgabe ``(zeitpunkt, spans, uhrzeit_explizit)`` oder ``None``, wenn kein
    Datum und keine Uhrzeit erkennbar ist. Eine Uhrzeit ohne Datum gilt für den
    Journaltag selbst; ein Datum ohne Uhrzeit bekommt ``default_time``.
    ``relative=False`` erkennt nur absolute Daten (für Texte unbekannten Alters).
    """
    source = str(text or "")
    if not source.strip():
        return None
    spans = []
    found_day = None
    clock = None
    for pattern, handler in _RULES[bool(relative)]:
        for match in pattern.finditer(source):
            resolved = handler(match, day)
            if resolved and resolved[0] is not None:
                found_day, clock = resolved
                spans.append(match.span())
                break
        if found_day is not None:
            break
    masked = _mask(source, spans)
    if clock is None:
        for pattern, handler in _TIME_RULES:
            match = pattern.search(masked)
            if match and handler(match) is not None:
                clock = handler(match)
                spans.append(match.span())
                masked = _mask(masked, [match.span()])
                break
    part_match = _DAYPART.search(masked)
    if part_match:
        word = (part_match.group(1) or "morgen").lower()
        if clock is None:
            clock = time(_DAYPARTS[word], 0)
        elif word in _AFTERNOON and clock.hour < 12:
            clock = time(clock.hour + 12, clock.minute)
        spans.append(part_match.span())
    if found_day is None and clock is None:
        return None
    if found_day is None and not relative:
        return None
    explicit = clock is not None
    moment = datetime.combine(found_day or day, clock or default_time)
    return moment, spans, explicit


def _sentences(text):
    for line in str(text or "").splitlines():
        if _DONE_TASK.match(line):
            continue
        for sentence in _SENTENCE_END.split(line):
            if sentence.strip():
                yield sentence


def clean_text(sentence, spans=()):
    """Den Auftrag aus dem Satz schälen: ohne Termin, Erinnerungswort und Markdown."""
    raw = _mask(sentence, spans)
    raw = _LIST_MARKER.sub("", raw)
    raw = _HASHTAG.sub(" ", raw)
    raw = re.sub(r"\*\*|__|`", "", raw)
    raw = _FILLER.sub(" ", raw)
    # Ein Wochentag neben einem Datum ("Sonntag, 25. Oktober") ist nur Beiwerk.
    raw = re.sub(r"\b(?:am\s+)?" + _WD + r",?", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s+", " ", raw).strip(" ,;:.-–—!?")
    raw = _TRAILING_WORDS.sub("", _LEADING_WORDS.sub("", raw)).strip(" ,;:.-–—!?")
    raw = re.sub(r"\s+([,.!?])", r"\1", raw)
    if len(raw) < 3:
        raw = re.sub(r"\s+", " ", _LIST_MARKER.sub("", sentence)).strip()
    return (raw[:1].upper() + raw[1:])[:MAX_TEXT]


def find_reminders(text, day):
    """Ausdrückliche Erinnerungen eines Journaltags: ``[{"text", "at"}]``.

    Ein Satz zählt nur mit Erinnerungswort und Termin. "Heute" ohne Uhrzeit
    ist kein Termin — eine Meldung irgendwann am selben Tag hilft niemandem.
    Erledigte Aufgaben (``- [x]``) fallen weg. Doppelte Sätze zählen einmal.
    """
    events = []
    seen = set()
    for sentence in _sentences(text):
        if not TRIGGER.search(sentence):
            continue
        parsed = parse_moment(sentence, day)
        if parsed is None:
            continue
        moment, spans, explicit = parsed
        if moment.date() == day and not explicit:
            continue
        if moment.date() < day:
            continue
        body = clean_text(sentence, spans)
        key = (moment, normalize(body))
        if key in seen:
            continue
        seen.add(key)
        events.append({"text": body, "at": moment})
    return events


# ─── Vergleich zweier Erinnerungstexte ──────────────────────────────

def normalize(text):
    # Bindestrich und Doppelpunkt bleiben: "2026-09-22" ist ein Wort, keine drei.
    return " ".join(re.sub(r"[^\w\s:-]", " ", str(text or "").lower()).split())


def _words(text):
    return {word for word in normalize(text).split() if len(word) >= 4}


def similar(first, second):
    """Dieselbe Erinnerung, nur anders wiederholt? (gleich, enthalten, Wortüberlappung)"""
    a, b = normalize(first), normalize(second)
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)):
        return True
    words_a, words_b = _words(a), _words(b)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / len(words_a | words_b) >= 0.6


def related(first, second):
    """Schwächer: teilen die Texte ein Wort (Wortanfang, ab vier Buchstaben)?

    Für KI-Erinnerungen, die zur selben Minute wie eine feste Erinnerung
    liegen — "X anrufen" und "Bei X anzurufen" sind dieselbe Sache.
    """
    stems_a = {word[:5] for word in _words(first)}
    stems_b = {word[:5] for word in _words(second)}
    return bool(stems_a & stems_b)


# ─── Kalender für den Prompt ────────────────────────────────────────

def calendar_block(day, days=14):
    """Wochentage zum Nachschlagen — ein lokales Modell verrechnet sich sonst."""
    week = _week_start(day)
    following = ", ".join(
        f"{WEEKDAY_SHORT[(day + timedelta(days=offset)).weekday()]} "
        f"{(day + timedelta(days=offset)).isoformat()}"
        for offset in range(1, days + 1))
    return (
        "## Kalender\n"
        f"Journaltag: {WEEKDAY_LABELS[day.weekday()]}, {day.isoformat()} "
        f"(KW {day.isocalendar()[1]})\n"
        f"morgen = {WEEKDAY_LABELS[(day + timedelta(days=1)).weekday()]} "
        f"{(day + timedelta(days=1)).isoformat()}, übermorgen = "
        f"{WEEKDAY_LABELS[(day + timedelta(days=2)).weekday()]} {(day + timedelta(days=2)).isoformat()}\n"
        f"Diese Woche: Montag {week.isoformat()} bis Sonntag {(week + timedelta(days=6)).isoformat()}\n"
        f"Nächste Woche: Montag {(week + timedelta(days=7)).isoformat()} bis Sonntag "
        f"{(week + timedelta(days=13)).isoformat()}\n"
        f"Folgetage: {following}"
    )


__all__ = [
    "DEFAULT_TIME", "TRIGGER", "parse_moment", "clean_text", "find_reminders",
    "normalize", "similar", "related", "calendar_block",
]
