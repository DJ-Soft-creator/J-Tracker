#!/usr/bin/env python3
"""Sidecar für die Journal-Analyse-Verwaltung.

Fünfter Compose-Dienst nach dem Muster von ``scheduler.py``,
``whisper-scheduler.py``, ``bucket-scheduler.py`` und
``db-checkpoint-scheduler.py``: eigener Prozess, unabhängig vom Webinterface,
``--loop`` mit festem Takt. Anders als die Familien- und Sammelmappen-Scheduler
läuft er nicht einmal täglich zu einer Uhrzeit, sondern pollt alle paar Minuten
und überlässt die Entscheidung ``journal_analysis_dates.is_due`` je Job.

Der Dienst tut ohne aktivierte Datenbank nichts (``JOURNL_DB_ENABLED``) — ein
normaler PROD-Betrieb bleibt damit file-only. Für Analysen ist ein lokaler
Anbieter (id beginnt mit ``lm_``, privates Netz) die Regel; ein Cloud-Anbieter
nur, wenn er in der Anbieterverwaltung ausdrücklich für die Journal-Analyse
freigegeben ist (``journal_analysis: true``, HTTPS) — siehe
``journal_analysis_providers.resolve_analysis_provider``. Host-Runner nie.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"
sys.path.insert(0, str(APP_DIR if APP_DIR.is_dir() else ROOT_DIR))

import db  # noqa: E402
import journal_analysis as ja  # noqa: E402
import journal_analysis_dates as dates  # noqa: E402
import journal_analysis_providers  # noqa: E402
import journal_analysis_worker as worker  # noqa: E402
import main  # noqa: E402
import pushover  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300


def _engine():
    from sqlalchemy import create_engine
    return create_engine(db.database_url(), pool_pre_ping=True)


def run_once(*, now=None):
    """Alle fälligen Jobs aller Nutzer einmal abarbeiten.

    Rückgabe ist die Anzahl tatsächlich gestarteter Jobs. Ein einzelner Fehler
    wird protokolliert und beendet nicht den ganzen Durchlauf.
    """
    if not db.is_enabled():
        logger.info("Journal-Analyse: Datenbank nicht aktiviert, nichts zu tun.")
        return 0
    now = now or main.get_tz_aware_now()[0]
    engine = _engine()
    started = 0
    try:
        jobs = ja.list_enabled_jobs(engine=engine)
        if not jobs:
            return 0
        config = main.load_config()
        for job in jobs:
            try:
                last_run_at = worker.latest_run_at(job["owner_id"], job["id"], engine=engine)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Journal-Analyse: Laufstand von %s nicht lesbar: %s", job["id"], exc)
                continue
            if not dates.is_due(job, now, last_run_at):
                continue
            provider = journal_analysis_providers.resolve_analysis_provider(
                config, job.get("provider_id"))
            if provider is None:
                logger.warning("Journal-Analyse: Job %s hat keinen erlaubten Anbieter (%s).",
                               job["id"], job.get("provider_id"))
                continue
            if (not str(job.get("model") or "").strip()
                    and not str(provider.get("model") or "").strip()):
                # Wie bei „Chat with your Notes“: weder im Job noch im Anbieter
                # ein Modell -> beim Lauf live auslesen, nicht raten, nicht speichern.
                try:
                    model = journal_analysis_providers.resolve_effective_model(provider)
                except journal_analysis_providers.ProviderDiscoveryError as exc:
                    logger.warning("Journal-Analyse: Job %s – Modell nicht ermittelbar: %s",
                                   job["id"], exc)
                    continue
                if not model:
                    logger.warning("Journal-Analyse: Job %s – Endpunkt meldet kein Modell.", job["id"])
                    continue
                provider = {**provider, "model": model}
            prompt_body = None
            if job.get("prompt_template_id"):
                try:
                    template = ja.get_prompt_template(job["owner_id"], job["prompt_template_id"],
                                                      engine=engine)
                    prompt_body = template["body"]
                except ja.JournalAnalysisError:
                    logger.warning("Journal-Analyse: Prompt-Vorlage von Job %s fehlt.", job["id"])
            try:
                report = worker.run_job(
                    job["owner_id"], job, today=now.date(), call_ai=main._call_ai_api,
                    provider=provider, data_root=main.DATA_DIR, engine=engine,
                    prompt_body=prompt_body, now=now,
                )
                started += 1
                logger.info("Journal-Analyse: Job %s (%s) → %s Tage, %s übersprungen, %s Fehler.",
                            job["id"], job.get("name", ""), report["processed"],
                            report["skipped"], report["failed"])
            except Exception as exc:  # noqa: BLE001 - ein Job darf den Rest nicht mitreißen
                logger.warning("Journal-Analyse: Job %s abgebrochen: %s", job["id"], exc)
        return started
    finally:
        engine.dispose()


def scan_reminders(*, now=None):
    """Feste Journal-Erinnerungen („erinnere mich morgen 10 Uhr …“) anlegen.

    Laeuft vor der Analyse in jedem Takt, damit eine Erinnerung wenige Minuten
    nach dem Schreiben geplant ist — nicht erst mit der Vortagsanalyse am
    naechsten Morgen. Mehrfaches Pruefen ist harmlos: bekannte bleiben einmalig.
    """
    if not db.is_enabled():
        return 0
    now = now or main.get_tz_aware_now()[0]
    engine = _engine()
    try:
        created = worker.scan_journal_reminders(now=now, data_root=main.DATA_DIR, engine=engine)
        if created:
            logger.info("Journal-Erinnerungen: %s neu geplant.", created)
        return created
    finally:
        engine.dispose()


def dispatch_pushover(*, now=None):
    """Pushover fuer alle Nutzer mit eingerichtetem Konto (pushover.py).

    Laeuft hier, weil dieser Sidecar ohnehin alle paar Minuten pollt und der
    einzige Prozess ist, der versendet - so gibt es keine Doppelzustellung
    zwischen Web und Scheduler. Analyse-Hinweise brauchen die DB, die
    Familien-Sammelmeldung nicht.
    """
    import family_documents
    import tagging_job

    now = now or main.get_tz_aware_now()[0]
    engine = _engine() if db.is_enabled() else None

    def notifications_for(user_id):
        if engine is None:
            return []
        return ja.list_notifications(user_id, status="open", due_only=True, now=now, engine=engine)

    try:
        user_ids = [user["id"] for user in main.get_all_users() if user.get("id")]
        sent = pushover.dispatch_all(main.DATA_DIR, user_ids, now,
                                     notifications_for=notifications_for,
                                     documents_for=family_documents.load_documents,
                                     tagging_events_for=lambda user_id: tagging_job.list_events(
                                         main.DATA_DIR, user_id))
        if sent:
            logger.info("Pushover: %s Nachricht(en) versendet.", sent)
        return sent
    finally:
        if engine is not None:
            engine.dispose()


def main_loop(interval):
    while True:
        try:
            scan_reminders()
        except Exception as exc:  # noqa: BLE001 - die Analyse laeuft trotzdem
            logger.warning("Journal-Erinnerungen: Durchlauf fehlgeschlagen: %s", exc)
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Journal-Analyse: Durchlauf fehlgeschlagen: %s", exc)
        try:
            dispatch_pushover()
        except Exception as exc:  # noqa: BLE001 - Pushover darf die Analyse nie aufhalten
            logger.warning("Pushover: Durchlauf fehlgeschlagen: %s", exc)
        time.sleep(max(30, interval))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int,
                        default=int(os.environ.get("JOURNAL_ANALYSIS_INTERVAL", DEFAULT_INTERVAL_SECONDS)))
    arguments = parser.parse_args()
    if arguments.loop:
        main_loop(arguments.interval)
    else:
        run_once()