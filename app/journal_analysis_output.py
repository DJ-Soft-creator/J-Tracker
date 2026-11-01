"""Ausgabemodi der Journal-Analyse (JA-08).

Ein Analyse-Ergebnis kann zusätzlich zur Benachrichtigung

- in das **Tagesjournal** geschrieben werden (ahead of the hashtag footer,
  damit der Footer am Dateiende bleibt), oder
- als **Notiz** an ein ausdrücklich gewähltes **Familiendokument** angehängt
  werden (landet im freien ``notes_after``-Bereich des Familienformats).

Beide Wege sind **idempotent je Lauf**: ein markierter Block
(``<!-- jt:analysis:start job="…" date="…" -->``) wird bei einem erneuten Lauf
für denselben Job und Tag ersetzt statt doppelt angehängt. Geschrieben wird
atomar über ``scheduling`` bzw. den Journal-Schreibpfad aus ``main`` — nie mit
``open(..., 'w')``.
"""

import re
import sys
from datetime import date
from pathlib import Path

import journal_analysis_sources as sources
import scheduling
import tagging


ANALYSIS_START = "<!-- jt:analysis:start"
ANALYSIS_END = "<!-- jt:analysis:end -->"
FAMILY_DIRNAME = "family"


def _day_string(day):
    if hasattr(day, "strftime"):
        return day.strftime("%Y-%m-%d")
    text = str(day)
    date.fromisoformat(text)
    return text


def _block_pattern(job_id, day):
    return re.compile(
        rf'<!-- jt:analysis:start job="{re.escape(str(job_id))}" date="{re.escape(_day_string(day))}" -->'
        r".*?<!-- jt:analysis:end -->\n?",
        re.DOTALL,
    )


def render_block(job_id, job_name, day, result):
    """Der markierte Block, den ein Lauf in Journal oder Familie hinterlässt."""
    header = job_name or "Journal-Analyse"
    body = (result or "").strip() or "(kein Text)"
    return (f'<!-- jt:analysis:start job="{job_id}" date="{_day_string(day)}" -->\n'
            f"## Journal-Analyse: {header}\n\n{body}\n{ANALYSIS_END}\n")


def strip_block(content, job_id, day):
    """Einen früheren Block desselben Jobs und Tages entfernen."""
    return _block_pattern(job_id, day).sub("", content or "")


def prepare_journal_body(current, job_id, job_name, day, result):
    """Journal-Inhalt vorbereiten: alten Block ersetzen, neuen vor den Footer.

    Rückgabe ist ``(neuer Text ohne Footer, Footer-Payload)`` — der Aufrufer
    baut den Footer über ``tagging.refresh_journal_footer`` neu auf.
    """
    body, footer = tagging.strip_footer(current)
    body = strip_block(body, job_id, day)
    updated = body.rstrip() + "\n\n" + render_block(job_id, job_name, day, result) + "\n"
    return updated, footer


def _main_module():
    return sys.modules.get("main") or sys.modules.get("__main__")


def write_journal_entry(user_id, day, job, result, *, data_root, writer=None,
                        refresh_footer=None):
    """Das Ergebnis in den Tag schreiben, auf den sich die Analyse bezieht."""
    if not (result or "").strip():
        return None
    path = sources.journal_path(user_id, day, data_root)
    job_id = job.get("id")
    job_name = job.get("name") or ""
    day_string = _day_string(day)
    if writer is None:
        module = _main_module()
        if module is None or not hasattr(module, "_update_journal_file"):
            raise RuntimeError("Journal-Schreibpfad ist nicht verfügbar")
        writer = module._update_journal_file
    if refresh_footer is None:
        refresh_footer = tagging.refresh_journal_footer

    def updater(current):
        updated, footer = prepare_journal_body(current, job_id, job_name, day, result)
        return refresh_footer(user_id, updated, day_string, footer)

    return writer(path, updater, user_id=user_id)


NOTES_DIRNAME = "notes"


def _run_block_pattern(job_id, period):
    return re.compile(
        rf'<!-- jt:analysis-run:start job="{re.escape(str(job_id))}" period="{re.escape(period)}" -->'
        r".*?<!-- jt:analysis-run:end -->\n?",
        re.DOTALL,
    )


def render_run_block(job_id, job_name, period, body, *, marker=None):
    header = job_name or "Journal-Analyse"
    text = (body or "").strip() or "(kein Text)"
    marker = marker or period
    return (f'<!-- jt:analysis-run:start job="{job_id}" period="{marker}" -->\n'
            f"## {header} · {period}\n\n{text}\n<!-- jt:analysis-run:end -->\n")


def notes_document_path(user_id, job, data_root):
    """Ziel in ``<user>/notes/`` — relative Angabe oder ``<slug>.md``."""
    target = job.get("notes_target")
    if not target:
        target = f"{job.get('slug') or 'journal-analyse'}.md"
    text = str(target).strip()
    if text.startswith("/") or "\x00" in text:
        raise ValueError("Ungültiges Notiz-Ziel")
    parts = [part for part in text.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError("Ungültiges Notiz-Ziel")
    if Path(parts[-1]).suffix.lower() != ".md":
        raise ValueError("Das Notiz-Ziel muss eine Markdown-Datei sein")
    base = Path(data_root) / str(user_id) / NOTES_DIRNAME
    candidate = base.joinpath(*parts)
    try:
        candidate.resolve().relative_to(base.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError("Das Notiz-Ziel liegt außerhalb des Notizordners") from exc
    return candidate


def write_notes_entry(user_id, job, days, body, *, data_root):
    """Die Zeitraum-Zusammenfassung als Notiz ablegen (idempotent je Job+Zeitraum)."""
    if not (body or "").strip():
        return None
    if not days:
        return None
    period = days[0].isoformat() if len(days) == 1 else f"{days[0].isoformat()} – {days[-1].isoformat()}"
    period_key = f"{days[0].isoformat()}_{days[-1].isoformat()}"
    path = notes_document_path(user_id, job, data_root)
    job_id = job.get("id")
    job_name = job.get("name") or ""

    def updater(current):
        stripped = _run_block_pattern(job_id, period_key).sub("", current or "")
        return (stripped.rstrip() + "\n\n"
                + render_run_block(job_id, job_name, period, body, marker=period_key) + "\n")

    scheduling.update_text_file(path, updater)
    return path


def family_document_path(target, data_root):
    """Ein Ziel relativ zum Familienordner auflösen — oder laut scheitern."""
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Kein Familien-Ziel angegeben")
    text = target.strip()
    if text.startswith(("familie/", "family/")):
        text = text.split("/", 1)[1]
    if text.startswith(("/", "\\")) or "\x00" in text:
        raise ValueError("Ungültiges Familien-Ziel")
    parts = [part for part in text.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError("Ungültiges Familien-Ziel")
    if Path(parts[-1]).suffix.lower() != ".md":
        raise ValueError("Das Familien-Ziel muss eine Markdown-Datei sein")
    base = Path(data_root) / FAMILY_DIRNAME
    candidate = base.joinpath(*parts)
    try:
        candidate.resolve().relative_to(base.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError("Das Familien-Ziel liegt außerhalb des Familienordners") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("Das Familien-Ziel existiert nicht")
    return candidate


def write_family_note(target, job, day, result, *, data_root):
    """Das Ergebnis als Notiz an ein Familiendokument anhängen (idempotent)."""
    if not (result or "").strip():
        return None
    path = family_document_path(target, data_root)
    job_id = job.get("id")
    job_name = job.get("name") or ""

    def updater(current):
        body = strip_block(current, job_id, day)
        return body.rstrip() + "\n\n" + render_block(job_id, job_name, day, result) + "\n"

    scheduling.update_text_file(path, updater)
    return path


__all__ = [
    "ANALYSIS_START", "ANALYSIS_END", "NOTES_DIRNAME", "render_block", "strip_block",
    "prepare_journal_body", "write_journal_entry", "family_document_path",
    "write_family_note", "notes_document_path", "render_run_block", "write_notes_entry",
]