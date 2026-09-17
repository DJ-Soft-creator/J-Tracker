"""Brain journal health counters.

AI maintenance contract:
Own the numbers that make silent gaps visible - Markdown files the index never
sees, journal days that only resolve to a single whole-day block, and how much
of the journal actually carries hashtags. Read-only: nothing here writes a
file, a footer or an index. Public compatibility names are re-exported by
brain.py; import the facade from application code.
"""

import sys

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before domain modules are imported")
globals().update({name: getattr(_brain, name) for name in dir(_brain) if not name.startswith("__")})

# Nur so viele Pfade an die Oberflaeche geben, dass die Liste lesbar bleibt.
# Die Gesamtzahl steht daneben und ist die eigentliche Aussage.
_IGNORED_SAMPLE = 25


def _sync_from_facade():
    for name in ("DATA_DIR", "FAMILY_DIR"):
        if hasattr(_brain, name):
            globals()[name] = getattr(_brain, name)


def ignored_markdown_files(user_id, sample=_IGNORED_SAMPLE):
    """Markdown-Dateien im Datenordner, die der Index nie zu sehen bekommt.

    ``_scan_personal_documents`` filtert ueber ``_personal_kind``; jeder Pfad,
    der zu keinem bekannten Layout passt, wird ohne jede Spur uebersprungen.
    Genau so blieben 43 Journaldateien namens ``original_*.md`` monatelang
    unsichtbar - nicht bloss unverschlagwortet, sondern gar nicht vorhanden:
    nicht in der Suche, nicht in den Tags, nirgends. Eine Datei, die per Sync,
    Import oder Vertipper dort landet, verschwindet auf dieselbe Weise.
    """
    root = _user_root(user_id)
    if not root or not root.is_dir():
        return {"count": 0, "files": []}
    ignored = []
    for path in sorted(root.rglob("*.md")):
        if path.is_symlink() or not path.is_file() or _is_technical_file(path, root):
            continue
        relative = _relative_path(root, path)
        if not relative or _personal_kind(relative):
            continue
        ignored.append(relative)
    return {"count": len(ignored), "files": ignored[:sample]}


def tag_coverage(user_id, start_date="", end_date=""):
    """Zaehle, wie viel des Journals adressierbar ist und Hashtags traegt.

    ``blocks`` ist die Zahl der Bloecke, die ``journal_blocks()`` herleitet -
    also das, was ein Hashtag-Filter ueberhaupt treffen kann. ``whole_day_days``
    zaehlt die Tage, die nur als ein einziger Ganztag-Block aufloesen: dort gilt
    ein Hashtag fuer 24 Stunden statt fuer einen Eintrag. Der Fallback ist als
    Notnagel gedacht und sieht ohne diese Zahl wie ein Normalzustand aus.

    ``stamped_days`` sind Tage mit ``ai_run``. Die Zahl steht bewusst neben
    ``ai_blocks``: ein Tag kann als klassifiziert gestempelt sein und trotzdem
    keinen einzigen Block mit Modell-Hashtags haben - dann ist der Stempel
    aelter als die heutige Blockstruktur.
    """
    report = {
        "days": 0, "blocks": 0, "tagged_blocks": 0, "ai_blocks": 0,
        "whole_day_days": 0, "stamped_days": 0, "empty_days": 0,
    }
    for path in sorted(tagging_module.journal_paths(user_id)):
        journal_date = tagging_module.journal_date_from_path(path)
        if not journal_date:
            continue
        if (start_date and journal_date < start_date) or (end_date and journal_date > end_date):
            continue
        try:
            content = read_text_file(path)
        except OSError:
            continue
        body, footer = tagging_module.strip_footer(content)
        blocks = tagging_module.journal_blocks(body, journal_date)
        report["days"] += 1
        if not blocks:
            report["empty_days"] += 1
            continue
        mapped = footer.get("blocks", {}) if isinstance(footer, dict) else {}
        machine = tagging_module.footer_ai_tags(footer)
        report["blocks"] += len(blocks)
        report["tagged_blocks"] += sum(1 for block in blocks if mapped.get(block["anchor"]))
        report["ai_blocks"] += sum(1 for block in blocks if machine.get(block["anchor"]))
        if len(blocks) == 1 and blocks[0].get("scope") == "day":
            report["whole_day_days"] += 1
        if tagging_module.footer_ai_run(footer):
            report["stamped_days"] += 1
    return report


def journal_health(user_id, start_date="", end_date=""):
    """Beide Diagnosen in einer Antwort, damit die Oberflaeche einmal fragt."""
    report = tag_coverage(user_id, start_date, end_date)
    report["ignored"] = ignored_markdown_files(user_id)
    report["range"] = {"start": start_date, "end": end_date}
    return report


__all__ = ["ignored_markdown_files", "tag_coverage", "journal_health"]
