#!/usr/bin/env python3
"""Zeitplan des Jobs „Hashtags nachtragen“.

Sechster Compose-Dienst nach dem Muster von ``scheduler.py`` und
``journal-analysis-scheduler.py``: eigener Prozess, unabhaengig vom
Webinterface, ``--loop`` im Takt. Alle ``TAGGING_POLL_SECONDS`` sieht er nach,
ob der Job eines Nutzers faellig ist. Zeitplan, Zeitraum, Modus und Meldungen
pflegt der Nutzer in der Journal-Analyse-Verwaltung; sie stehen neben der
Verbindung in ``.config/historical-tagging.json`` (``tagging_job.py``). Die
Faelligkeit folgt denselben Regeln wie die Analyse-Jobs (taeglich, Takt,
Wochen-/Monatstage, manuell).

Ein Lauf geht ueber ``background_runs`` mit ``inline=True``: er landet damit im
selben Protokoll wie ein von Hand gestarteter, samt Tagesliste und Fehlern, und
sperrt einen gleichzeitigen Handlauf aus. Scheitert er (Endpunkt schlaeft),
versucht der Takt es nach ``RETRY_MINUTES`` erneut, hoechstens
``MAX_ATTEMPTS`` Mal; erst der letzte Fehlversuch erzeugt eine Fehlermeldung.

Die Verbindung bleibt lokal, weil schon das Speichern nur Loopback- oder
private Adressen zulaesst (D-015).
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"
sys.path.insert(0, str(APP_DIR if APP_DIR.is_dir() else ROOT_DIR))

import background_runs  # noqa: E402
import historical_tagging  # noqa: E402
import main  # noqa: E402
import semantic_index  # noqa: E402
import tagging_job  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TZ_NAME = os.environ.get("TZ", "Europe/Berlin")
# Wie oft nachgesehen wird, ob ein Job faellig ist. Ein fester, kleiner Takt
# statt eines Weckens zur Stunde: der Zeitplan kann sich jederzeit aendern.
POLL_SECONDS = max(60, int(os.environ.get("TAGGING_POLL_SECONDS", "300")))


def _get_now():
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(TZ_NAME)
    except (ImportError, KeyError):
        tz = timezone(timedelta(hours=2))
    return datetime.now(tz)


def run_user(user_id, now):
    """Einen faelligen Job ausfuehren. ``True``, wenn ein Lauf gestartet wurde."""
    settings = historical_tagging.load_settings(user_id)
    if not settings.get("enabled") or settings.get("schedule_mode") == "manual":
        return False
    state = tagging_job.load_state(main.DATA_DIR, user_id)
    if not tagging_job.is_due(settings, state, now):
        return False
    if not historical_tagging.is_configured(settings):
        logger.warning("Verschlagwortung: %s hat den Zeitplan aktiv, aber keine Verbindung.", user_id)
        return False
    params = historical_tagging.run_params(settings, now.date())
    # Der Fehler eines Versuchs, auf den noch ein weiterer folgt, wird nicht
    # gemeldet; das entscheidet record_attempt nach dem Lauf.
    params["defer_failure_notice"] = True
    try:
        record = background_runs.start(
            user_id, historical_tagging.RUN_KIND, params,
            label=historical_tagging.run_label(params), trigger="schedule", inline=True,
        )
    except background_runs.RunConflict:
        logger.info("Verschlagwortung: %s hat schon einen laufenden Auftrag, der Termin wartet.", user_id)
        return False
    ok = record.get("status") == "done"
    final = tagging_job.record_attempt(main.DATA_DIR, user_id, now, ok)
    if not ok and final:
        historical_tagging.notify_failure(user_id, record)
    result = record.get("result") or {}
    logger.info(
        "Verschlagwortung: %s → %s (%s verarbeitet, %s übersprungen, %s Fehler)%s",
        user_id, record.get("status"), result.get("processed", 0), result.get("skipped", 0),
        result.get("failed", 0), "" if ok else ": " + str(record.get("error") or ""),
    )
    return True


def run_once(now=None):
    """Fuer jeden Nutzer mit faelligem Job einen Lauf. Gibt die Zahl gestarteter Laeufe zurueck.

    Ein Fehler bei einem Nutzer wird protokolliert und beendet den Durchlauf nicht.
    """
    now = now or _get_now()
    started = 0
    for user in main.get_all_users():
        user_id = user.get("id")
        if not user_id:
            continue
        try:
            started += int(run_user(user_id, now))
            background_runs.cleanup(user_id)
        except Exception as exc:  # noqa: BLE001 - ein Nutzer darf den Rest nicht mitreißen
            logger.warning("Verschlagwortung: Lauf für %s abgebrochen: %s", user_id, exc)
    # Der Nachtlauf des semantischen Index teilt sich diesen Takt, statt einen
    # weiteren Container zu brauchen. Er ist global (Admin), nicht je Nutzer.
    try:
        started += int(semantic_index.run_scheduled(now))
    except Exception as exc:  # noqa: BLE001 - der Tagging-Takt muss weiterlaufen
        logger.warning("Semantischer Index: Nachtlauf abgebrochen: %s", exc)
    return started


def run_loop():
    logger.info("Starte Verschlagwortungs-Takt (Job je Nutzer, timezone=%s)", TZ_NAME)
    # Ein Lauf, den dieser Prozess vor seinem Neustart nicht beenden konnte,
    # blockierte sonst jeden weiteren Lauf derselben Art.
    background_runs.abort_stale_runs(runner="inline")
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Verschlagwortungs-Lauf fehlgeschlagen")
        time.sleep(POLL_SECONDS)


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true", help="Dauerhaft im Takt laufen")
    args = parser.parse_args()
    if args.loop:
        run_loop()
        return 0
    try:
        run_once()
        return 0
    except Exception:
        logger.exception("Verschlagwortungs-Lauf fehlgeschlagen")
        return 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
