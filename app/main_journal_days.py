"""Journal day availability: calendar month view and the previous journal day.

AI maintenance contract:
Own the read-only "which days have a journal" questions. Both endpoints only
inspect directory names below the user root and never create or write files.
Entry content is still read through ``_get_today_entries`` in
``main_journal_routes.py``; this module answers *which* day, not *what is in
it*. Imported after main.py and after main_journal_routes.py.
"""

import sys

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before route modules are imported")
globals().update({name: getattr(_core, name) for name in dir(_core) if not name.startswith("__")})
_DYNAMIC_NAMES = ('DATA_DIR', '_get_today_entries')


def _sync_runtime():
    for name in _DYNAMIC_NAMES:
        if hasattr(_core, name):
            globals()[name] = getattr(_core, name)


@app.before_request
def _sync_journal_day_runtime():
    _sync_runtime()


def _has_journal_file(day_dir):
    """True when the day directory holds at least one real journal file."""
    try:
        return any(not path.is_symlink() and path.is_file()
                   and path.name.startswith("Journal_") and path.suffix == ".md"
                   for path in day_dir.iterdir())
    except OSError:
        return False


def _previous_journal_date(user_id, before):
    """Nearest journal day strictly before ``before``, or None.

    Walks the YYYY/MM/DD tree downwards instead of stepping day by day: gaps
    between journal days are the norm, and a per-day probe would need an
    arbitrary cut-off to give up. Symlinked directories are skipped for the
    same reason as in the calendar endpoint - they can point outside the user
    root.
    """
    user_root = (DATA_DIR / user_id).resolve()
    if not user_root.is_dir():
        return None

    def _numeric_dirs(parent, width):
        try:
            entries = [path for path in parent.iterdir()
                       if path.is_dir() and not path.is_symlink() and re.fullmatch(r"\d{%d}" % width, path.name)]
        except OSError:
            return []
        return sorted(entries, key=lambda path: path.name, reverse=True)

    for year_dir in _numeric_dirs(user_root, 4):
        if year_dir.name > before[:4]:
            continue
        for month_dir in _numeric_dirs(year_dir, 2):
            if f"{year_dir.name}-{month_dir.name}" > before[:7]:
                continue
            for day_dir in _numeric_dirs(month_dir, 2):
                candidate = f"{year_dir.name}-{month_dir.name}-{day_dir.name}"
                if candidate >= before:
                    continue
                try:
                    datetime.strptime(candidate, "%Y-%m-%d")
                except ValueError:
                    continue
                if _has_journal_file(day_dir):
                    return candidate
    return None


@app.route("/api/dashboard/previous")
@require_auth
def api_dashboard_previous():
    """Nearest earlier journal day with its entries, so Heute can scroll on.

    Returns ``date: null`` once nothing older exists; the button then
    disappears instead of loading empty days.
    """
    before = (request.args.get("before") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", before):
        return jsonify({"error": "Ungültiges Journaldatum"}), 400
    previous = _previous_journal_date(_current_user()["id"], before)
    if previous is None:
        return jsonify({"date": None, "entries": []})
    return jsonify({"date": previous, "entries": _get_today_entries(journal_date=previous)})


@app.route("/api/journals/dates")
@require_auth
def api_journal_dates():
    """Return content-free journal availability for one calendar month."""
    month = (request.args.get("month") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return jsonify({"error": "Ungültiger Kalendermonat"}), 400
    try:
        month_date = datetime.strptime(month, "%Y-%m")
    except ValueError:
        return jsonify({"error": "Ungültiger Kalendermonat"}), 400

    user_root = (DATA_DIR / _current_user()["id"]).resolve()
    year_dir = user_root / month_date.strftime("%Y")
    month_dir = year_dir / month_date.strftime("%m")
    dates = []
    if year_dir.is_symlink() or month_dir.is_symlink():
        return jsonify({"month": month, "dates": dates})
    if month_dir.is_dir():
        try:
            for day_dir in month_dir.iterdir():
                if day_dir.is_symlink() or not day_dir.is_dir() or not re.fullmatch(r"\d{2}", day_dir.name):
                    continue
                try:
                    day = datetime.strptime(f"{month}-{day_dir.name}", "%Y-%m-%d").date()
                except ValueError:
                    continue
                if any(not path.is_symlink() and path.is_file()
                       and path.name.startswith("Journal_") and path.suffix == ".md"
                       for path in day_dir.iterdir()):
                    dates.append(day.isoformat())
        except OSError as error:
            logger.warning("Journal calendar unavailable for %s: %s", month, error)
            return jsonify({"error": "Journaltage konnten nicht gelesen werden"}), 500
    return jsonify({"month": month, "dates": sorted(dates)})


__all__ = ['api_dashboard_previous', 'api_journal_dates', '_previous_journal_date']
