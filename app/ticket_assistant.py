"""Ticket-Assistent: Produktfragmente ueber mehrere Tage zusammentragen.

Der Product Owner arbeitet in kurzen Zeitfenstern; Anforderungen entstehen als
verstreute Fragmente in vielen Tagesjournalen
(``docs/project/Anforderung-Ticket-Assistent.md``). Dieses Modul sammelt sie
anhand der Hashtags ein, die ohnehin gesetzt werden, laesst sie vom Modell zu
Vorhaben gruppieren und nach **festen Kriterien** bewerten (E-13).

Der Lauf **schlaegt nur vor**. Ob aus einem Vorhaben eine Themenmappe oder ein
Ticket wird, entscheidet der Product Owner — konsistent mit dem
Verarbeitungsvertrag, nach dem nichts ungefragt zum Auftrag wird.

Ausgefuehrt wird der Assistent vorerst als fest eingebauter Lauf (E-42); sobald
die Job-Verwaltung des Worker-Bereichs steht, wird daraus eine Job-Definition.
"""

import json
import re
import sys

from scheduling import read_text_file


# Hashtags, mit denen der Product Owner Produktaussagen ohnehin markiert.
FRAGMENT_TAGS = (
    "jtracker", "feature-request", "feature-beschreibung", "optimierung",
    "it-idee", "aufgabe", "frage", "bug",
)
# Feste Reifekriterien (E-13): nachvollziehbar statt Bauchgefuehl.
CRITERIA = {
    "ziel": "Ist erkennbar, was erreicht werden soll?",
    "bereich": "Ist benannt, welcher Bereich der App betroffen ist?",
    "abgrenzung": "Ist erkennbar, was NICHT dazugehört oder wo die Grenze liegt?",
}
MAX_FRAGMENTS = 120
MAX_FRAGMENT_LENGTH = 1200
_DATE_RE = re.compile(r"Journal_(\d{4}-\d{2}-\d{2})\.md$")


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def collect_fragments(user_id, start_date, end_date):
    """Journalbloecke mit Produkt-Hashtags im Zeitraum, aeltester zuerst."""
    import tagging as tagging_module

    fragments = []
    for path in tagging_module.journal_paths(user_id):
        match = _DATE_RE.search(path.name)
        if not match or not start_date <= match.group(1) <= end_date:
            continue
        day = match.group(1)
        try:
            body, _ = tagging_module.strip_footer(read_text_file(path))
        except OSError:
            continue
        for block in tagging_module.journal_blocks(body, day):
            tags = set(block.get("raw_tags") or [])
            if not tags & set(FRAGMENT_TAGS):
                continue
            fragments.append({
                "date": day,
                "anchor": block["anchor"],
                "tags": sorted(tags),
                "text": block["text"][:MAX_FRAGMENT_LENGTH],
            })
    fragments.sort(key=lambda entry: entry["anchor"])
    return fragments[:MAX_FRAGMENTS]


def build_prompt(fragments):
    criteria = "\n".join(f"- {key}: {question}" for key, question in CRITERIA.items())
    numbered = "\n\n".join(
        f"[{index}] {entry['date']} ({', '.join(entry['tags'])})\n{entry['text']}"
        for index, entry in enumerate(fragments, start=1))
    return (
        "Du sortierst verstreute Produktnotizen aus einem Journal.\n\n"
        "Fasse Notizen, die zum selben Vorhaben gehören, zu einem Vorhaben zusammen. "
        "Notizen ohne gemeinsames Thema bleiben je ein eigenes Vorhaben. "
        "Erfinde nichts dazu.\n\n"
        "Bewerte jedes Vorhaben nach genau diesen Kriterien mit true oder false:\n"
        f"{criteria}\n\n"
        "Antworte ausschließlich mit JSON in dieser Form:\n"
        '{"vorhaben": [{"titel": "kurzer Name", "fragmente": [1, 4], '
        '"kriterien": {"ziel": true, "bereich": false, "abgrenzung": false}, '
        '"begruendung": "ein Satz"}]}\n\n'
        "Die Zahlen in \"fragmente\" sind die Nummern in eckigen Klammern.\n\n"
        f"Notizen:\n\n{numbered}\n"
    )


def parse_response(value, fragments):
    """Modellantwort in Vorschlaege uebersetzen; unbekannte Nummern fliegen raus."""
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Das Modell hat kein verwertbares JSON geliefert: {error}")
    entries = payload.get("vorhaben") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Die Antwort enthält kein Feld 'vorhaben'")

    proposals = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("titel") or "").strip()[:80]
        if not title:
            continue
        numbers = [value for value in (entry.get("fragmente") or [])
                   if isinstance(value, int) and 1 <= value <= len(fragments)]
        if not numbers:
            continue
        raw = entry.get("kriterien") if isinstance(entry.get("kriterien"), dict) else {}
        criteria = {key: bool(raw.get(key)) for key in CRITERIA}
        proposals.append({
            "title": title,
            "criteria": criteria,
            # Reif ist nur, was alle Kriterien erfüllt — kein Ermessen.
            "mature": all(criteria.values()),
            "reason": str(entry.get("begruendung") or "").strip()[:300],
            "fragments": [
                {"date": fragments[number - 1]["date"],
                 "anchor": fragments[number - 1]["anchor"],
                 "text": fragments[number - 1]["text"][:280]}
                for number in sorted(set(numbers))
            ],
        })
    return proposals


def run_scan(user_id, start_date, end_date, provider, call_ai):
    """Fragmente sammeln, gruppieren, bewerten. Aendert nichts."""
    fragments = collect_fragments(user_id, start_date, end_date)
    if not fragments:
        return {"fragments": 0, "proposals": [], "note": "Keine Produktnotizen im Zeitraum."}
    response = call_ai(provider, {}, build_prompt(fragments))
    proposals = parse_response(response, fragments)
    return {
        "fragments": len(fragments),
        "proposals": proposals,
        "mature": sum(1 for entry in proposals if entry["mature"]),
    }
