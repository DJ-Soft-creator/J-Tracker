"""Markdown hashtag footer, catalog, and rebuildable reference index."""

import json
import os
import re
import threading
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

from scheduling import read_text_file, update_text_file, write_text_file


DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
FAMILY_DIR = DATA_DIR / "family"
INDEX_PATH = DATA_DIR / "indexes" / "hashtag_index.json"
FOOTER_START = '<!-- jt:hashtag-index:start schema="1" -->'
FOOTER_END = "<!-- jt:hashtag-index:end -->"
_FOOTER_RE = re.compile(
    r"\n?<!-- jt:hashtag-index:start schema=\"1\" -->\s*<!--\s*(?P<payload>.*?)\s*-->\s*"
    r"<!-- jt:hashtag-index:end -->\s*$",
    re.DOTALL,
)
_HASHTAG_RE = re.compile(r"(?<![\w#])#([\w-]+)", re.UNICODE)
_ANCHOR_RE = re.compile(
    r"Datum\s*&\s*Uhrzeit:\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})",
    re.IGNORECASE,
)
_LEGACY_QUICK_NOTE_RE = re.compile(r"(?m)^-\s*(\d{2}:\d{2}:\d{2})\s*$")
_QUICK_NOTE_RE = re.compile(r"(?m)^-\s*(\d{2})\.(\d{2})\.\s+(\d{2}:\d{2}:\d{2})\s*$")
_QUICK_NOTE_END_RE = re.compile(r"~~(?:\d{2}\.\d{2}\.\s+)?\d{2}:\d{2}:\d{2}~~")
# Pre-"Datum & Uhrzeit" journals stamped an entry as ``Time: 15:53:02``,
# ``**Time**: 19:24:43`` or a bare clock line. Both spellings only ever reach a
# file the current rules leave without a single block (see journal_blocks).
_LEGACY_TIME_RE = re.compile(r"(?:\*\*)?\bTime\b(?:\*\*)?\s*:\s*(?:\*\*)?\s*(\d{1,2}:\d{2}:\d{2})", re.IGNORECASE)
_LEGACY_CLOCK_LINE_RE = re.compile(r"(?m)^[\s>*_#-]*(\d{1,2}:\d{2}:\d{2})[\s*_]*$")
_JOURNAL_RE = re.compile(r"^(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/Journal_\d{4}-\d{2}-\d{2}\.md$")
_snapshot = None
_snapshot_lock = threading.Lock()


def normalise_tag(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.strip().lstrip("#").casefold()
    value = re.sub(r"\s+", "-", value)
    return value if re.fullmatch(r"[\w-]+", value, re.UNICODE) else ""


def _read_json(path, default):
    try:
        return json.loads(read_text_file(path)) if Path(path).exists() else default
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def _catalog_default():
    return {
        "version": 1,
        "canonical": [],
        "aliases": {},
        "proposals": [],
        "blocked": [],
        "ai_workflows": {},
        "knowledge_sources": {},
        "write_targets": {},
    }


def _catalog_path(user_id=None, family=False):
    return (FAMILY_DIR if family else DATA_DIR / user_id) / "hashtag_catalog.json"


def _valid_catalog(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("canonical", []), list)
        and isinstance(value.get("aliases", {}), dict)
        and isinstance(value.get("proposals", []), list)
        # ``blocked`` was added later, so a catalog written before it existed is
        # still valid and simply carries no ignore list yet.
        and isinstance(value.get("blocked", []), list)
        and isinstance(value.get("ai_workflows", {}), dict)
        and isinstance(value.get("knowledge_sources", {}), dict)
    )


def read_catalog(user_id=None, family=False):
    catalog = _read_json(_catalog_path(user_id, family), _catalog_default())
    if not _valid_catalog(catalog):
        return _catalog_default()
    # Fill keys that older catalog files predate, so every caller can read them
    # without repeating a default.
    return {**_catalog_default(), **catalog}


def _write_catalog(user_id, family, updater):
    path = _catalog_path(user_id, family)

    def update(content):
        try:
            catalog = json.loads(content) if content else _catalog_default()
        except json.JSONDecodeError:
            raise ValueError("Hashtag catalog is corrupt")
        if not _valid_catalog(catalog):
            raise ValueError("Hashtag catalog has an invalid schema")
        result = updater(catalog)
        catalog["version"] = 1
        # Classification belongs exclusively to AI workflows. Remove the short-lived
        # legacy catalog field rather than carrying it into future writes.
        catalog.pop("classifications", None)
        catalog["canonical"] = sorted({normalise_tag(tag) for tag in catalog["canonical"] if normalise_tag(tag)})
        catalog["aliases"] = {
            normalise_tag(alias): normalise_tag(target)
            for alias, target in catalog["aliases"].items()
            if normalise_tag(alias) and normalise_tag(target)
        }
        catalog["proposals"] = sorted({normalise_tag(tag) for tag in catalog["proposals"] if normalise_tag(tag)})
        catalog["blocked"] = sorted({normalise_tag(tag) for tag in catalog.get("blocked", []) if normalise_tag(tag)})
        catalog["ai_workflows"] = {
            normalise_tag(tag): workflow
            for tag, workflow in catalog.get("ai_workflows", {}).items()
            if normalise_tag(tag).startswith("ai-") and isinstance(workflow, dict)
        }
        catalog["knowledge_sources"] = {
            normalise_tag(tag): source for tag, source in catalog.get("knowledge_sources", {}).items()
            if normalise_tag(tag) and isinstance(source, dict)
        }
        catalog["write_targets"] = {
            normalise_tag(tag): target for tag, target in catalog.get("write_targets", {}).items()
            if normalise_tag(tag).startswith("schreibziel-") and isinstance(target, dict)
        }
        known_tags = set(catalog["canonical"]) | set(catalog["ai_workflows"]) | set(catalog["knowledge_sources"]) | set(catalog["write_targets"])
        # A deliberately configured hashtag always wins over a stale ignore
        # entry, otherwise an approved tag could stay invisible in the catalog.
        catalog["blocked"] = [tag for tag in catalog["blocked"] if tag not in known_tags and tag not in catalog["aliases"]]
        catalog["proposals"] = [
            tag for tag in catalog["proposals"]
            if tag not in known_tags and tag not in catalog["blocked"]
        ]
        return json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", result

    return update_text_file(path, update)


def propose_tags(user_id, tags, family=False):
    cleaned = {normalise_tag(tag) for tag in tags}
    cleaned.discard("")
    if not cleaned:
        return []

    def update(catalog):
        known = set(catalog.get("canonical", [])) | set(catalog.get("aliases", {})) | set(catalog.get("ai_workflows", {}))
        # Tags the user has deliberately ignored must never come back as a
        # proposal, no matter how often they appear in journal text again.
        known |= set(catalog.get("blocked", []))
        added = sorted(cleaned - known - set(catalog.get("proposals", [])))
        catalog["proposals"].extend(added)
        return added

    return _write_catalog(user_id, family, update)


def catalog_view(user_id):
    personal = read_catalog(user_id)
    family = read_catalog(family=True)
    personal_targets = personal.get("write_targets", {})
    family_targets = family.get("write_targets", {})
    return {
        "personal": personal,
        "family": family,
        "canonical": sorted(set(personal["canonical"]) | set(family["canonical"])),
        "ai": personal.get("ai_workflows", {}),
        # Keep the scopes separate.  A knowledge tag must be unambiguous for a
        # reader, so the write endpoint rejects duplicate names across these
        # two catalogs instead of relying on dictionary merge order.
        "knowledge": {
            "personal": {
                tag: value for tag, value in personal.get("knowledge_sources", {}).items()
                if value.get("scope", "personal") == "personal"
            },
            "family": {
                tag: value for tag, value in family.get("knowledge_sources", {}).items()
                if value.get("scope", "family") == "family"
            },
            "host": {
                tag: value for tag, value in personal.get("knowledge_sources", {}).items()
                if value.get("scope") == "host"
            },
        },
        "write_targets": {
            "personal": {tag: value for tag, value in personal_targets.items() if value.get("scope") == "personal"},
            "family": {tag: value for tag, value in family_targets.items() if value.get("scope") == "family"},
            "host": {tag: value for tag, value in personal_targets.items() if value.get("scope") == "host"},
        },
    }


def update_catalog(user_id, family, action, tag, target=""):
    tag = normalise_tag(tag)
    target = normalise_tag(target)
    if not tag:
        raise ValueError("A valid hashtag is required")
    def update(catalog):
        canonical = set(catalog["canonical"])
        aliases = dict(catalog["aliases"])
        proposals = set(catalog["proposals"])
        blocked = set(catalog.get("blocked", []))
        if action == "approve":
            canonical.add(tag)
            proposals.discard(tag)
            blocked.discard(tag)
        elif action == "remove":
            canonical.discard(tag)
            proposals.discard(tag)
            aliases = {alias: value for alias, value in aliases.items() if alias != tag and value != tag}
        elif action == "alias":
            if not target or target not in canonical or tag == target:
                raise ValueError("Alias target must be an approved canonical hashtag")
            aliases[tag] = target
            proposals.discard(tag)
            blocked.discard(tag)
        elif action == "remove_alias":
            aliases.pop(tag, None)
        elif action == "block":
            if tag in canonical or tag in aliases:
                raise ValueError("Freigegebene Hashtags müssen zuerst entfernt werden")
            if tag in catalog.get("ai_workflows", {}) or tag in catalog.get("knowledge_sources", {}) or tag in catalog.get("write_targets", {}):
                raise ValueError("Konfigurierte Hashtags müssen zuerst entfernt werden")
            blocked.add(tag)
            proposals.discard(tag)
        elif action == "unblock":
            if tag not in blocked:
                raise ValueError("Dieses Hashtag steht nicht auf der Ignorierliste")
            blocked.discard(tag)
            # Offer it again right away instead of waiting for the next journal
            # write to re-detect it.
            proposals.add(tag)
        else:
            raise ValueError("Unknown catalog action")
        catalog["canonical"] = sorted(canonical)
        catalog["aliases"] = aliases
        catalog["proposals"] = sorted(proposals)
        catalog["blocked"] = sorted(blocked)
        return catalog

    return _write_catalog(user_id, family, update)


def update_ai_workflow(user_id, action, tag, workflow=None):
    tag = normalise_tag(tag)
    if not tag.startswith("ai-") or len(tag) > 80:
        raise ValueError("AI hashtags must start with ai-")

    def update(catalog):
        workflows = dict(catalog.get("ai_workflows", {}))
        if action == "remove":
            workflows.pop(tag, None)
        elif action == "save":
            value = workflow or {}
            agent = str(value.get("agent") or "").strip().casefold()
            model = str(value.get("model") or "").strip()
            prompt = str(value.get("prompt") or "").strip()
            # ``block``/``files`` are retained for existing catalogs.  New
            # document sessions translate these to section/none at use time.
            context = str(value.get("context") or "section").strip().casefold()
            classification = str(value.get("classification") or "internal").strip().casefold()
            target = str(value.get("target") or "document").strip().casefold()
            provider_id = str(value.get("provider_id") or "").strip()
            context_files = value.get("context_files") or []
            default_hashtags = value.get("default_hashtags") or []
            share_inputs = value.get("share_inputs") or ["text", "url", "audio", "image", "file"]
            if agent not in {"codex", "opencode", "hermes", "pi", "custom"}:
                raise ValueError("Agent must be codex, opencode, hermes, pi, or custom")
            if not model or len(model) > 200 or any(char in model for char in "\r\n"):
                raise ValueError("A valid model is required")
            if not prompt or len(prompt) > 12000:
                raise ValueError("A prompt between 1 and 12000 characters is required")
            if context not in {"none", "section", "journal", "block", "files"}:
                raise ValueError("Context must be none, section, journal, block, or files")
            if classification not in {"public", "internal", "confidential", "secret"}:
                raise ValueError("Unknown AI hashtag classification")
            if target not in {"document", "write_tab", "daily_journal"}:
                raise ValueError("AI hashtag target must be document, write_tab, or daily_journal")
            if target == "daily_journal" and agent != "pi":
                raise ValueError("Share API daily_journal workflows require the Pi agent")
            if len(provider_id) > 120 or any(char in provider_id for char in "\r\n"):
                raise ValueError("A valid AI provider is required")
            if not isinstance(context_files, list) or any(not isinstance(path, str) for path in context_files):
                raise ValueError("context_files must be a list")
            if not isinstance(default_hashtags, list) or len(default_hashtags) > 30:
                raise ValueError("default_hashtags must be a list with at most 30 entries")
            cleaned_hashtags = []
            for raw_tag in default_hashtags:
                cleaned = normalise_tag(str(raw_tag).lstrip("#"))
                if not cleaned or cleaned.startswith("ai-") or cleaned in cleaned_hashtags:
                    continue
                cleaned_hashtags.append(cleaned)
            allowed_share_inputs = {"text", "url", "audio", "image", "file"}
            if (
                not isinstance(share_inputs, list) or not share_inputs
                or any(item not in allowed_share_inputs for item in share_inputs)
            ):
                raise ValueError("share_inputs contains an unsupported input type")
            cleaned_files = []
            for path in context_files:
                path = path.strip()
                candidate = Path(path)
                if path and (
                    len(path) > 240 or candidate.is_absolute() or ".." in candidate.parts
                    or candidate.suffix.casefold() != ".md"
                ):
                    raise ValueError("Context files must be relative Markdown paths")
                if path and path not in cleaned_files:
                    cleaned_files.append(path)
            workflows[tag] = {
                "agent": agent,
                "model": model,
                "prompt": prompt,
                "context": context,
                "classification": classification,
                "target": target,
                "provider_id": provider_id,
                "context_files": cleaned_files,
                "default_hashtags": cleaned_hashtags,
                "share_inputs": list(dict.fromkeys(share_inputs)),
            }
            catalog["proposals"] = [item for item in catalog.get("proposals", []) if normalise_tag(item) != tag]
        else:
            raise ValueError("Unknown AI workflow action")
        catalog["ai_workflows"] = workflows
        return workflows.get(tag)

    return _write_catalog(user_id, False, update)


def update_knowledge_source(user_id, family, action, tag, source=None):
    tag = normalise_tag(tag)
    if not tag or tag.startswith("ai-"):
        raise ValueError("A non-AI knowledge hashtag is required")

    # One visible tag may identify exactly one source.  In particular, do not
    # let a personal definition silently shadow a shared Family definition.
    other_sources = read_catalog(user_id, family=not family).get("knowledge_sources", {})
    if action == "save" and tag in other_sources:
        raise ValueError("This knowledge hashtag is already configured in the other scope")

    def update(catalog):
        sources = dict(catalog.get("knowledge_sources", {}))
        if action == "remove":
            sources.pop(tag, None)
        elif action == "save":
            value = source or {}
            path = str(value.get("path") or "").strip()
            kind = str(value.get("kind") or "reference").strip().casefold()
            description = str(value.get("description") or "").strip()
            root_id = str(value.get("root_id") or "").strip()
            if root_id:
                candidate = PurePosixPath(path)
                if (
                    family or not path or "\\" in path or not candidate.is_absolute()
                    or ".." in candidate.parts or candidate.as_posix() != path or len(path) > 500
                    or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", root_id)
                ):
                    raise ValueError("External knowledge source must be an absolute Linux directory below an allowed root")
            else:
                candidate = PurePosixPath(path)
                if (
                    not path or "\\" in path or candidate.is_absolute() or ".." in candidate.parts
                    or candidate.suffix != ".md" or candidate.as_posix() != path
                    or not candidate.parts or candidate.parts[0] not in {"notes", "projects"}
                ):
                    raise ValueError("Knowledge source must be a relative Markdown path")
            if kind not in {"reference", "constraints", "glossary", "examples"}:
                raise ValueError("Unknown knowledge source kind")
            if len(description) > 240 or "\n" in description or "\r" in description:
                raise ValueError("Knowledge source description must be a single line of at most 240 characters")
            sources[tag] = {
                "scope": "host" if root_id else "family" if family else "personal",
                "path": path, "kind": kind, "description": description,
            }
            if root_id:
                sources[tag].update({"root_id": root_id, "recursive": True})
        else:
            raise ValueError("Unknown knowledge source action")
        catalog["knowledge_sources"] = sources
        return sources.get(tag)
    return _write_catalog(user_id, family, update)


def update_write_target(user_id, family, action, tag, target=None):
    """Persist one explicitly granted directory tree for proposal-only AI edits."""
    tag = normalise_tag(tag)
    if not tag.startswith("schreibziel-") or len(tag) > 80:
        raise ValueError("Schreibziel-Hashtags müssen mit schreibziel- beginnen")
    other = read_catalog(user_id, family=not family)
    if action == "save" and (tag in other.get("write_targets", {}) or tag in other.get("knowledge_sources", {})):
        raise ValueError("Dieses Hashtag ist bereits im anderen Bereich konfiguriert")

    def update(catalog):
        targets = dict(catalog.get("write_targets", {}))
        if action == "remove":
            targets.pop(tag, None)
        elif action == "save":
            if tag in catalog.get("knowledge_sources", {}):
                raise ValueError("Dieses Hashtag ist bereits als Knowledge-Quelle konfiguriert")
            value = target or {}
            path = str(value.get("path") or "").strip().rstrip("/")
            policy = str(value.get("file_policy") or "markdown_only").strip().casefold()
            if policy not in {"markdown_only", "all_regular_files"}:
                raise ValueError("Unbekannte Schreibziel-Dateiregel")
            root_id = str(value.get("root_id") or "").strip()
            if root_id:
                candidate = PurePosixPath(path)
                if (not path or "\\" in path or not candidate.is_absolute() or ".." in candidate.parts
                        or candidate.as_posix() != path or len(path) > 500):
                    raise ValueError("Externes Schreibziel muss ein absoluter Linux-Pfad sein")
                if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", root_id):
                    raise ValueError("Ungültige externe Schreibziel-Wurzel")
                targets[tag] = {"scope": "host", "root_id": root_id, "path": path,
                                "recursive": True, "file_policy": policy}
            else:
                candidate = PurePosixPath(path)
                if (not path or "\\" in path or candidate.is_absolute() or ".." in candidate.parts
                        or candidate.as_posix() != path or not candidate.parts
                        or candidate.parts[0] not in {"notes", "projects"}):
                    raise ValueError("Schreibziel muss ein relativer Ordner unter notes oder projects sein")
                targets[tag] = {"scope": "family" if family else "personal", "path": path,
                                "recursive": True, "file_policy": policy}
        else:
            raise ValueError("Unknown write target action")
        catalog["write_targets"] = targets
        return targets.get(tag)
    return _write_catalog(user_id, family, update)


def canonical_tag(raw, catalogs):
    tag = normalise_tag(raw)
    if not tag:
        return ""
    for catalog in catalogs:
        if tag in catalog.get("ai_workflows", {}):
            return tag
        canonical = set(catalog.get("canonical", []))
        if tag in canonical:
            return tag
        alias = catalog.get("aliases", {}).get(tag)
        if alias in canonical:
            return alias
    return ""


def filterable_tag(raw, catalogs):
    """Resolve a hashtag usable as a Brain filter: canonical, else a proposal.

    Brain search must reach hashtags the user has not curated yet. Restricting
    filters to the canonical catalog made every fresh tag unfindable until it
    was approved, which is exactly the point at which one wants to look it up.
    Approval still decides colour and catalog membership, not searchability.
    """
    tag = normalise_tag(raw)
    if not tag:
        return ""
    canonical = canonical_tag(tag, catalogs)
    if canonical:
        return canonical
    for catalog in catalogs:
        if tag in set(catalog.get("proposals", [])):
            return tag
    return ""


def strip_footer(content):
    match = _FOOTER_RE.search(content or "")
    if not match:
        return content or "", None
    try:
        payload = json.loads(match.group("payload"))
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), dict):
        payload = None
    return (content or "")[:match.start()].rstrip() + "\n", payload


def _quick_note_anchors(text, journal_date):
    """Anchor a quick note block via its start marker.

    Since the marker carries its own date, a note begun before midnight and
    submitted afterwards keeps that date even though it lands in the next
    day's journal. The year comes from the journal and is rolled back when the
    block would otherwise sit in the future (a note started on New Year's Eve).
    """
    dated = _QUICK_NOTE_RE.findall(text)
    if len(dated) == 1 and _QUICK_NOTE_END_RE.search(text):
        day, month, clock = dated[0]
        try:
            journal_day = date.fromisoformat(journal_date)
            started = date(journal_day.year, int(month), int(day))
            if started > journal_day:
                started = date(journal_day.year - 1, int(month), int(day))
        except ValueError:
            return []
        return [f"{started.isoformat()} {clock}"]
    legacy = _LEGACY_QUICK_NOTE_RE.findall(text)
    if len(legacy) == 1 and f"~~{legacy[0]}~~" in text:
        return [f"{journal_date} {legacy[0]}"]
    return []


def _block_tags(text):
    return sorted({normalise_tag(match.group(1)) for match in _HASHTAG_RE.finditer(text)} - {""})


def _trim_range(lines, start, end):
    """Narrow a line range to its first and last non-empty line."""
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    if start < end and lines[start].startswith("# Journal "):
        return _trim_range(lines, start + 1, end)
    return start, end


def _separated_blocks(body, journal_date=None):
    """Return only complete separated journal blocks with a unique full timestamp."""
    lines = body.splitlines()
    separators = [index for index, line in enumerate(lines) if line.strip() == "___"]
    blocks = []
    for before, after in zip(separators, separators[1:]):
        start, end = _trim_range(lines, before + 1, after)
        if start >= end:
            continue
        text = "\n".join(lines[start:end])
        anchors = _ANCHOR_RE.findall(text)
        if not anchors and journal_date:
            anchors = _quick_note_anchors(text, journal_date)
        if len(anchors) != 1:
            continue
        try:
            anchor = datetime.fromisoformat(anchors[0].replace(" ", "T")).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        blocks.append({
            "anchor": anchor,
            "start_line": before + 2,
            "end_line": after,
            "text": text,
            "raw_tags": _block_tags(text),
        })
    return blocks


def _legacy_clock(text):
    """Return the single clock time an old-format block is stamped with."""
    found = {
        "{:02d}:{}".format(int(clock.split(":", 1)[0]), clock.split(":", 1)[1])
        for clock in (_LEGACY_TIME_RE.findall(text) or _LEGACY_CLOCK_LINE_RE.findall(text))
    }
    return found.pop() if len(found) == 1 else ""


def _legacy_blocks(body, journal_date):
    """Anchor the blocks of a pre-"Datum & Uhrzeit" journal on their clock time.

    Old journals separated entries with ``___`` as well, but stamped them as
    ``Time: 15:53:02``, ``**Time**: 19:24:43`` or a bare clock line, and the
    first entry of a day frequently sits before the very first separator — which
    is why the boundaries here span the whole file instead of only the gaps
    between two separators.

    Two blocks carrying the same clock are both dropped rather than guessed
    apart: an anchor addresses exactly one block everywhere else in this module,
    and a wrong guess would file a hashtag under the wrong entry.
    """
    lines = body.splitlines()
    separators = [index for index, line in enumerate(lines) if line.strip() == "___"]
    boundaries = [-1, *separators, len(lines)]
    blocks = []
    for before, after in zip(boundaries, boundaries[1:]):
        start, end = _trim_range(lines, before + 1, after)
        if start >= end:
            continue
        text = "\n".join(lines[start:end])
        clock = _legacy_clock(text)
        if not clock:
            continue
        try:
            datetime.fromisoformat(f"{journal_date}T{clock}")
        except ValueError:
            continue
        blocks.append({
            "anchor": f"{journal_date} {clock}",
            "start_line": start + 1,
            "end_line": end,
            "text": text,
            "raw_tags": _block_tags(text),
        })
    seen = [block["anchor"] for block in blocks]
    return [block for block in blocks if seen.count(block["anchor"]) == 1]


def _day_block(body, journal_date):
    """Fall back to a single block covering the whole day.

    The last resort for a journal whose entries carry no usable time at all:
    without it such a day stays invisible to every hashtag filter, because the
    reference index only keeps footer anchors that resolve to a real block. The
    anchor is the day at midnight, and ``scope`` marks it so a caller can say
    "the whole day" instead of showing a time that was never written down.
    """
    lines = body.splitlines()
    start, end = _trim_range(lines, 0, len(lines))
    text = "\n".join(lines[start:end]).strip()
    if not text.strip("_ \n\t"):
        return []
    return [{
        "anchor": f"{journal_date} 00:00:00",
        "start_line": start + 1,
        "end_line": end,
        "text": text,
        "raw_tags": _block_tags(text),
        "scope": "day",
    }]


def journal_blocks(content, journal_date=None):
    """Return the addressable blocks of a journal, oldest format included.

    Files written in the current format resolve on the first rule and never see
    the two fallbacks below, so no journal in use can be reinterpreted by them.
    Only a day that yields nothing at all falls back to the legacy clock stamps,
    and only a day without those to a single block for the whole day.
    """
    body, _ = strip_footer(content)
    blocks = _separated_blocks(body, journal_date)
    if blocks or not journal_date:
        return blocks
    try:
        date.fromisoformat(journal_date)
    except (TypeError, ValueError):
        return blocks
    return _legacy_blocks(body, journal_date) or _day_block(body, journal_date)


def render_footer(blocks, catalogs, ai_tags=None, ai_run=None):
    """Map every block to the hashtags the index may point at.

    Proposals count, not just approved tags: the footer feeds the hashtag index
    and therefore Brain's hashtag search and the MCP ``get_blocks`` tool. Limiting
    it to the canonical catalog made a fresh tag unfindable exactly while one was
    still deciding whether to keep it. Approval remains what decides colour and
    catalog membership; tags the user has blocked never return as a proposal and
    so stay out of the index.

    ``ai_tags`` records which of those hashtags a model assigned, and ``ai_run``
    when it last did so for this file. Both live beside ``blocks`` rather than in
    the block text, which is what lets a later run replace its own suggestions
    without ever touching a hashtag the user typed.
    """
    mapped = {}
    for block in blocks:
        tags = sorted({filterable_tag(tag, catalogs) for tag in block["raw_tags"]} - {""})
        if tags:
            mapped[block["anchor"]] = tags
    payload = {"blocks": mapped}
    machine = {
        anchor: sorted({filterable_tag(tag, catalogs) for tag in tags} - {""})
        for anchor, tags in (ai_tags or {}).items()
    }
    machine = {anchor: tags for anchor, tags in machine.items() if tags and anchor in mapped}
    if machine:
        payload["ai"] = machine
    if ai_run:
        payload["ai_run"] = ai_run
    payload = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return f'{FOOTER_START}\n<!--\n{payload}\n-->\n{FOOTER_END}\n'


def footer_ai_tags(footer):
    """Return the machine-assigned hashtags a footer carries, per block anchor."""
    machine = (footer or {}).get("ai")
    if not isinstance(machine, dict):
        return {}
    return {
        anchor: [tag for tag in tags if isinstance(tag, str)]
        for anchor, tags in machine.items()
        if isinstance(anchor, str) and isinstance(tags, list)
    }


def footer_ai_run(footer):
    """Return when a model last classified this file, or an empty string."""
    stamp = (footer or {}).get("ai_run")
    return stamp if isinstance(stamp, str) else ""


def refresh_journal_footer(user_id, content, journal_date=None, existing_footer=None):
    body, parsed_footer = strip_footer(content)
    if existing_footer is None:
        existing_footer = parsed_footer
    existing_blocks = (existing_footer or {}).get("blocks", {})
    blocks = journal_blocks(body, journal_date)
    for block in blocks:
        previous = existing_blocks.get(block["anchor"], [])
        if isinstance(previous, list):
            block["raw_tags"] = sorted(set(block["raw_tags"]) | {
                tag for tag in previous if isinstance(tag, str)
            })
    # A hashtag has to be known to the catalog before render_footer will keep it,
    # so every write path proposes first instead of losing the tag until the next
    # full index rebuild.
    if user_id:
        propose_tags(user_id, [tag for block in blocks for tag in block["raw_tags"]])
    catalogs = [read_catalog(user_id), read_catalog(family=True)]
    normalized = body.rstrip() + "\n\n" if body.strip() else ""
    # Machine origin survives every ordinary save: editing a day must not turn a
    # model's hashtags into the user's own.
    return normalized + render_footer(
        blocks, catalogs, footer_ai_tags(existing_footer), footer_ai_run(existing_footer),
    )


def journal_date_from_path(path):
    """The journal day a file stands for, or None for anything else.

    Every footer rewrite needs it: without the date a quick note carries no
    resolvable anchor, so its footer entry — including hashtags a model assigned,
    which exist nowhere else — would be dropped on the next save.
    """
    match = re.fullmatch(r"Journal_(\d{4}-\d{2}-\d{2})\.md", Path(path).name)
    return match.group(1) if match else None


def refresh_journal_file(user_id, path):
    path = Path(path)
    date_match = re.fullmatch(r"Journal_(\d{4}-\d{2}-\d{2})\.md", path.name)
    journal_date = date_match.group(1) if date_match else None

    def update(content):
        blocks = journal_blocks(content, journal_date)
        propose_tags(user_id, [tag for block in blocks for tag in block["raw_tags"]])
        refreshed = refresh_journal_footer(user_id, content, journal_date)
        return refreshed, refreshed

    return update_text_file(path, update)


def journal_paths(user_id):
    root = DATA_DIR / user_id
    if not root.is_dir():
        return []
    return [
        path for path in root.rglob("Journal_*.md")
        if path.is_file() and _JOURNAL_RE.fullmatch(path.relative_to(root).as_posix())
    ]


def _family_paths():
    if not FAMILY_DIR.is_dir():
        return []
    return [path for path in FAMILY_DIR.rglob("*.md") if path.is_file() and "indexes" not in path.parts]


def _family_references(path):
    try:
        content = read_text_file(path)
    except (OSError, UnicodeDecodeError):
        return []
    relative = path.relative_to(FAMILY_DIR).as_posix()
    references = []
    for line_no, line in enumerate(content.splitlines(), 1):
        tags = sorted({normalise_tag(match.group(1)) for match in _HASHTAG_RE.finditer(line)} - {""})
        if tags:
            references.append({
                "source": "family", "path": relative, "anchor": f"line:{line_no}",
                "start_line": line_no, "end_line": line_no, "tags": tags,
            })
    return references


def rebuild_index(user_ids):
    """Refresh journal footers then publish an atomic, replaceable reference index."""
    references = []
    for user_id in sorted(set(user_ids)):
        for path in journal_paths(user_id):
            try:
                content = refresh_journal_file(user_id, path)
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            _, footer = strip_footer(content)
            date_match = re.fullmatch(r"Journal_(\d{4}-\d{2}-\d{2})\.md", path.name)
            journal_date = date_match.group(1) if date_match else None
            blocks = {block["anchor"]: block for block in journal_blocks(content, journal_date)}
            machine_tags = footer_ai_tags(footer)
            for anchor, tags in (footer or {}).get("blocks", {}).items():
                if anchor not in blocks or not isinstance(tags, list):
                    continue
                clean_tags = sorted({normalise_tag(tag) for tag in tags} - {""})
                if clean_tags:
                    block = blocks[anchor]
                    reference = {
                        "source": "personal", "user_id": user_id,
                        "path": path.relative_to(DATA_DIR / user_id).as_posix(), "anchor": anchor,
                        "start_line": block["start_line"], "end_line": block["end_line"], "tags": clean_tags,
                    }
                    # Origin travels with the reference so a reader can tell a
                    # model's hashtag from one the user typed without reopening
                    # the file.
                    machine = sorted(
                        {normalise_tag(tag) for tag in machine_tags.get(anchor, [])} & set(clean_tags)
                    )
                    if machine:
                        reference["ai_tags"] = machine
                    references.append(reference)
    family_refs = [reference for path in _family_paths() for reference in _family_references(path)]
    # Propose before filtering: a Family hashtag seen for the first time has to be
    # in the catalog already, otherwise filterable_tag drops it from this very
    # rebuild and the tag stays unsearchable until the next one.
    propose_tags(None, [tag for reference in family_refs for tag in reference["tags"]], family=True)
    family_catalog = read_catalog(family=True)
    for reference in family_refs:
        reference["tags"] = sorted({filterable_tag(tag, [family_catalog]) for tag in reference["tags"]} - {""})
        if reference["tags"]:
            references.append(reference)
    index = {"version": 1, "rebuilt_at": datetime.now(timezone.utc).isoformat(), "references": references}
    write_text_file(INDEX_PATH, json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    global _snapshot
    with _snapshot_lock:
        _snapshot = index
    return index


def get_index():
    global _snapshot
    with _snapshot_lock:
        if _snapshot is None:
            candidate = _read_json(INDEX_PATH, {})
            _snapshot = candidate if candidate.get("version") == 1 and isinstance(candidate.get("references"), list) else {
                "version": 1, "references": [], "rebuilt_at": None
            }
        return _snapshot


def references_for_tags(user_id, tags, sources=None):
    """Return the caller's index references, optionally narrowed to one source.

    ``sources`` exists for the split MCP endpoints: the family server must never
    reach a personal journal, not even one that carries a family hashtag.
    """
    selected = {normalise_tag(tag) for tag in tags} - {""}
    allowed = set(sources) if sources else {"personal", "family"}
    refs = [
        reference for reference in get_index()["references"]
        if ("personal" in allowed
            and reference.get("source") == "personal" and reference.get("user_id") == user_id)
        or ("family" in allowed and reference.get("source") == "family")
    ]
    if selected:
        refs = [reference for reference in refs if selected.issubset(set(reference.get("tags", [])))]
    return refs
