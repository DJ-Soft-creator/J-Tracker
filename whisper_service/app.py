"""Private CPU Whisper service with an OpenAI-like transcription API."""

import os
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
_model = None
_model_lock = threading.Lock()
_inference_lock = threading.Lock()


def _authorized():
    if not API_KEY:
        return False
    bearer = request.headers.get("Authorization", "")
    supplied = bearer[7:] if bearer.startswith("Bearer ") else request.headers.get("X-API-Key", "")
    return bool(supplied) and secrets.compare_digest(supplied, API_KEY)


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            _model = WhisperModel(
                MODEL_NAME, device="cpu", compute_type=COMPUTE_TYPE,
                cpu_threads=CPU_THREADS, download_root=MODEL_DIR,
            )
    return _model


@app.get("/health")
def health():
    return jsonify({"ok": True, "model": MODEL_NAME, "loaded": _model is not None})


@app.get("/openapi.json")
def openapi():
    return jsonify({
        "openapi": "3.1.0",
        "info": {"title": "Journl Local Whisper API", "version": "1.0.0"},
        "paths": {
            "/health": {"get": {"responses": {"200": {"description": "Service status"}}}},
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
    _get_model()
    return jsonify({
        "ok": True, "model": MODEL_NAME, "compute_type": COMPUTE_TYPE,
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
    suffix = Path(upload.filename or "audio").suffix[:12]
    with tempfile.NamedTemporaryFile(dir="/tmp/whisper", suffix=suffix, delete=True) as temp:
        upload.save(temp)
        temp.flush()
        with _inference_lock:
            segments_iter, info = _get_model().transcribe(
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
        "text": text, "segments": segments, "model": MODEL_NAME,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, threaded=True)
