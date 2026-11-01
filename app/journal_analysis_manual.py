"""Manueller Lauf der Journal-Analyse aus der Verwaltung.

Reiht einen Lauf über das vorhandene Server-Lauf-System ein
(``background_runs``) — dasselbe Muster wie Brain-Tagging, Einkaufslisten-Agent
und Schreib-Agent. Der Lauf läuft im Serverprozess, ist abbrechbar und über
``/api/runs`` beobachtbar; der Browser blockiert nicht.

Die eigentliche Ausführung bleibt in ``journal_analysis_worker``. Ein
prozessübergreifender Sperrpfad je Job verhindert, dass ein manueller Lauf und
der Sidecar denselben Tag gleichzeitig schreiben.
"""

import sys
from datetime import datetime

import background_runs
import db
import journal_analysis as ja
import journal_analysis_providers as providers
import journal_analysis_worker as worker


RUN_KIND = "journal_analysis"
WRITE_LOCK = "Es läuft bereits eine Analyse dieses Nutzers."


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _engine():
    from sqlalchemy import create_engine
    return create_engine(db.database_url(), pool_pre_ping=True)


def _today(main):
    getter = getattr(main, "get_tz_aware_now", None) if main else None
    if callable(getter):
        try:
            return getter()[0].date()
        except (TypeError, ValueError, IndexError):
            pass
    return datetime.now().astimezone().date()


def run_handler(handle, params):
    """Einen Job sofort ausführen — als Serverlauf, nicht im Browser-Request."""
    main = _main()
    job_id = str(params.get("job_id") or "")
    if not job_id:
        raise background_runs.RunError("Kein Job angegeben.")
    engine = _engine()
    try:
        job = ja.get_job(handle.user_id, job_id, engine=engine)
        config = main.load_config() if main else {}
        provider = providers.resolve_analysis_provider(config, job.get("provider_id"))
        if provider is None:
            raise background_runs.RunError(
                "Der Job hat keinen erlaubten Anbieter (lokal oder für die Journal-Analyse freigegeben).")
        if (not str(job.get("model") or "").strip()
                and not str(provider.get("model") or "").strip()):
            provider = {**provider, "model": providers.resolve_effective_model(provider)}
        prompt_body = None
        if job.get("prompt_template_id"):
            template = ja.get_prompt_template(handle.user_id, job["prompt_template_id"], engine=engine)
            prompt_body = template["body"]
        handle.progress(phase="start", job=job.get("name", ""))
        force = bool(params.get("force"))
        report = worker.run_job(
            handle.user_id, job, today=_today(main),
            # „Abbrechen“ soll auch einen laufenden Modellaufruf beenden, nicht
            # erst nach dessen Ende greifen; der Stream-Leser fragt alle 5 s.
            call_ai=lambda provider, ai_function, message: main._call_ai_api(
                provider, {**ai_function, "should_stop": lambda: handle.cancelled}, message),
            provider=provider, data_root=main.DATA_DIR, engine=engine,
            prompt_body=prompt_body, is_cancelled=lambda: handle.cancelled, force=force,
        )
        handle.progress(processed=report.get("processed", 0), skipped=report.get("skipped", 0))
        handle.log("{} Tag(e) analysiert, {} übersprungen.".format(
            report.get("processed", 0), report.get("skipped", 0)))
        return report
    except (ja.JournalAnalysisError, providers.ProviderDiscoveryError,
            worker.JobBusyError) as exc:
        raise background_runs.RunError(str(exc)) from exc
    finally:
        engine.dispose()


background_runs.register(RUN_KIND, run_handler)


__all__ = ["RUN_KIND", "run_handler"]