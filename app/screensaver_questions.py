"""Bildschirmschoner-Modus „Fragen“: Fragensets, Ablauf und KI-Vorschlaege.

Die Fragen stehen ueber der Bildershow - entweder als **Impuls** (grosser
Hintergrundtext unter dem Schreibfeld der Kulisse, geschrieben wird frei) oder
als **Fragen-Session** (je Frage ein Textfeld oder feste Antwortknoepfe, die
Antworten wandern als Block ins Schreibfeld). Gespeichert wird nichts ohne den
Nutzer: das Schreibfeld sendet er selbst.

Ablaeufe:

* ``manual``   — eine Frage, keine Zeit; weiter geht es von Hand.
* ``auto``     — die Fragen wechseln nach der eingestellten Zeit.
* ``quiz``     — wie ``auto``, aber mit sichtbar ablaufendem Countdown.
* ``sequence`` — die Fragen des Sets der Reihe nach, jede mit eigener Zeit.

Jeder Ablauf endet nach der letzten Frage des Sets; er beginnt nicht von vorn -
ausser ``manual``/``auto`` mit ``loop``. Zurueck gibt es nur dort; Quiz und
Sequenz laufen nur vorwaerts.

Sets, Ablauf und gespeicherte Generier-Prompts liegen je Nutzer unter
``<user>/screensaver/question-sets.json``.
„Fragen generieren“ laeuft als Serverlauf (``background_runs``) und nur ueber
einen **lokalen** Anbieter (``lm_*`` im privaten Netz, wie die Journal-Analyse):
auf Wunsch gehen die Journale der letzten Tage als Kontext mit, und die duerfen
das Haus nicht verlassen. Das Modell ist waehlbar (Live-Liste des Endpunkts);
ohne Wahl nimmt der Lauf das konfigurierte bzw. erste geladene Modell.
"""

import json
import re
import sys
import uuid
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

import background_runs


questions_bp = Blueprint("screensaver_questions", __name__, url_prefix="/api/screensaver/questions")

RUN_KIND = "screensaver-questions"
FILE_NAME = "question-sets.json"
MAX_SETS = 30
MAX_QUESTIONS = 60
MAX_TEXT = 400
MAX_LABEL = 80
MAX_CHOICES = 9
MAX_CHOICE = 80
SECONDS_MIN = 5
SECONDS_MAX = 1800
GENERATE_MAX = 20
CONTEXT_DAYS_MAX = 31
CONTEXT_CHARS = 30000
MAX_PROMPTS = 30
MAX_INSTRUCTION = 1000
MAX_MODEL = 200

FLOWS = ("manual", "auto", "quiz", "sequence")
ANSWERS = ("impulse", "session")
TIMEOUTS = ("keep", "skip", "wait")
DEFAULT_FLOW = {
    "set_id": "",
    "flow": "auto",
    "seconds": 90,
    "answer": "impulse",
    "on_timeout": "keep",
    "shuffle": True,
    "heading": True,
    "loop": False,
}

STARTER_SET = {
    "id": "tagesreflexion",
    "label": "Tagesreflexion",
    "questions": [
        {"id": "energie", "text": "Wie war deine Energie heute?", "seconds": 20,
         "choices": ["Hoch", "Mittel", "Niedrig"], "required": True},
        {"id": "schlaf", "text": "Wie hast du letzte Nacht geschlafen?", "seconds": 20,
         "choices": ["Gut", "Okay", "Schlecht"], "required": True},
        {"id": "moment", "text": "Welcher Moment heute war es wert, festgehalten zu werden?",
         "seconds": 120, "choices": [], "required": False},
        {"id": "beschaeftigt", "text": "Was beschäftigt dich gerade am meisten?",
         "seconds": 120, "choices": [], "required": False},
        {"id": "morgen", "text": "Was ist morgen das Wichtigste?", "seconds": 60,
         "choices": [], "required": False},
    ],
}

GENERATE_PROMPT = (
    "Du formulierst Fragen für ein persönliches Journal. Die Fragen sollen zum "
    "Schreiben anregen: konkret, offen, freundlich, ohne Bewertung, je höchstens "
    "zwei Sätze, auf Deutsch, in der Du-Form. Wenn Journalauszüge mitkommen, "
    "knüpfe an Themen, Vorhaben und offene Punkte darin an, ohne Details zu "
    "zitieren oder etwas zu erfinden. Für schnelle Einschätzungen (Stimmung, "
    "Energie, Fortschritt) darfst du feste Antwortmöglichkeiten vorschlagen.\n"
    "Antworte ausschließlich mit JSON der Form "
    "{\"questions\": [{\"text\": \"...\", \"choices\": []}]} — `choices` ist "
    "leer oder enthält 2 bis 5 kurze Antworten."
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


# ─── Bereinigung ─────────────────────────────────────────────────────

def _slug(value):
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:48]
    return text if _ID_RE.match(text or "") else ""


def _seconds(value, default=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(SECONDS_MIN, min(SECONDS_MAX, int(value)))


def clean_question(raw):
    """Eine Frage auf erlaubte Felder und Laengen bringen; ``None`` ohne Text."""
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        return None
    text = " ".join(str(raw.get("text") or "").split())[:MAX_TEXT]
    if not text:
        return None
    choices = []
    for choice in raw.get("choices") or []:
        label = " ".join(str(choice or "").split())[:MAX_CHOICE]
        if label and label not in choices:
            choices.append(label)
    return {
        "id": _slug(raw.get("id")) or uuid.uuid4().hex[:12],
        "text": text,
        "seconds": _seconds(raw.get("seconds")),
        "choices": choices[:MAX_CHOICES],
        "required": bool(raw.get("required")),
    }


def clean_sets(raw):
    sets = []
    seen = set()
    for entry in (raw if isinstance(raw, list) else [])[:MAX_SETS]:
        if not isinstance(entry, dict):
            continue
        label = " ".join(str(entry.get("label") or "").split())[:MAX_LABEL] or "Fragen"
        set_id = _slug(entry.get("id")) or _slug(label) or uuid.uuid4().hex[:12]
        while set_id in seen:
            set_id = f"{set_id[:40]}-{uuid.uuid4().hex[:6]}"
        seen.add(set_id)
        questions = []
        question_ids = set()
        for item in (entry.get("questions") or [])[:MAX_QUESTIONS]:
            question = clean_question(item)
            if question is None:
                continue
            while question["id"] in question_ids:
                question["id"] = uuid.uuid4().hex[:12]
            question_ids.add(question["id"])
            questions.append(question)
        sets.append({"id": set_id, "label": label, "questions": questions})
    return sets


def clean_flow(raw, sets=None):
    data = raw if isinstance(raw, dict) else {}
    flow = dict(DEFAULT_FLOW)
    for key, choices in (("flow", FLOWS), ("answer", ANSWERS), ("on_timeout", TIMEOUTS)):
        if data.get(key) in choices:
            flow[key] = data[key]
    # 0 ist die Reglerstufe „Manuell“: keine Zeit, weiter von Hand.
    seconds = data.get("seconds")
    manual = seconds == 0 and not isinstance(seconds, bool)
    flow["seconds"] = 0 if manual else _seconds(seconds, DEFAULT_FLOW["seconds"])
    for key in ("shuffle", "heading", "loop"):
        if isinstance(data.get(key), bool):
            flow[key] = data[key]
    known = [item["id"] for item in sets or []]
    set_id = str(data.get("set_id") or "")
    flow["set_id"] = set_id if set_id in known else (known[0] if known else "")
    return flow


def clean_prompts(raw):
    """Gespeicherte Generier-Prompts: Wunsch plus Anzahl, Tage, Anbieter, Modell."""
    prompts = []
    seen = set()
    for entry in (raw if isinstance(raw, list) else [])[:MAX_PROMPTS]:
        if not isinstance(entry, dict):
            continue
        instruction = str(entry.get("instruction") or "").strip()[:MAX_INSTRUCTION]
        label = " ".join(str(entry.get("label") or "").split())[:MAX_LABEL] or " ".join(instruction.split())[:40]
        if not label:
            continue
        prompt_id = _slug(entry.get("id")) or _slug(label) or uuid.uuid4().hex[:12]
        while prompt_id in seen:
            prompt_id = f"{prompt_id[:40]}-{uuid.uuid4().hex[:6]}"
        seen.add(prompt_id)
        count, days = entry.get("count"), entry.get("days")
        prompts.append({
            "id": prompt_id,
            "label": label,
            "instruction": instruction,
            "count": max(1, min(GENERATE_MAX, count)) if isinstance(count, int) and not isinstance(count, bool) else 5,
            "days": max(0, min(CONTEXT_DAYS_MAX, days)) if isinstance(days, int) and not isinstance(days, bool) else 0,
            "provider_id": str(entry.get("provider_id") or "")[:MAX_LABEL],
            "model": str(entry.get("model") or "").strip()[:MAX_MODEL],
        })
    return prompts


# ─── Ablage ──────────────────────────────────────────────────────────

def _path(user_id):
    return _main().DATA_DIR / str(user_id) / "screensaver" / FILE_NAME


def load(user_id):
    """``{"flow": …, "sets": […], "prompts": […]}``; ohne Datei mit dem Starter-Set."""
    import screensaver

    stored = screensaver._read_json(_path(user_id)) or {}
    sets = clean_sets(stored.get("sets")) if "sets" in stored else clean_sets([STARTER_SET])
    return {"flow": clean_flow(stored.get("flow"), sets), "sets": sets,
            "prompts": clean_prompts(stored.get("prompts"))}


def save(user_id, payload):
    """Teilweise speichern: was fehlt, bleibt wie es war."""
    import screensaver

    current = load(user_id)
    data = payload if isinstance(payload, dict) else {}
    sets = clean_sets(data["sets"]) if "sets" in data else current["sets"]
    flow = clean_flow({**current["flow"], **(data.get("flow") or {})}, sets)
    prompts = clean_prompts(data["prompts"]) if "prompts" in data else current["prompts"]
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {"flow": flow, "sets": sets, "prompts": prompts}
    screensaver._write_json(path, stored)
    return stored


# ─── Lokale Anbieter und Journal-Kontext ─────────────────────────────

def local_providers(config):
    import journal_analysis_providers as providers

    result = []
    for item in config.get("ai_providers") or []:
        provider = providers.resolve_local_provider(config, item.get("id"))
        if provider is not None:
            result.append(provider)
    return result


def journal_context(user_id, days, data_root, today=None):
    """Die Journale der letzten ``days`` Tage, neueste zuletzt, hart gekappt."""
    import journal_analysis_sources as sources

    days = max(0, min(CONTEXT_DAYS_MAX, int(days or 0)))
    if not days:
        return ""
    today = today or date.today()
    chunks = []
    for offset in range(days - 1, -1, -1):
        collected = sources.collect_sources(user_id, today - timedelta(days=offset), [], data_root)
        if collected.get("journal"):
            chunks.append(sources.render_sources(collected, total_limit=CONTEXT_CHARS))
    text = "\n\n".join(chunks)
    # Bei zu viel Text zaehlen die juengsten Tage: vorne kuerzen.
    return text[-CONTEXT_CHARS:] if len(text) > CONTEXT_CHARS else text


def parse_generated(text):
    """Die Modellantwort in bereinigte Fragen zerlegen; Unbrauchbares faellt weg."""
    raw = _FENCE_RE.sub("", str(text or "").strip()).strip()
    payload = None
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start, end = raw.find(start_char), raw.rfind(end_char)
        if 0 <= start < end:
            try:
                payload = json.loads(raw[start:end + 1])
                break
            except ValueError:
                continue
    if isinstance(payload, dict):
        payload = payload.get("questions")
    if not isinstance(payload, list):
        # Kein JSON: jede nicht leere Zeile ist eine Frage.
        payload = [line.lstrip("-*0123456789. )").strip() for line in raw.splitlines()]
    questions = []
    for item in payload:
        question = clean_question(item)
        if question is not None:
            question["choices"] = question["choices"][:5]
            questions.append(question)
    return questions[:GENERATE_MAX]


def generate_handler(handle, params):
    main = _main()
    config = main.load_config()
    available = local_providers(config)
    provider = next((item for item in available if item.get("id") == params.get("provider_id")), None)
    provider = provider or (available[0] if available else None)
    if provider is None:
        raise background_runs.RunError(
            "Kein lokaler KI-Anbieter (lm_* im privaten Netz) eingerichtet - "
            "Journalinhalte gehen hier nicht nach außen.")
    # Ein im Formular gewaehltes Modell geht vor; sonst das konfigurierte oder
    # - wie bei der Journal-Analyse - das erste, das der Endpunkt meldet.
    chosen = str(params.get("model") or "").strip()[:MAX_MODEL]
    if chosen:
        provider = {**provider, "model": chosen}
    if not str(provider.get("model") or "").strip():
        import journal_analysis_providers as providers

        provider = {**provider, "model": providers.resolve_effective_model(provider)}
    count = max(1, min(GENERATE_MAX, int(params.get("count") or 5)))
    instruction = " ".join(str(params.get("instruction") or "").split())[:1000]
    handle.progress(phase="context")
    context = journal_context(handle.user_id, params.get("days") or 0, main.DATA_DIR)
    message = (f"Formuliere {count} Fragen."
               + (f"\nWunsch: {instruction}" if instruction else "")
               + (f"\n\n## Journalauszüge\n{context}" if context else ""))
    handle.progress(phase="model", provider=provider.get("id"))
    response = main._call_ai_api(provider, {
        "system_prompt": GENERATE_PROMPT, "max_tokens": 3000, "temperature": 0.7,
        "require_content": True, "stream": True, "timeout": 600, "max_duration": 1800,
    }, message)
    questions = parse_generated(response)
    if not questions:
        raise background_runs.RunError("Das Modell hat keine brauchbaren Fragen geliefert.")
    return {"questions": questions, "provider_id": provider.get("id"), "model": provider.get("model") or "",
            "context_days": int(params.get("days") or 0)}


background_runs.register(RUN_KIND, generate_handler)


# ─── HTTP ────────────────────────────────────────────────────────────

def _guard(write=False):
    import screensaver

    user, error = screensaver._browser_user()
    if error:
        return None, error
    if write:
        csrf = screensaver._csrf_error()
        if csrf is not None:
            return None, csrf
    return user, None


@questions_bp.route("", methods=["GET"])
def get_questions():
    user, error = _guard()
    if error:
        return error
    data = load(user["id"])
    providers = [{"id": item.get("id"), "label": item.get("label") or item.get("id"),
                  "model": item.get("model") or ""} for item in local_providers(_main().load_config())]
    response = jsonify({**data, "providers": providers})
    response.headers["Cache-Control"] = "no-store"
    return response


@questions_bp.route("/providers/<provider_id>/models", methods=["GET"])
def provider_models(provider_id):
    """Live-Modell-Liste eines lokalen Anbieters - nur ``lm_*`` im privaten Netz."""
    import journal_analysis_providers as providers

    user, error = _guard()
    if error:
        return error
    provider = next((item for item in local_providers(_main().load_config())
                     if item.get("id") == provider_id), None)
    if provider is None:
        return jsonify({"error": "Unbekannter oder nicht lokaler Anbieter"}), 404
    try:
        models = providers.discover_models(provider)
    except providers.ProviderDiscoveryError as exc:
        return jsonify({"error": str(exc)}), 502
    response = jsonify({"provider_id": provider_id, "models": models,
                        "configured": str(provider.get("model") or "").strip()})
    response.headers["Cache-Control"] = "no-store"
    return response


@questions_bp.route("", methods=["PUT"])
def put_questions():
    user, error = _guard(write=True)
    if error:
        return error
    return jsonify(save(user["id"], request.get_json(silent=True) or {}))


@questions_bp.route("/generate", methods=["POST"])
def generate():
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    params = {
        "provider_id": str(data.get("provider_id") or ""),
        "instruction": str(data.get("instruction") or "")[:MAX_INSTRUCTION],
        "model": str(data.get("model") or "")[:MAX_MODEL],
        "count": data.get("count") if isinstance(data.get("count"), int) else 5,
        "days": data.get("days") if isinstance(data.get("days"), int) else 0,
    }
    try:
        record = background_runs.start(user["id"], RUN_KIND, params, label="Fragen generieren")
    except background_runs.RunConflict as exc:
        return jsonify({"error": str(exc)}), 409
    except background_runs.RunError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run": background_runs.public_run(record)}), 202


__all__ = ["questions_bp", "load", "save", "clean_sets", "clean_flow", "clean_question", "clean_prompts",
           "parse_generated", "journal_context", "generate_handler", "RUN_KIND"]
