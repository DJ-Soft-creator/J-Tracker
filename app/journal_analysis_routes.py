"""HTTP-Oberfläche der Journal-Analyse-Verwaltung (`/api/journal-analysis/...`).

Eigenständiger Bereich (JA-01) — bewusst **ohne** Admin-Gate und ohne Bezug zum
AI-Worker. Nur Request-Validierung und Serialisierung; die Fachlogik liegt in
``journal_analysis.py``.

Writes laufen über ``main.csrf_protect``. Ist die Datenbank nicht aktiviert,
antworten alle Endpunkte mit 503 und einer klaren Meldung statt mit einem
Datenbankfehler.
"""

import sys

from flask import Blueprint, jsonify, request

import background_runs
import db
import journal_analysis as ja
import journal_analysis_manual as ja_manual
import journal_analysis_providers as ja_providers
import journal_analysis_runs as ja_runs


journal_analysis_bp = Blueprint("journal_analysis", __name__,
                                url_prefix="/api/journal-analysis")


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _guard(*, write=False):
    main = _main()
    user = main._current_user() if main else None
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if write:
        error = main.csrf_protect(lambda: None)()
        if error:
            return None, error
    if not db.is_enabled():
        return None, (jsonify({
            "error": "Die Journal-Analyse benötigt die aktivierte Datenbank.",
            "db_enabled": False,
        }), 503)
    return user, None


def _no_store(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _error(exc):
    if isinstance(exc, (ja.JobNotFound, ja.PromptNotFound, ja.RunNotFound,
                        ja.NotificationNotFound)):
        return jsonify({"error": str(exc)}), 404
    if isinstance(exc, ja.JournalAnalysisError):
        return jsonify({"error": str(exc)}), 400
    raise exc


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ─── Übersicht ───────────────────────────────────────────────────────


@journal_analysis_bp.route("/state", methods=["GET"])
def state():
    """Was die Oberfläche beim Öffnen braucht: Jobs, Vorlagen, offene Hinweise."""
    user, error = _guard()
    if error:
        return error
    notifications = ja.list_notifications(user["id"], status="open")
    return _no_store({
        "jobs": ja.list_jobs(user["id"]),
        "prompts": ja.list_prompt_templates(user["id"]),
        "notifications": notifications,
        "runs": ja_runs.list_runs(user["id"], limit=50),
        "date_range_modes": list(ja.DATE_RANGE_MODES),
        "schedule_modes": list(ja.SCHEDULE_MODES),
    })


# ─── Jobs ────────────────────────────────────────────────────────────


@journal_analysis_bp.route("/jobs", methods=["GET"])
def list_jobs():
    user, error = _guard()
    if error:
        return error
    return _no_store({"jobs": ja.list_jobs(user["id"])})


@journal_analysis_bp.route("/jobs", methods=["POST"])
def create_job():
    user, error = _guard(write=True)
    if error:
        return error
    try:
        job = ja.create_job(user["id"], _body())
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    return _no_store({"job": job}, 201)


@journal_analysis_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    user, error = _guard()
    if error:
        return error
    try:
        job = ja.get_job(user["id"], job_id)
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    return _no_store({"job": job, "runs": ja_runs.list_runs(user["id"], job_id=job_id)})


@journal_analysis_bp.route("/jobs/<job_id>", methods=["PUT", "PATCH"])
def update_job(job_id):
    user, error = _guard(write=True)
    if error:
        return error
    try:
        job = ja.update_job(user["id"], job_id, _body())
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    return _no_store({"job": job})


@journal_analysis_bp.route("/jobs/<job_id>/run", methods=["POST"])
def run_job_now(job_id):
    """Einen Job sofort ausführen — auch manuelle Jobs ohne Zeitplan.

    Der Lauf landet im Server-Lauf-System und ist dort abbrechbar; der Browser
    bekommt nur die Lauf-Kennung und beobachtet den Fortschritt über
    ``/api/runs/<id>``.
    """
    user, error = _guard(write=True)
    if error:
        return error
    try:
        ja.get_job(user["id"], job_id)
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force"))
    try:
        record = background_runs.start(user["id"], ja_manual.RUN_KIND,
                                       {"job_id": job_id, "force": force},
                                       label="Journal-Analyse")
    except background_runs.RunConflict as exc:
        return jsonify({"error": str(exc)}), 409
    except background_runs.RunError as exc:
        return jsonify({"error": str(exc)}), 400
    return _no_store({"run": background_runs.public_run(record)}, 202)


@journal_analysis_bp.route("/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    user, error = _guard(write=True)
    if error:
        return error
    try:
        slug = ja.delete_job(user["id"], job_id)
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    return jsonify({"ok": True, "slug": slug})


# ─── Prompt-Vorlagen ─────────────────────────────────────────────────


@journal_analysis_bp.route("/providers/<provider_id>/models", methods=["GET"])
def provider_models(provider_id):
    """Live-Modell-Liste eines lokalen Anbieters — wie bei „Chat with your Notes“.

    Damit muss im Job kein Modell stehen: ist keines gesetzt, liest der Lauf es
    zur Laufzeit über denselben Endpunkt aus.
    """
    user, error = _guard()
    if error:
        return error
    module = _main()
    config = module.load_config() if module else {}
    provider = ja_providers.resolve_analysis_provider(config, provider_id)
    if provider is None:
        return jsonify({"error": "Unbekannter oder nicht freigegebener Anbieter"}), 404
    if ja_providers.is_cloud_provider(provider):
        # Cloud: das in der Anbieterverwaltung gesetzte Modell, keine Live-Liste
        # (die von OpenRouter & Co. hat Hunderte Eintraege).
        model = str(provider.get("model") or "").strip()
        return _no_store({"provider_id": provider_id, "models": [model] if model else [], "cloud": True})
    try:
        models = ja_providers.discover_models(provider)
    except ja_providers.ProviderDiscoveryError as exc:
        return jsonify({"error": str(exc)}), 502
    return _no_store({"provider_id": provider_id, "models": models})


@journal_analysis_bp.route("/prompts", methods=["GET"])
def list_prompts():
    user, error = _guard()
    if error:
        return error
    return _no_store({"prompts": ja.list_prompt_templates(user["id"])})


@journal_analysis_bp.route("/prompts", methods=["POST"])
def create_prompt():
    user, error = _guard(write=True)
    if error:
        return error
    data = _body()
    try:
        template = ja.create_prompt_template(user["id"], data.get("name"), data.get("body"))
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    return _no_store({"prompt": template}, 201)


@journal_analysis_bp.route("/prompts/<template_id>", methods=["PUT", "PATCH"])
def update_prompt(template_id):
    user, error = _guard(write=True)
    if error:
        return error
    data = _body()
    try:
        template = ja.update_prompt_template(
            user["id"], template_id, name=data.get("name"), body=data.get("body"))
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    return _no_store({"prompt": template})


@journal_analysis_bp.route("/prompts/<template_id>", methods=["DELETE"])
def delete_prompt(template_id):
    user, error = _guard(write=True)
    if error:
        return error
    try:
        ja.delete_prompt_template(user["id"], template_id)
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    return jsonify({"ok": True})


# ─── Läufe / Job-Log ─────────────────────────────────────────────────


@journal_analysis_bp.route("/runs", methods=["GET"])
def list_runs():
    """Job-Log: KI-Worker-Job und Journal-Analyse werden getrennt ausgewiesen."""
    user, error = _guard()
    if error:
        return error
    job_id = request.args.get("job_id") or None
    runs = ja_runs.list_runs(user["id"], job_id=job_id, limit=request.args.get("limit", 100))
    jobs = {job["id"]: job["name"] for job in ja.list_jobs(user["id"])}
    for run in runs:
        run["job_name"] = jobs.get(run["job_id"], "")
        run["source"] = "journal_analysis"
    return _no_store({"runs": runs})


@journal_analysis_bp.route("/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    user, error = _guard()
    if error:
        return error
    try:
        run = ja_runs.get_run(user["id"], run_id)
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    run["source"] = "journal_analysis"
    return _no_store({"run": run})


# ─── Benachrichtigungen ──────────────────────────────────────────────


@journal_analysis_bp.route("/notifications", methods=["GET"])
def list_notifications():
    user, error = _guard()
    if error:
        return error
    status = request.args.get("status") or None
    upcoming = request.args.get("upcoming") in ("1", "true", "yes")
    retention = ja.NOTIFICATION_RETENTION_DAYS
    if status == "active":
        # Schreiben-Tab: nur Meldungen, die jetzt relevant sind (``due_only``).
        # Mit ``upcoming=1`` kommen auch geplante dazu.
        items = ja.list_notifications(user["id"], statuses=["open", "ignored"],
                                      due_only=not upcoming, max_age_days=retention)
    elif status == "recent":
        # Verwaltung → Hinweise: alles der letzten 30 Tage, auch Geschlossenes,
        # damit im Schreiben-Tab Weggedrücktes nachschaubar bleibt.
        items = ja.list_notifications(user["id"], max_age_days=retention)
    else:
        items = ja.list_notifications(user["id"], status=status)
    response, code = _no_store({"notifications": items, "count": len(items)})
    return response, code


@journal_analysis_bp.route("/notifications/close-all", methods=["POST"])
def close_all_notifications():
    """„Alle schließen“ im Schreiben-Tab: fällige Hinweise abhaken."""
    user, error = _guard(write=True)
    if error:
        return error
    return _no_store({"closed": ja.close_due_notifications(user["id"])})


@journal_analysis_bp.route("/notifications/<notification_id>/status", methods=["POST"])
def set_notification_status(notification_id):
    """Ignorieren, abhaken oder übernehmen."""
    user, error = _guard(write=True)
    if error:
        return error
    status = (_body().get("status") or "").strip()
    try:
        item = ja.set_notification_status(user["id"], notification_id, status)
    except ja.JournalAnalysisError as exc:
        return _error(exc)
    return _no_store({"notification": item})


__all__ = ["journal_analysis_bp"]