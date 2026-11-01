#!/usr/bin/env python3
"""Trefferqualitaet der semantischen Suche messen — nur Zahlen, nie Inhalte.

Liest ein Eval-Set (``semantic-eval.json``, Format in
``docs/project/Anforderung-Semantische-Suche-RAG.md`` Anhang A), stellt jede
Frage an ``semantic_search.search`` in den Modi Wort, Semantisch und Hybrid und
gibt je Modus und Fragetyp Recall@k, Hit@k und MRR aus. Fragen, Pfade und
Ueberschriften erscheinen in keiner Ausgabe: das Ergebnis darf weitergegeben
werden, das Eval-Set selbst bleibt auf dem Server.

Aufruf im App-Container::

    python /app/scripts/semantic-eval.py --user <name-oder-id> [--file PFAD] [--k 10]

Ohne ``--file`` liegt das Set unter ``DATA_DIR/<user-id>/.config/semantic-eval.json``.
"""

import argparse
import json
import re
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"
sys.path.insert(0, str(APP_DIR if APP_DIR.is_dir() else ROOT_DIR))

import brain  # noqa: E402
import main  # noqa: E402
import semantic_search  # noqa: E402
import semantic_settings  # noqa: E402


MODES = ("word", "semantic", "hybrid")


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:200]


def matches(hit, expected):
    if hit.get("path") != expected.get("path"):
        return False
    heading = _clean(expected.get("heading"))
    return not heading or _clean(hit.get("heading")) == heading


def score_item(results, expected, k):
    """Recall@k, Hit@k und reziproker Rang des ersten Treffers fuer eine Frage."""
    top = results[:k]
    found = [any(matches(hit, item) for hit in top) for item in expected]
    first = next((rank for rank, hit in enumerate(results, 1) if any(matches(hit, item) for item in expected)), 0)
    return {
        "recall": sum(found) / len(expected),
        "hit": 1.0 if any(found) else 0.0,
        "rr": 1.0 / first if first else 0.0,
    }


def evaluate(user_id, items, k, search=semantic_search.search):
    documents, _pending = brain._visible_documents(user_id)
    sources = list(semantic_settings.SOURCES)
    report = {mode: {} for mode in MODES}
    semantic_failures = set()
    for item in items:
        kind = str(item.get("type") or "other")
        expected = [entry for entry in item.get("expected") or [] if isinstance(entry, dict)]
        for mode in MODES:
            payload = search(user_id, str(item.get("query") or ""), sources=sources, mode=mode,
                             limit=max(k, 50), documents=documents)
            if payload["semantic"]["status"] == "unavailable":
                semantic_failures.add(payload["semantic"]["reason"])
            bucket = report[mode].setdefault(kind, {"n": 0, "recall": 0.0, "hit": 0.0, "rr": 0.0, "empty": 0})
            bucket["n"] += 1
            if not expected:
                # Negativfrage: gut ist, wenn nichts kommt.
                bucket["empty"] += int(not payload["results"])
                continue
            for key, value in score_item(payload["results"], expected, k).items():
                bucket[key] += value
    return report, sorted(semantic_failures)


def format_report(report, k, failures):
    lines = ["Modus     Typ             n   Recall@{k}  Hit@{k}  MRR    leer".format(k=k)]
    for mode in MODES:
        for kind, bucket in sorted(report[mode].items()):
            n = bucket["n"] or 1
            if kind == "negative":
                lines.append("{:<9} {:<15} {:>2}   {:>8}  {:>6}  {:>5}  {:>4}".format(
                    mode, kind, bucket["n"], "-", "-", "-", bucket["empty"]))
                continue
            lines.append("{:<9} {:<15} {:>2}   {:>8.2f}  {:>6.2f}  {:>5.2f}  {:>4}".format(
                mode, kind, bucket["n"], bucket["recall"] / n, bucket["hit"] / n, bucket["rr"] / n, "-"))
    for reason in failures:
        lines.append("Semantische Seite nicht verfügbar: " + reason)
    return "\n".join(lines)


def _user_id(name):
    for user in main.get_all_users():
        if name in (user.get("id"), user.get("username")):
            return user["id"]
    raise SystemExit("Unbekannter Nutzer.")


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", required=True)
    parser.add_argument("--file")
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()
    user_id = _user_id(args.user)
    path = Path(args.file) if args.file else main.DATA_DIR / user_id / ".config" / "semantic-eval.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("items") or []
    except (OSError, ValueError, AttributeError) as exc:
        raise SystemExit("Eval-Set nicht lesbar: {}".format(exc.__class__.__name__))
    report, failures = evaluate(user_id, items, max(1, args.k))
    print(format_report(report, max(1, args.k), failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
