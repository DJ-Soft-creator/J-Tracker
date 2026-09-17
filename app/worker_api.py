"""Disabled-by-default, narrow API for a future authenticated agent worker."""

import hmac
import logging
import os

from flask import Blueprint, jsonify, request

import background_jobs
import share_api


logger = logging.getLogger(__name__)


worker_api_bp = Blueprint("worker_api", __name__, url_prefix="/api/internal/worker")


def _token_is_valid():
    configured = os.environ.get("JOURNL_WORKER_API_TOKEN", "")
    presented = request.headers.get("X-Journl-Worker-Token", "")
    # A missing or weak configuration means that this surface simply does not
    # exist.  It is never protected by browser authentication or CSRF tokens.
    return len(configured) >= 32 and hmac.compare_digest(configured, presented)


def _guard():
    if not _token_is_valid():
        return jsonify({"error": "Not found"}), 404
    return None


def _payload():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _response(value, status=200):
    response = jsonify(value)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@worker_api_bp.route("/agent-jobs/claim", methods=["POST"])
def claim_agent_job():
    denied = _guard()
    if denied:
        return denied
    try:
        lease_seconds = _payload().get("lease_seconds", 300)
        job = background_jobs.claim_next_agent_job(lease_seconds=lease_seconds)
    except ValueError as exc:
        return _response({"error": str(exc)}, 400)
    return _response({"job": job}, 200)


@worker_api_bp.route("/agent-jobs/<job_id>/response", methods=["POST"])
def submit_agent_response(job_id):
    denied = _guard()
    if denied:
        return denied
    payload = _payload()
    try:
        result = background_jobs.record_agent_response(
            job_id, payload.get("lease_token"), payload.get("response_text"),
        )
    except ValueError as exc:
        return _response({"error": str(exc)}, 400)
    if result is None:
        return _response({"error": "Lease not found"}, 409)
    # Share jobs are delivered server-side and do not depend on a browser poll.
    # The durable Share worker also reconciles this state after restarts.
    try:
        share_api.finalize_agent_job(job_id)
    except Exception as exc:
        # The DB response is already durable.  Share's own durable reconciler
        # retries delivery and records a visible per-job error if it cannot.
        logger.warning("Share delivery for completed agent job %s is deferred: %s", job_id, type(exc).__name__)
    return _response(result, 201)


@worker_api_bp.route("/agent-jobs/<job_id>/failure", methods=["POST"])
def report_agent_failure(job_id):
    denied = _guard()
    if denied:
        return denied
    payload = _payload()
    error_code = payload.get("error_code")
    if not isinstance(error_code, str) or not error_code or len(error_code) > 120:
        return _response({"error": "Ungültiger Fehlercode"}, 400)
    try:
        finished = background_jobs.finish_job(job_id, payload.get("lease_token"), "failed", error_code=error_code)
    except ValueError as exc:
        return _response({"error": str(exc)}, 400)
    if not finished:
        return _response({"error": "Lease not found"}, 409)
    return _response({"ok": True})
