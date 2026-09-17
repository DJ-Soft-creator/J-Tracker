"""Family project Markdown: id validation, parser and serializer.

AI maintenance contract: pure content-string transforms only, no file IO and
no Flask. Split out of family.py purely to stay under the 1000-line limit
(tests/test_code_structure.py); family.py imports every name here back into
its own namespace so the facade (and everything that syncs from it, e.g.
family_content_routes.py, family_documents.py, brain_content.py) sees no
difference.
"""

import re
import uuid

import family_views

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _valid_id(value):
    return isinstance(value, str) and _SAFE_ID_RE.fullmatch(value) is not None


# ─── Projekt-Parser/Serializer ─────────────────────────────────────────
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)
_KV_RE = re.compile(r"^(\w+):\s*(.*)$")

_TASK_PREFIX_RE = re.compile(r"^-\s*\[(?P<check>[ xX])\]\s*(?P<body>.*)$")

_COMMENT_RE = re.compile(
    r'^-\s*id:\s*(?P<id>[^|]+?)\s*\|\s*'
    r'user:\s*(?P<user>[^|]*?)\s*\|\s*'
    r'text:\s*(?P<text>.*?)\s*\|\s*'
    r'created_at:\s*(?P<created_at>[^|]*?)\s*$'
)


def _is_shopping_project(project):
    """Whether a project uses the shopping-list representation."""
    return (
        project.get("template_id") == "einkaufsliste"
        or (project.get("target_file") or "").endswith("einkaufsliste.md")
    )


def _shopping_heading_title(title):
    """Return a shopping category for a Markdown heading, else ``None``.

    Older imports could store category lines (for example ``## Kühlung``) as
    ordinary tasks.  They are structural information, never purchasable
    items, and consequently must not receive a completion checkbox.
    """
    value = str(title or "").strip()
    heading = re.fullmatch(r"#{1,6}\s+(.+?)\s*", value)
    if heading:
        return heading.group(1).strip()
    bold_heading = re.fullmatch(r"\*\*(.+?)\*\*", value)
    if bold_heading:
        return bold_heading.group(1).strip()
    return None


def _normalize_shopping_heading_tasks(project):
    """Convert legacy heading-tasks into category metadata on real items."""
    if not _is_shopping_project(project):
        return

    category = ""
    tasks = []
    for task in project.get("tasks", []):
        heading = _shopping_heading_title(task.get("title"))
        if heading:
            category = heading
            continue
        if category and not task.get("gruppe"):
            task["gruppe"] = category
        tasks.append(task)
    project["tasks"] = tasks


def _parse_task_line(line):
    """Parse one extensible task line without losing optional metadata.

    Project files predate shopping categories, so task lines may contain old
    fields only or newer fields such as ``sort-index`` and ``gruppe``.  A
    key/value parser accepts both formats and fails closed for malformed or
    duplicate metadata.
    """
    match = _TASK_PREFIX_RE.match(line)
    if not match:
        return None
    values = {}
    for part in match.group("body").split("|"):
        if ":" not in part:
            return None
        key, value = part.split(":", 1)
        key = key.strip().casefold()
        if not key or key in values:
            return None
        values[key] = value.strip()
    required = {"id", "title", "user", "target-date"}
    if not required.issubset(values):
        return None
    task_id = values["id"]
    if not _valid_id(task_id):
        return None
    sort_index = values.get("sort-index", "")
    if sort_index and not sort_index.isdigit():
        return None
    return {
        "id": task_id,
        "title": values["title"],
        "user": values["user"],
        "target_date": values["target-date"],
        "completed": match.group("check").lower() == "x",
        "created_at": values.get("created_at", ""),
        "created_by": values.get("created_by", ""),
        "completed_at": values.get("completed_at", "") or None,
        "completed_by": values.get("completed_by", "") or None,
        "sort_index": int(sort_index) if sort_index else None,
        "gruppe": values.get("gruppe", ""),
        "source": "manual",
    }


def parse_project_content(content, file_name=None, repair_context=None):
    """Parst Projekt-Markdown aus einer bereits gesperrten Transaktion.

    ``repair_context`` (``{"user_id": ..., "now": iso_string}``) schaltet den
    Reparaturmodus frei: Aufgaben-/Kommentarzeilen, die keine Maschinenfelder
    tragen (von Hand im Markdown-Editor getippt), werden in gueltige
    Datensaetze umgewandelt statt das ganze Projekt fuer ungueltig zu
    erklaeren. Nur der raw-Schreibpfad nutzt das; alle anderen Aufrufer lesen
    weiterhin strikt, damit Task-Identitaeten zwischen zwei Lesevorgaengen
    stabil bleiben (Reparatur erzeugt neue IDs und darf daher nicht bei jedem
    Read neu passieren).
    """
    project = {
        "id": None,
        "title": None,
        "type": None,
        "template_id": None,
        "target_file": None,
        "assigned_users": [],
        "created_at": None,
        "created_by": None,
        "updated_at": None,
        "owner": None,
        "archived_at": None,
        "exclude_from_personal": None,
        "notes": "",
        "notes_after": "",
        "tasks": [],
        "comments": [],
        "file": file_name,
        "_frontmatter_valid": True,
        "_task_ids_valid": True,
    }

    # Frontmatter
    normalized = (content or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    fm_match = _FRONTMATTER_RE.match(normalized)
    body = normalized
    if fm_match:
        lines = fm_match.group(1).split("\n")
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#"):
                index += 1
                continue
            m = _KV_RE.match(line)
            if not m:
                project["_frontmatter_valid"] = False
                index += 1
                continue
            key, val = m.group(1), m.group(2).strip()
            if key == "assigned_users":
                v = val.strip()
                if v.startswith("["):
                    if not v.endswith("]"):
                        project["_frontmatter_valid"] = False
                        index += 1
                        continue
                    inner = v[1:-1].strip()
                    if inner:
                        parts = [p.strip().strip('"').strip("'") for p in inner.split(",")]
                        project["assigned_users"] = [p for p in parts if p]
                    else:
                        project["assigned_users"] = []
                elif v:
                    project["assigned_users"] = [v] if v else []
                else:
                    assigned = []
                    while index + 1 < len(lines):
                        list_match = re.match(r"^\s+-\s+(.+?)\s*$", lines[index + 1])
                        if not list_match:
                            break
                        assigned.append(list_match.group(1).strip().strip("'\""))
                        index += 1
                    project["assigned_users"] = [item for item in assigned if item]
            else:
                project[key] = val
            index += 1
        body = normalized[fm_match.end():]
    elif normalized.startswith("---"):
        project["_frontmatter_valid"] = False
        body = ""

    # Body parsen
    section = None
    seen_task_ids = set()
    notes_lines = []
    # Freier Text UNTER der Aufgabenliste. Er ging frueher verloren: alles in
    # der Aufgaben-Sektion, was keine Aufgabenzeile war, fiel beim naechsten
    # Speichern still aus der Datei. Notizen werden aber genau so geschrieben -
    # erst ein paar Aufgaben, dann Text dazu.
    tail_lines = []

    def _fresh_task_id():
        new_id = str(uuid.uuid4())
        while new_id in seen_task_ids:
            new_id = str(uuid.uuid4())
        return new_id

    for raw_line in body.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            header = stripped[3:].strip().lower()
            if "aufgabe" in header:
                section = "tasks"
                continue
            if "kommentar" in header:
                section = "comments"
                continue
            # Jede andere Ueberschrift ist eine Zwischenueberschrift im
            # Notiztext, kein Ablagefach. Sie verschwand frueher beim naechsten
            # Speichern - seit der Text im Familie-Tab sichtbar ist, faellt das
            # sofort auf.
            section = None
            notes_lines.append(line)
            continue

        if section is None:
            # Freier Text vor/zwischen den erkannten Ueberschriften: das ist
            # der eigentliche Notiz-Inhalt, kein Maschinenformat - wird
            # unveraendert (inkl. Leerzeilen fuer Absaetze) mitgefuehrt.
            notes_lines.append(line)
            continue

        if section == "tasks":
            # Leerzeilen wandern mit in den Puffer, damit Absaetze im
            # Schlusstext erhalten bleiben; fuehrende und abschliessende
            # verschwinden beim Zusammensetzen wieder.
            if not stripped:
                tail_lines.append(line)
                continue
            task = _parse_task_line(stripped)
            if task:
                task_id = task["id"]
                if task_id in seen_task_ids:
                    if repair_context:
                        task["id"] = _fresh_task_id()
                    else:
                        project["_task_ids_valid"] = False
                        continue
                seen_task_ids.add(task["id"])
                project["tasks"].append(task)
            elif stripped.startswith("- ["):
                prefix_match = _TASK_PREFIX_RE.match(stripped)
                title = prefix_match.group("body").strip() if prefix_match else ""
                if prefix_match and not title:
                    # Leere Checkbox ("- [ ]" ohne Titel) - z.B. ein Snippet
                    # wie "- [ ] " eingefuegt und noch nichts dahinter
                    # getippt. Traegt keine Information, ist also kein Fehler.
                    pass
                elif repair_context and prefix_match:
                    # Von Hand getippte Checkliste ohne Maschinenfelder: als
                    # neue Aufgabe uebernehmen statt das Projekt zu verwerfen.
                    title = title.replace("|", "/")
                    task_id = _fresh_task_id()
                    seen_task_ids.add(task_id)
                    project["tasks"].append({
                        "id": task_id,
                        "title": title,
                        "user": "",
                        "target_date": "",
                        "completed": prefix_match.group("check").lower() == "x",
                        "created_at": repair_context.get("now", ""),
                        "created_by": repair_context.get("user_id", ""),
                        "completed_at": None,
                        "completed_by": None,
                        "sort_index": None,
                        "gruppe": "",
                        "source": "manual",
                    })
                else:
                    project["_task_ids_valid"] = False
            else:
                # Keine Aufgabenzeile: freier Text, der unter der Liste steht.
                tail_lines.append(line)
            continue

        if not stripped:
            continue

        if section == "comments":
            m = _COMMENT_RE.match(stripped)
            if m:
                project["comments"].append({
                    "id": m.group("id").strip(),
                    "user": m.group("user").strip(),
                    "text": m.group("text").strip(),
                    "created_at": m.group("created_at").strip(),
                })
            elif repair_context:
                # Von Hand getippter Kommentar ohne Maschinenfelder - mit
                # oder ohne fuehrenden Bindestrich, beides ist gemeint.
                text = stripped[2:].strip() if stripped.startswith("- ") else stripped
                if text:
                    project["comments"].append({
                        "id": str(uuid.uuid4()),
                        "user": repair_context.get("user_id", ""),
                        "text": text,
                        "created_at": repair_context.get("now", ""),
                    })
            continue

    project["notes"] = "\n".join(notes_lines).strip("\n")
    project["notes_after"] = "\n".join(tail_lines).strip("\n")

    # Dateien aus der Zeit vor den Familie-Subtabs tragen kein `type`. Sie sind
    # Aufgabenlisten und muessen deshalb als `bucket` gelten, nicht verschwinden.
    project["type"] = family_views.normalize_type(project.get("type"))
    project["exclude_from_personal"] = _exclude_from_personal(project)
    _normalize_shopping_heading_tasks(project)
    return project


def _exclude_from_personal(project):
    """Whether this document's tasks stay out of the aggregate buckets.

    Einkaufslisten-Artikel bekommen beim Import den Ersteller als `user` und
    wuerden sonst geschlossen in "Meine Aufgaben" landen. Sie sind aber
    Artikel, keine Aufgaben — deshalb ist eine Einkaufsliste standardmaessig
    ausgeschlossen. Ein explizit gesetztes Feld gewinnt immer.
    """
    raw = project.get("exclude_from_personal")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip():
        return raw.strip().casefold() in ("true", "1", "yes", "ja")
    return _is_shopping_project(project)


def serialize_project(project):
    """Serialisiert ein Projekt-dict in MD-String (Frontmatter + Body)."""
    lines = ["---"]
    lines.append(f"id: {project.get('id') or ''}")
    lines.append(f"title: {project.get('title') or ''}")
    lines.append(f"type: {family_views.normalize_type(project.get('type'))}")
    lines.append(f"template_id: {project.get('template_id') or ''}")
    lines.append(f"target_file: {project.get('target_file') or ''}")
    au = project.get("assigned_users") or []
    if au:
        lines.append("assigned_users: [" + ", ".join(au) + "]")
    else:
        lines.append("assigned_users: []")
    lines.append(f"created_at: {project.get('created_at') or ''}")
    lines.append(f"created_by: {project.get('created_by') or ''}")
    # Immer schreiben, damit die Wahl stabil bleibt: waere das Feld nur bei
    # `true` vorhanden, wuerde ein abgewaehlter Ausschluss beim naechsten Lesen
    # wieder auf den Einkaufslisten-Standard zurueckfallen.
    lines.append(f"exclude_from_personal: {'true' if _exclude_from_personal(project) else 'false'}")
    # Optionale Felder nur schreiben, wenn gesetzt: bestehende Dateien sollen
    # sich beim ersten Speichern nicht um leere Zeilen aufblaehen.
    if project.get("updated_at"):
        lines.append(f"updated_at: {project.get('updated_at')}")
    if project.get("owner"):
        lines.append(f"owner: {project.get('owner')}")
    if project.get("archived_at"):
        lines.append(f"archived_at: {project.get('archived_at')}")
    lines.append("---")
    lines.append("")
    notes = (project.get("notes") or "").strip("\n")
    if notes:
        lines.extend(notes.split("\n"))
        lines.append("")
    lines.append("## Aufgaben")
    for t in project.get("tasks", []):
        check = "x" if t.get("completed") else " "
        parts = [
            f"id: {t.get('id', '')}",
            f"title: {t.get('title', '')}",
            f"user: {t.get('user', '')}",
            f"target-date: {t.get('target_date', '')}",
        ]
        if t.get("created_at"):
            parts.append(f"created_at: {t.get('created_at')}")
        if t.get("created_by"):
            parts.append(f"created_by: {t.get('created_by')}")
        if t.get("completed_at"):
            parts.append(f"completed_at: {t.get('completed_at')}")
        if t.get("completed_by"):
            parts.append(f"completed_by: {t.get('completed_by')}")
        if t.get("sort_index") is not None:
            parts.append(f"sort-index: {t.get('sort_index')}")
        if t.get("gruppe"):
            parts.append(f"gruppe: {t.get('gruppe')}")
        lines.append(f"- [{check}] " + " | ".join(parts))
    notes_after = (project.get("notes_after") or "").strip("\n")
    if notes_after:
        lines.append("")
        lines.extend(notes_after.split("\n"))
    lines.append("")
    lines.append("## Kommentare")
    for c in project.get("comments", []):
        lines.append(
            f"- id: {c.get('id', '')} | user: {c.get('user', '')} | "
            f"text: {c.get('text', '')} | created_at: {c.get('created_at', '')}"
        )
    lines.append("")
    return "\n".join(lines)
