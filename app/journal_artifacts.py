"""Read-only inventory of non-canonical journal side artifacts for Phase 11."""

from collections import Counter
import json
from pathlib import Path

from markdown.legacy_metadata import classify_legacy_metadata


def inspect_journal_artifacts(root, relative_path, content):
    """Classify current-revision markers and adjacent side files without reading them."""
    root = Path(root).resolve(strict=True)
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Journalpfad nicht erlaubt")
    journal = (root / relative).resolve(strict=False)
    try:
        journal.relative_to(root)
    except ValueError as error:
        raise ValueError("Journalpfad nicht erlaubt") from error
    markers = classify_legacy_metadata(content)
    valid = Counter(marker.kind for marker in markers if marker.valid)
    invalid = Counter(marker.kind for marker in markers if not marker.valid)
    backup = journal.parent / "_Backup"
    sidecar = journal.parent / ".journal-media"
    return {
        "relative_path": relative.as_posix(),
        "markers": {"valid": dict(sorted(valid.items())), "invalid": dict(sorted(invalid.items()))},
        "sidecars": {
            "backup_files": sum(1 for item in backup.glob(journal.stem + "_backup_*.bak") if item.is_file() and not item.is_symlink()) if backup.is_dir() else 0,
            "journal_media_metadata": sum(1 for item in sidecar.glob("*.json") if item.is_file() and not item.is_symlink()) if sidecar.is_dir() else 0,
        },
    }


def report_journal_artifacts(items):
    """Aggregate report data only; never include Markdown or sidecar contents."""
    marker_counts, invalid_counts = Counter(), Counter()
    backups = sidecars = 0
    for item in items:
        marker_counts.update(item["markers"]["valid"])
        invalid_counts.update(item["markers"]["invalid"])
        backups += item["sidecars"]["backup_files"]
        sidecars += item["sidecars"]["journal_media_metadata"]
    return {"summary": {"journals": len(items), "valid_markers": dict(sorted(marker_counts.items())),
                         "invalid_markers": dict(sorted(invalid_counts.items())), "backup_files": backups,
                         "journal_media_metadata": sidecars}, "journals": items}


def inspect_media_sidecars(root, relative_path, content):
    """Compare sidecars only with persisted-media markers that require one.

    Older ``jt:media`` markers deliberately have no ``media_path`` and were
    never accompanied by a sidecar.  They remain visible in the general
    artifact inventory but must not create a false sidecar alarm.
    """
    root = Path(root).resolve(strict=True)
    journal = (root / Path(relative_path)).resolve(strict=False)
    try:
        journal.relative_to(root)
    except ValueError as error:
        raise ValueError("Journalpfad nicht erlaubt") from error
    import re
    marker_ids = set()
    for raw in re.findall(r"<!--\s*jt:media\s+(\{.*?\})\s*-->", content or "", re.DOTALL):
        try:
            item = json.loads(raw)
            if (isinstance(item, dict) and isinstance(item.get("id"), str)
                    and isinstance(item.get("media_path"), str) and item["media_path"]):
                marker_ids.add(item["id"])
        except json.JSONDecodeError:
            continue
    sidecar_dir = journal.parent / ".journal-media"
    sidecar_ids = set()
    malformed = 0
    if sidecar_dir.is_dir() and not sidecar_dir.is_symlink():
        for path in sidecar_dir.glob("*.json"):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    sidecar_ids.add(item["id"])
                else:
                    malformed += 1
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                malformed += 1
    return {"relative_path": Path(relative_path).as_posix(), "marker_ids": len(marker_ids),
            "sidecar_ids": len(sidecar_ids), "missing_sidecars": sorted(marker_ids - sidecar_ids),
            "orphan_sidecars": sorted(sidecar_ids - marker_ids), "malformed_sidecars": malformed}
