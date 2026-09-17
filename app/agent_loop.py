"""Die gemeinsame Werkzeugschleife fuer alle App-internen Agenten.

AI maintenance contract: dieses Modul kennt ausschliesslich den Dialog mit
einem OpenAI-kompatiblen ``/v1/chat/completions``-Endpunkt. Es weiss nichts
darueber, welche Werkzeuge es ausfuehrt, was sie tun duerfen oder wo ihre
Ergebnisse landen — das bringt der Aufrufer als ``Profile`` mit.

Herausgeloest aus brain_chat.py, als der Einkaufslisten-Agent dieselbe
Schleife brauchte. Eine zweite Kopie waere die schlechtere Wahl gewesen: die
Deckelungen hier (Runden, Nachrichtenlaenge, Werkzeugausgabe) sind der Grund,
warum ein Modell weder den Kontext flutet noch endlos zwischen Werkzeugen
pendelt, und sie muessen fuer jeden Agenten identisch gelten.
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Die Schleife ist gedeckelt, damit ein Modell weder den Kontext flutet noch
# endlos zwischen Werkzeugen pendelt. Nach MAX_ROUNDS wird eine Antwort ohne
# weitere Werkzeuge erzwungen statt abgebrochen.
MAX_ROUNDS = 20
MAX_HISTORY_MESSAGES = 40
MAX_MESSAGE_CHARS = 16000
MAX_TOOL_RESULT_CHARS = 44000
REQUEST_TIMEOUT = 1900
MAX_TOKENS = 9200
TEMPERATURE = 0.3


class AgentError(RuntimeError):
    """Fehler, den die Oberflaeche dem Nutzer wortwoertlich zeigen darf."""


class Profile:
    """Was ein Agent mitbringen muss, damit ``run`` ihn ausfuehren kann.

    Eine Profilinstanz gehoert zu genau einem Lauf. Alles, was der Lauf an
    Bindung braucht — der Benutzer, bei Bedarf ein festgenageltes Dokument —
    steckt in der Instanz, nie in den Argumenten des Modells. So kann ein
    Modell ein Werkzeug benennen, aber niemals seinen Wirkungsbereich.
    """

    error_class = AgentError
    max_rounds = MAX_ROUNDS
    max_tokens = MAX_TOKENS
    temperature = TEMPERATURE
    # Werkzeugfehler dieser Typen sind fuer das Modell korrigierbar und gehen
    # als Werkzeugantwort zurueck, statt den Lauf zu beenden.
    recoverable_errors = ()

    def system_prompt(self):
        raise NotImplementedError

    def tool_specs(self):
        raise NotImplementedError

    def run_tool(self, name, arguments):
        raise NotImplementedError

    def step_summary(self, name, payload):
        """Ein kurzer, anzeigbarer Abriss dessen, was ein Werkzeug lieferte."""
        return {"tool": name}

    def post(self, settings, body):
        """Wie dieses Profil den Endpunkt erreicht.

        Ueberschreibbar, damit ein Agent den Modellaufruf in seinem eigenen
        Modul behaelt — dort ist er benannt, testbar und ersetzbar.
        """
        return post(settings, body, self.error_class)


def endpoint_url(settings):
    """Die Completions-URL einer Verbindung.

    Zwei Sorten Verbindung treffen hier aufeinander: die Chat-Einstellungen
    halten eine ``base_url`` und haengen den Pfad selbst an, ein Eintrag aus
    ``ai_providers`` bringt die vollstaendige ``api_url`` schon mit.
    """
    url = str(settings.get("url") or "").strip()
    return url or "{}/v1/chat/completions".format(settings["base_url"])


def configured(settings):
    """Ob eine Verbindung ueberhaupt aufgerufen werden kann."""
    return bool((settings.get("url") or settings.get("base_url")) and settings.get("model"))


def post(settings, body, error_class=AgentError):
    url = endpoint_url(settings)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings["api_key"]:
        headers["Authorization"] = "Bearer {}".format(settings["api_key"])
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST",
    )
    # Local endpoints must be reached directly; an inherited proxy cannot route
    # into a private network and answers with a misleading gateway error.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        if exc.code in (400, 404, 422) and "tool" in detail.casefold():
            raise error_class(
                "Das Modell oder der Endpunkt unterstuetzt keine Werkzeugaufrufe. "
                "Bitte ein Modell mit Tool-Calling waehlen. ({})".format(detail)
            ) from exc
        raise error_class("Der Endpunkt antwortete mit HTTP {}: {}".format(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise error_class("Der Endpunkt ist nicht erreichbar: {}".format(exc.reason)) from exc
    except TimeoutError as exc:
        raise error_class("Der Endpunkt hat nicht rechtzeitig geantwortet.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_class("Der Endpunkt hat keine gueltige Antwort geliefert.") from exc


def message_of(payload, error_class=AgentError):
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise error_class("Der Endpunkt hat keine Antwort geliefert.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise error_class("Der Endpunkt hat keine Antwort geliefert.")
    return message


def tool_calls_of(message):
    calls = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = None
        calls.append({
            "id": str(call.get("id") or "call_{}".format(len(calls))),
            "name": str(function.get("name") or ""),
            "arguments": arguments,
        })
    return calls


def tool_reply(call_id, text):
    return {"role": "tool", "tool_call_id": call_id, "content": text[:MAX_TOOL_RESULT_CHARS]}


def normalise_history(raw, error_class=AgentError):
    """Accept only the two roles the browser is allowed to replay."""
    if not isinstance(raw, list):
        raise error_class("Der Verlauf ist ungueltig.")
    history = []
    for item in raw[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        history.append({"role": role, "content": content[:MAX_MESSAGE_CHARS]})
    if not history or history[-1]["role"] != "user":
        raise error_class("Bitte eine Frage eingeben.")
    return history


def run(profile, history, settings, on_step=None):
    """Die Werkzeugschleife eines Profils ausfuehren.

    ``on_step`` sieht jeden Werkzeugschritt, sobald er fertig ist. Ein
    serverseitiger Lauf kann damit zeigen, woran das Modell gerade arbeitet,
    statt minutenlang nur „arbeitet" zu melden.
    """
    error_class = profile.error_class
    if not configured(settings):
        raise error_class(profile.unconfigured_message)
    messages = [{"role": "system", "content": profile.system_prompt()}] + list(history)
    tools = profile.tool_specs()
    steps = []
    truncated = False

    for round_number in range(profile.max_rounds + 1):
        body = {
            "model": settings["model"],
            "messages": messages,
            "max_tokens": profile.max_tokens,
            "temperature": profile.temperature,
        }
        last_round = round_number == profile.max_rounds
        if last_round:
            # Der Deckel ist erreicht: eine Antwort aus dem bereits Getanen
            # ist ehrlicher als ein Abbruch mitten in der Arbeit.
            truncated = True
        else:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        message = message_of(profile.post(settings, body), error_class)
        calls = tool_calls_of(message)
        if not calls:
            content = str(message.get("content") or "").strip()
            if not content:
                raise error_class("Das Modell hat keine Antwort geliefert.")
            return {"answer": content, "steps": steps, "truncated": truncated,
                    "model": settings["model"]}

        messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls"),
        })
        for call in calls:
            if call["arguments"] is None:
                invalid = {"tool": call["name"], "error": "ungueltige Argumente"}
                steps.append(invalid)
                if on_step:
                    on_step(invalid)
                messages.append(tool_reply(
                    call["id"], "Die Argumente waren kein gueltiges JSON. Bitte erneut versuchen.",
                ))
                continue
            try:
                payload = profile.run_tool(call["name"], call["arguments"])
            except profile.recoverable_errors as exc:
                # Fuer das Modell korrigierbar, also als Werkzeugantwort zurueck.
                failed = {"tool": call["name"], "error": str(exc)}
                steps.append(failed)
                if on_step:
                    on_step(failed)
                messages.append(tool_reply(call["id"], str(exc)))
                continue
            step = profile.step_summary(call["name"], payload)
            step["arguments"] = call["arguments"]
            steps.append(step)
            if on_step:
                on_step(step)
            messages.append(tool_reply(call["id"], json.dumps(payload, ensure_ascii=False)))

    raise error_class("Das Modell kam zu keiner Antwort.")  # pragma: no cover


__all__ = [
    "AgentError", "MAX_HISTORY_MESSAGES", "MAX_MESSAGE_CHARS", "MAX_ROUNDS",
    "MAX_TOKENS", "MAX_TOOL_RESULT_CHARS", "Profile", "REQUEST_TIMEOUT",
    "TEMPERATURE", "configured", "endpoint_url", "message_of",
    "normalise_history", "post", "run",
    "tool_calls_of", "tool_reply",
]
