"""Private CPU Whisper service with an OpenAI-like transcription API."""

import gc
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path

from flask import Flask, jsonify, request
from faster_whisper import WhisperModel


app = Flask(__name__)
API_KEY = os.environ.get("WHISPER_API_KEY", "")
MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
CPU_THREADS = max(1, int(os.environ.get("WHISPER_CPU_THREADS", "4")))
MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR", "/models")

# Quality steps offered to callers. German accuracy rises with model size, so the
# notes name the CPU/RAM price instead of hiding it: this host has ~4 GiB total.
MODEL_CATALOG = {
    "tiny": {"label": "Mini (tiny)", "note": "Sehr schnell, für Deutsch ungenau", "size_mb": 75},
    "base": {"label": "Basis (base)", "note": "Schnell, brauchbar für kurze Notizen", "size_mb": 145},
    "small": {"label": "Gut (small)", "note": "Deutlich bessere deutsche Texte, ~3x langsamer als base", "size_mb": 500},
    "medium": {"label": "Sehr gut (medium)", "note": "Beste realistische Qualität, sehr langsam und RAM-hungrig", "size_mb": 1500},
    "large-v3": {"label": "Maximal (large-v3)", "note": "Nur mit viel RAM sinnvoll, auf diesem Server nicht empfohlen", "size_mb": 3100},
}
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


def _offered_models():
    raw = os.environ.get("WHISPER_MODELS", "tiny,base,small,medium")
    names = [item.strip() for item in raw.split(",") if item.strip() and _MODEL_NAME_RE.fullmatch(item.strip())]
    if MODEL_NAME not in names:
        names.insert(0, MODEL_NAME)
    return names


def _resolve_model_name(requested):
    """Only ever load a model this deployment explicitly offers."""
    if not requested:
        return MODEL_NAME
    requested = str(requested).strip()
    if requested not in _offered_models():
        raise ValueError(f"Unknown model: {requested[:64]}")
    return requested


_model = None
_loaded_name = None
_model_lock = threading.Lock()
_inference_lock = threading.Lock()


def _authorized():
    if not API_KEY:
        return False
    bearer = request.headers.get("Authorization", "")
    supplied = bearer[7:] if bearer.startswith("Bearer ") else request.headers.get("X-API-Key", "")
    return bool(supplied) and secrets.compare_digest(supplied, API_KEY)


def _get_model(name=None):
    """Keep exactly one model resident; this host cannot hold two at once."""
    global _model, _loaded_name
    name = _resolve_model_name(name)
    with _model_lock:
        if _model is None or _loaded_name != name:
            _model = None
            _loaded_name = None
            gc.collect()
            _model = WhisperModel(
                name, device="cpu", compute_type=COMPUTE_TYPE,
                cpu_threads=CPU_THREADS, download_root=MODEL_DIR,
            )
            _loaded_name = name
    return _model


@app.get("/health")
def health():
    return jsonify({"ok": True, "model": MODEL_NAME, "loaded": _model is not None, "loaded_model": _loaded_name})


@app.get("/v1/models")
def list_models():
    """Public quality-step catalogue so clients can offer a model picker."""
    models = [{
        "id": name,
        "label": MODEL_CATALOG.get(name, {}).get("label", name),
        "note": MODEL_CATALOG.get(name, {}).get("note", ""),
        "size_mb": MODEL_CATALOG.get(name, {}).get("size_mb"),
        "loaded": name == _loaded_name,
    } for name in _offered_models()]
    return jsonify({"default": MODEL_NAME, "loaded": _loaded_name, "models": models})


@app.get("/openapi.json")
def openapi():
    return jsonify({
        "openapi": "3.1.0",
        "info": {"title": "Journl Local Whisper API", "version": "1.0.0"},
        "paths": {
            "/health": {"get": {"responses": {"200": {"description": "Service status"}}}},
            "/v1/models": {"get": {"responses": {"200": {"description": "Offered quality steps"}}}},
            "/v1/models/load": {"post": {
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {"description": "Configured model is downloaded and loaded"},
                    "401": {"description": "Invalid API key"},
                },
            }},
            "/v1/audio/transcriptions": {"post": {
                "security": [{"bearerAuth": []}],
                "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
                    "type": "object", "required": ["file"], "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "model": {"type": "string", "description": "Quality step from GET /v1/models"},
                        "language": {"type": "string", "default": "de"},
                        "response_format": {"type": "string", "enum": ["text", "json", "verbose_json"], "default": "verbose_json"},
                    },
                }}}},
                "responses": {"200": {"description": "Transcript with optional timestamped segments"}, "401": {"description": "Invalid API key"}},
            }},
        },
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
    })


@app.post("/v1/models/load")
def load_model():
    if not _authorized():
        return jsonify({"error": {"message": "Unauthorized"}}), 401
    requested = request.form.get("model") or (request.get_json(silent=True) or {}).get("model")
    try:
        name = _resolve_model_name(requested)
    except ValueError as exc:
        return jsonify({"error": {"message": str(exc)}}), 400
    _get_model(name)
    return jsonify({
        "ok": True, "model": name, "compute_type": COMPUTE_TYPE,
        "cpu_threads": CPU_THREADS, "loaded": True,
    })


@app.post("/v1/audio/transcriptions")
def transcribe():
    if not _authorized():
        return jsonify({"error": {"message": "Unauthorized"}}), 401
    upload = request.files.get("file")
    if not upload:
        return jsonify({"error": {"message": "file is required"}}), 400
    language = request.form.get("language") or None
    response_format = request.form.get("response_format", "verbose_json")
    try:
        model_name = _resolve_model_name(request.form.get("model"))
    except ValueError as exc:
        return jsonify({"error": {"message": str(exc)}}), 400
    suffix = Path(upload.filename or "audio").suffix[:12]
    with tempfile.NamedTemporaryFile(dir="/tmp/whisper", suffix=suffix, delete=True) as temp:
        upload.save(temp)
        temp.flush()
        with _inference_lock:
            segments_iter, info = _get_model(model_name).transcribe(
                temp.name, language=language, vad_filter=True, beam_size=5,
            )
            segments = [{
                "id": index, "start": round(segment.start, 3), "end": round(segment.end, 3),
                "text": segment.text.strip(),
            } for index, segment in enumerate(segments_iter)]
    text = " ".join(item["text"] for item in segments if item["text"]).strip()
    if response_format == "text":
        return text, 200, {"Content-Type": "text/plain; charset=utf-8"}
    if response_format == "json":
        return jsonify({"text": text})
    return jsonify({
        "task": "transcribe", "language": info.language, "duration": info.duration,
        "text": text, "segments": segments, "model": model_name,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, threaded=True)
