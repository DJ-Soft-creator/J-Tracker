"""Datumsbereiche und Fälligkeit der Journal-Analyse-Jobs.

Der Datumsbereich ist **rollierend** (JA-03/Entscheidung des Product Owners):
„Vortag“ bezieht sich immer auf den Laufzeitpunkt, nie auf das Anlegen des Jobs.
Zukünftige Tage werden nie analysiert, der Bereich endet also spätestens heute.
So kann derselbe Job bei jedem Lauf denselben Tag treffen, ohne einen zweiten
Lauf zu erzwingen — die Doppellauf-Sperre greift über den Scan-Status.

Reine Funktionen ohne DB, Flask oder Datei-IO.
"""

import calendar
from datetime import date, datetime, time, timedelta


DATE_RANGE_MODES = (
    "previous_day", "last_days", "current_week", "last_week", "current_month", "last_month", "custom",
)
SCHEDULE_MODES = ("daily", "interval", "manual", "custom")
SCHEDULE_RULE_KINDS = ("weekly", "monthly")
# So weit schaut die Fälligkeit zurück: deckt jeden Monatstag sicher ab.
_LOOKBACK_DAYS = 62


def parse_day(value, field="Datum"):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"Ungültiges {field}") from exc


def resolve_range(mode, *, today, custom_start=None, custom_end=None, range_days=None):
    """Den rollierenden Zeitraum als inklusive ``(start, end)`` auflösen.

    ``last_days`` sind die letzten ``range_days`` Tage **einschließlich heute**:
    mit 2 also gestern und heute. Heute zählt mit, weil ein Tag oft über
    Mitternacht hinaus geht; ändert sich der heutige Eintrag noch, läuft er über
    die Geändert-Prüfung beim nächsten Lauf erneut.
    """
    today = parse_day(today, "Heute")
    if mode not in DATE_RANGE_MODES:
        raise ValueError("Ungültiger Zeitraum-Modus")
    if mode == "previous_day":
        start = end = today - timedelta(days=1)
    elif mode == "last_days":
        count = int(range_days or 0)
        if count < 1:
            raise ValueError("Für „Letzte Tage“ eine Anzahl angeben")
        start, end = today - timedelta(days=count - 1), today
    elif mode == "current_week":
        start, end = today - timedelta(days=today.weekday()), today
    elif mode == "last_week":
        this_monday = today - timedelta(days=today.weekday())
        start, end = this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    elif mode == "current_month":
        start, end = today.replace(day=1), today
    elif mode == "last_month":
        first_this_month = today.replace(day=1)
        end = first_this_month - timedelta(days=1)
        start = end.replace(day=1)
    else:  # custom
        start = parse_day(custom_start, "Startdatum")
        end = parse_day(custom_end, "Enddatum")
        if start is None or end is None:
            raise ValueError("Für einen eigenen Zeitraum Start und Ende angeben")
    if start > end:
        raise ValueError("Der Zeitraum ist verdreht")
    if end > today:
        end = today
    if start > end:
        # Der gesamte Bereich liegt in der Zukunft — nichts zu tun.
        return None, None
    return start, end


def days_in_range(start, end):
    """Alle Tage von ``start`` bis ``end`` inklusive, aufsteigend."""
    if start is None or end is None:
        return []
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _parse_time(value):
    parts = str(value or "").split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def matches_rule(rule, day):
    """Ob ``day`` ein Termin der benutzerdefinierten Regel ist.

    Monatstage jenseits des Monatsendes fallen auf den Monatsletzten — „31“
    heißt damit immer „letzter Tag des Monats“.
    """
    if not isinstance(rule, dict):
        return False
    kind = rule.get("kind")
    if kind == "weekly":
        return day.weekday() in (rule.get("weekdays") or [])
    if kind == "monthly":
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day in {min(int(value), last) for value in (rule.get("days") or [])}
    return False


def last_occurrence(rule, run_at, now):
    """Der jüngste Termin ``<= now`` (Datum + Uhrzeit) oder ``None``."""
    for offset in range(_LOOKBACK_DAYS):
        day = now.date() - timedelta(days=offset)
        if not matches_rule(rule, day):
            continue
        moment = datetime.combine(day, run_at, tzinfo=now.tzinfo)
        if moment <= now:
            return moment
    return None


def _comparable(value, reference):
    """``value`` an die Zeitzonen-Art von ``reference`` angleichen."""
    if (value.tzinfo is None) == (reference.tzinfo is None):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    return value.replace(tzinfo=None)


def is_due(job, now, last_run_at=None):
    """Ob ein Job zum Zeitpunkt ``now`` automatisch laufen soll.

    ``manual`` läuft nie von selbst. ``daily`` läuft ab der Uhrzeit einmal je
    Kalendertag; ``interval`` nach Ablauf der Stunden seit dem letzten Lauf.
    ``custom`` läuft einmal je Termin der Regel (Wochentage oder Monatstage),
    ab dessen Uhrzeit. Ein verpasster Termin wird einmal nachgeholt; ohne
    bisherigen Lauf zählt nur ein Termin von heute, damit ein neuer Job nicht
    sofort für den letzten Freitag losläuft.
    """
    now = now if isinstance(now, datetime) else parse_day(now)
    if not job.get("enabled", True):
        return False
    mode = job.get("schedule_mode") or "daily"
    if mode == "manual":
        return False
    if mode == "daily":
        run_at = _parse_time(job.get("schedule_time") or "06:00")
        if run_at is None or now.time() < run_at:
            return False
        if last_run_at is None:
            return True
        return last_run_at.date() != now.date()
    if mode == "interval":
        hours = job.get("schedule_interval_hours")
        if not hours:
            return False
        if last_run_at is None:
            return True
        return now - last_run_at >= timedelta(hours=int(hours))
    if mode == "custom":
        run_at = _parse_time(job.get("schedule_time") or "06:00")
        if run_at is None:
            return False
        occurrence = last_occurrence(job.get("schedule_rule"), run_at, now)
        if occurrence is None:
            return False
        if last_run_at is None:
            return occurrence.date() == now.date()
        return _comparable(last_run_at, occurrence) < occurrence
    return False


__all__ = [
    "DATE_RANGE_MODES", "SCHEDULE_MODES", "parse_day", "resolve_range",
    "days_in_range", "is_due", "matches_rule", "last_occurrence", "SCHEDULE_RULE_KINDS",
]