"""Chatverlaeufe von „Chat with your Notes" als Markdown im Tagesordner.

AI maintenance contract: dieses Modul besitzt Format, Pfad und Lebenszyklus der
Chatdatei. Modellaufruf und Werkzeugschleife bleiben in brain_chat.py.

Warum im Tagesordner: ein Chat ueber das eigene Journal gehoert zum Tag, an dem
er gefuehrt wurde — neben ``Journal_<datum>.md``, nicht in einem Sonderordner,
den man nur findet, wenn man ihn kennt. Die Datei entsteht mit der ersten
Antwort und waechst mit jeder weiteren; ein gesperrtes Handy kostet den Verlauf
damit nicht mehr. Verworfen wird sie nur auf ausdruecklichen Wunsch.

``brain_documents._personal_kind`` kennt diesen Pfad als Art ``chat``, damit die
Chats in der Brain-Suche auftauchen. Die Journalpfade selbst bleiben unberuehrt:
``tagging.journal_paths`` sucht ``Journal_*.md``, eine Chatdatei faellt dort nie
hinein.
"""

import json
import re
import sys
import uuid
from datetime import datetime

from scheduling import read_text_file, update_text_file, write_text_file

SESSION_TYPE = "brain-chat-session"
FILE_STEM = "Chat"
# Derselbe Aufbau wie der Journalpfad, nur mit anderem Dateinamen: Jahr, Monat,
# Tag als Ordner. Alles andere waere ein zweites Ablageschema fuer denselben Tag.
PATH_RE = re.compile(
    r"^(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"Chat_(?P=year)-(?P=month)-(?P=day)_\d{2}-\d{2}-\d{2}(?:_\d+)?\.md$"
)
_EVENT_RE = re.compile(
    r"<!-- jt:brain-chat-event (?P<meta>\{.*?\}) -->\n(?P<body>.*?)(?=\n___\n|\Z)",
    re.DOTALL,
)
MAX_STEPS_IN_MARKER = 20


def _core():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _now():
    core = _core()
    if core is not None and hasattr(core, "get_tz_aware_now"):
        return core.get_tz_aware_now()[0]
    return datetime.now().astimezone()


def _user_root(user_id):
    return _core().DATA_DIR / user_id


def is_session_path(relative):
    return bool(PATH_RE.match(relative or ""))


def resolve(user_id, relative):
    """Den Pfad einer bestehenden Sitzung fail-closed aufloesen."""
    if not is_session_path(relative):
        return None
    root = _user_root(user_id)
    path = root / relative
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file() or path.is_symlink():
        return None
    return path


def _relative(user_id, path):
    return path.relative_to(_user_root(user_id)).as_posix()


def _header(session_id, model, created_at):
    return (
        "---\n"
        "id: {}\n".format(session_id)
        + "type: {}\n".format(SESSION_TYPE)
        + "model: {}\n".format(model)
        + "created_at: {}\n".format(created_at)
        + "---\n\n"
        "# Chat mit den Notizen\n\n"
        "Beantwortet aus dem eigenen Journal, Modell `{}`.\n".format(model or "unbekannt")
    )


def _event(kind, at, body, extra=None):
    meta = {"schema": 1, "type": kind, "at": at}
    if extra:
        meta.update(extra)
    heading = "Frage" if kind == "question" else "Antwort"
    return (
        "\n___\n\n"
        "## {} | Datum & Uhrzeit: {}\n".format(heading, at)
        + "<!-- jt:brain-chat-event {} -->\n\n".format(json.dumps(meta, ensure_ascii=False, sort_keys=True))
        + "{}\n".format(body.strip())
    )


def _steps_text(steps):
    """Die Nachweise als lesbare Liste — die Datei soll ohne App verstaendlich sein."""
    lines = []
    for step in steps or []:
        parts = [str(step.get("tool") or "")]
        if step.get("error"):
            parts.append("Fehler: {}".format(step["error"]))
        elif isinstance(step.get("count"), int):
            parts.append("{} Treffer".format(step["count"]))
        hits = step.get("hits") or []
        if hits:
            parts.append(", ".join(str(hit) for hit in hits))
        lines.append("- {}".format(" · ".join(part for part in parts if part)))
    if not lines:
        return ""
    return "\n\nNachweise:\n" + "\n".join(lines)


def create(user_id, model):
    """Lege eine neue Sitzung im heutigen Tagesordner an."""
    now = _now()
    directory = _user_root(user_id) / now.strftime("%Y/%m/%d")
    stem = "{}_{}".format(FILE_STEM, now.strftime("%Y-%m-%d_%H-%M-%S"))
    path = directory / "{}.md".format(stem)
    suffix = 2
    while path.exists():
        path = directory / "{}_{}.md".format(stem, suffix)
        suffix += 1
    session_id = str(uuid.uuid4())
    write_text_file(path, _header(session_id, model, now.isoformat(timespec="seconds")))
    return {"id": session_id, "path": _relative(user_id, path), "created_at": now.isoformat(timespec="seconds")}


def append_turn(user_id, relative, question, answer, steps=None, *, model="", truncated=False):
    """Haenge Frage und Antwort an; ohne bestehende Sitzung entsteht eine neue.

    Angehaengt wird ausschliesslich — eine Antwort, die einmal in der Datei steht,
    wird von einem spaeteren Turn nie mehr angefasst.
    """
    session = None
    path = resolve(user_id, relative) if relative else None
    if path is None:
        session = create(user_id, model)
        path = resolve(user_id, session["path"])
    at = _now().isoformat(timespec="seconds")
    block = _event("question", at, question)
    block += _event(
        "answer", at, "{}{}".format(answer, _steps_text(steps)),
        {"steps": (steps or [])[:MAX_STEPS_IN_MARKER], "truncated": bool(truncated), "model": model},
    )
    update_text_file(path, lambda current: (current or "").rstrip("\n") + "\n" + block)
    return session or {"id": read_meta(path).get("id", ""), "path": _relative(user_id, path)}


def read_meta(path):
    meta = {}
    try:
        content = read_text_file(path) or ""
    except OSError:
        return meta
    if not content.startswith("---\n"):
        return meta
    end = content.find("\n---\n", 4)
    if end == -1:
        return meta
    for line in content[4:end].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            meta[key.strip()] = value.strip()
    return meta


def read_session(user_id, relative):
    """Den gespeicherten Verlauf zurueckgeben, damit der Browser ihn fortsetzen kann."""
    path = resolve(user_id, relative)
    if path is None:
        return None
    try:
        content = read_text_file(path) or ""
    except OSError:
        return None
    turns = []
    for match in _EVENT_RE.finditer(content):
        try:
            meta = json.loads(match.group("meta"))
        except json.JSONDecodeError:
            continue
        body = match.group("body").strip()
        if meta.get("type") == "question":
            turns.append({"role": "user", "content": body, "at": meta.get("at", "")})
        elif meta.get("type") == "answer":
            # Die Nachweise stehen als Liste im Text und strukturiert im Marker;
            # fuer die Anzeige zaehlt der Marker, der Text bleibt fuer Menschen.
            answer = body.split("\n\nNachweise:\n", 1)[0].strip()
            turns.append({
                "role": "assistant", "content": answer, "at": meta.get("at", ""),
                "steps": meta.get("steps") or [], "truncated": bool(meta.get("truncated")),
            })
    meta = read_meta(path)
    return {"path": relative, "id": meta.get("id", ""), "model": meta.get("model", ""), "turns": turns}


def discard(user_id, relative):
    """Loesche eine Sitzung endgueltig — nur auf ausdruecklichen Wunsch."""
    path = resolve(user_id, relative)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


__all__ = [
    "PATH_RE", "SESSION_TYPE", "append_turn", "create", "discard", "is_session_path",
    "read_meta", "read_session", "resolve",
]
