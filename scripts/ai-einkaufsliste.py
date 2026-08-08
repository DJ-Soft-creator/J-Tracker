#!/usr/bin/env python3
"""Workflow for #ai-einkaufsliste, executed in the Journl container.

The host monitor invokes ``prepare`` before calling OpenCode and ``apply`` with
the OpenCode result afterwards. Both commands read one JSON object from stdin
and emit one JSON object to stdout.
"""

import json
import os
import sys
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value if isinstance(value, type(default)) else default


def _recent_shopping_lists():
    projects = DATA_DIR / "family" / "projects"
    if not projects.is_dir():
        return []
    lists = []
    for path in projects.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "einkaufsliste" not in content.casefold():
            continue
        tasks = []
        for line in content.splitlines():
            if "|title:" in line:
                title = line.split("|title:", 1)[1].split("|", 1)[0].strip()
                if title:
                    tasks.append(title)
        if tasks:
            lists.append({"file": path.name, "items": tasks[-80:]})
    return lists[-4:]


def _prepare(payload):
    user_id = str(payload.get("user_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not user_id or not text:
        raise ValueError("user_id and text are required")
    context = _read_json(DATA_DIR / "family" / "ai_context.json", {})
    prompt = {
        "workflow": "einkaufsliste",
        "request": text,
        "family_context": context,
        "recent_shopping_lists": _recent_shopping_lists(),
        "response_contract": {
            "journal_response": "Short, useful German summary for the journal.",
            "shopping_items": ["Optional new shopping-list item"],
        },
    }
    return {
        "workflow": "ai-einkaufsliste",
        "opencode_prompt": (
            "Erstelle aus diesem Kontext eine Einkaufsliste. Antworte ausschliesslich als JSON "
            "mit journal_response (String) und shopping_items (Liste von Strings).\n\n"
            + json.dumps(prompt, ensure_ascii=False)
        ),
    }


def _apply(payload):
    user_id = str(payload.get("user_id") or "").strip()
    raw_result = str(payload.get("opencode_result") or "").strip()
    if not user_id or not raw_result:
        raise ValueError("user_id and opencode_result are required")
    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError:
        return {"journal_response": raw_result, "changed_files": []}
    if not isinstance(result, dict):
        raise ValueError("OpenCode result must be a JSON object")
    response = str(result.get("journal_response") or "").strip()
    items = result.get("shopping_items") or []
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise ValueError("shopping_items must be a list of strings")
    cleaned = []
    for item in items:
        item = item.strip()
        if item and len(item) <= 160 and "\n" not in item and "|" not in item:
            cleaned.append(item)
    if cleaned:
        import family

        for item in cleaned[:100]:
            family.append_task_to_target_file("einkaufsliste.md", item, "", created_by=user_id)
    return {
        "journal_response": response or "Einkaufsliste aktualisiert.",
        "changed_files": ["family/projects/einkaufsliste.md"] if cleaned else [],
    }


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"prepare", "apply"}:
        raise SystemExit("Usage: ai-einkaufsliste.py prepare|apply < request.json")
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        result = _prepare(payload) if sys.argv[1] == "prepare" else _apply(payload)
        json.dump(result, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    except (ValueError, OSError) as exc:
        json.dump({"error": str(exc)}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
