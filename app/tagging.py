"""Markdown hashtag footer, catalog, and rebuildable reference index."""

import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
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
        "ai_workflows": {},
        "knowledge_sources": {},
    }


def _catalog_path(user_id=None, family=False):
    return (FAMILY_DIR if family else DATA_DIR / user_id) / "hashtag_catalog.json"


def _valid_catalog(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("canonical", []), list)
        and isinstance(value.get("aliases", {}), dict)
        and isinstance(value.get("proposals", []), list)
        and isinstance(value.get("ai_workflows", {}), dict)
        and isinstance(value.get("knowledge_sources", {}), dict)
    )


def read_catalog(user_id=None, family=False):
    catalog = _read_json(_catalog_path(user_id, family), _catalog_default())
    return catalog if _valid_catalog(catalog) else _catalog_default()


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
        catalog["ai_workflows"] = {
            normalise_tag(tag): workflow
            for tag, workflow in catalog.get("ai_workflows", {}).items()
            if normalise_tag(tag).startswith("ai-") and isinstance(workflow, dict)
        }
        catalog["knowledge_sources"] = {
            normalise_tag(tag): source for tag, source in catalog.get("knowledge_sources", {}).items()
            if normalise_tag(tag) and isinstance(source, dict)
        }
        known_tags = set(catalog["canonical"]) | set(catalog["ai_workflows"])
        catalog["proposals"] = [tag for tag in catalog["proposals"] if tag not in known_tags]
        return json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", result

    return update_text_file(path, update)


def propose_tags(user_id, tags, family=False):
    cleaned = {normalise_tag(tag) for tag in tags}
    cleaned.discard("")
    if not cleaned:
        return []

    def update(catalog):
        known = set(catalog.get("canonical", [])) | set(catalog.get("aliases", {})) | set(catalog.get("ai_workflows", {}))
        added = sorted(cleaned - known - set(catalog.get("proposals", [])))
        catalog["proposals"].extend(added)
        return added

    return _write_catalog(user_id, family, update)


def catalog_view(user_id):
    personal = read_catalog(user_id)
    family = read_catalog(family=True)
    return {
        "personal": personal,
        "family": family,
        "canonical": sorted(set(personal["canonical"]) | set(family["canonical"])),
        "ai": personal.get("ai_workflows", {}),
        # Keep the scopes separate.  A knowledge tag must be unambiguous for a
        # reader, so the write endpoint rejects duplicate names across these
        # two catalogs instead of relying on dictionary merge order.
        "knowledge": {
            "personal": personal.get("knowledge_sources", {}),
            "family": family.get("knowledge_sources", {}),
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
        if action == "approve":
            canonical.add(tag)
            proposals.discard(tag)
        elif action == "remove":
            canonical.discard(tag)
            proposals.discard(tag)
            aliases = {alias: value for alias, value in aliases.items() if alias != tag and value != tag}
        elif action == "alias":
            if not target or target not in canonical or tag == target:
                raise ValueError("Alias target must be an approved canonical hashtag")
            aliases[tag] = target
            proposals.discard(tag)
        elif action == "remove_alias":
            aliases.pop(tag, None)
        else:
            raise ValueError("Unknown catalog action")
        catalog["canonical"] = sorted(canonical)
        catalog["aliases"] = aliases
        catalog["proposals"] = sorted(proposals)
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
            if target not in {"document", "write_tab"}:
                raise ValueError("AI hashtag target must be document or write_tab")
            if len(provider_id) > 120 or any(char in provider_id for char in "\r\n"):
                raise ValueError("A valid AI provider is required")
            if not isinstance(context_files, list) or any(not isinstance(path, str) for path in context_files):
                raise ValueError("context_files must be a list")
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
            candidate = PurePosixPath(path)
            if (
                not path or "\\" in path or candidate.is_absolute() or ".." in candidate.parts
                or candidate.suffix != ".md" or candidate.as_posix() != path
                or not candidate.parts or candidate.parts[0] not in {"notes", "projects"}
            ):
                raise ValueError("Knowledge source must be a relative Markdown path")
            sources[tag] = {"scope": "family" if family else "personal", "path": path}
        else:
            raise ValueError("Unknown knowledge source action")
        catalog["knowledge_sources"] = sources
        return sources.get(tag)
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


def journal_blocks(content, journal_date=None):
    """Return only complete separated journal blocks with a unique full timestamp."""
    body, _ = strip_footer(content)
    lines = body.splitlines()
    separators = [index for index, line in enumerate(lines) if line.strip() == "___"]
    blocks = []
    for before, after in zip(separators, separators[1:]):
        chunk = lines[before + 1:after]
        while chunk and not chunk[0].strip():
            chunk.pop(0)
        while chunk and not chunk[-1].strip():
            chunk.pop()
        if not chunk:
            continue
        text = "\n".join(chunk)
        anchors = _ANCHOR_RE.findall(text)
        if not anchors and journal_date:
            quick_note_times = _LEGACY_QUICK_NOTE_RE.findall(text)
            if len(quick_note_times) == 1 and f"~~{quick_note_times[0]}~~" in text:
                anchors = [f"{journal_date} {quick_note_times[0]}"]
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
            "raw_tags": sorted({normalise_tag(match.group(1)) for match in _HASHTAG_RE.finditer(text)} - {""}),
        })
    return blocks


def render_footer(blocks, catalogs):
    mapped = {}
    for block in blocks:
        tags = sorted({canonical_tag(tag, catalogs) for tag in block["raw_tags"]} - {""})
        if tags:
            mapped[block["anchor"]] = tags
    payload = json.dumps({"blocks": mapped}, ensure_ascii=False, indent=2, sort_keys=True)
    return f'{FOOTER_START}\n<!--\n{payload}\n-->\n{FOOTER_END}\n'


def refresh_journal_footer(user_id, content, journal_date=None, existing_footer=None):
    body, parsed_footer = strip_footer(content)
    if existing_footer is None:
        existing_footer = parsed_footer
    existing_blocks = (existing_footer or {}).get("blocks", {})
    catalogs = [read_catalog(user_id), read_catalog(family=True)]
    blocks = journal_blocks(body, journal_date)
    for block in blocks:
        previous = existing_blocks.get(block["anchor"], [])
        if isinstance(previous, list):
            block["raw_tags"] = sorted(set(block["raw_tags"]) | {
                tag for tag in previous if isinstance(tag, str)
            })
    normalized = body.rstrip() + "\n\n" if body.strip() else ""
    return normalized + render_footer(blocks, catalogs)


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
            for anchor, tags in (footer or {}).get("blocks", {}).items():
                if anchor not in blocks or not isinstance(tags, list):
                    continue
                clean_tags = sorted({normalise_tag(tag) for tag in tags} - {""})
                if clean_tags:
                    block = blocks[anchor]
                    references.append({
                        "source": "personal", "user_id": user_id,
                        "path": path.relative_to(DATA_DIR / user_id).as_posix(), "anchor": anchor,
                        "start_line": block["start_line"], "end_line": block["end_line"], "tags": clean_tags,
                    })
    family_tags = []
    family_catalog = read_catalog(family=True)
    for path in _family_paths():
        refs = _family_references(path)
        family_tags.extend(tag for ref in refs for tag in ref["tags"])
        for reference in refs:
            reference["tags"] = sorted({canonical_tag(tag, [family_catalog]) for tag in reference["tags"]} - {""})
            if reference["tags"]:
                references.append(reference)
    propose_tags(None, family_tags, family=True)
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


def references_for_tags(user_id, tags):
    selected = {normalise_tag(tag) for tag in tags} - {""}
    refs = [
        reference for reference in get_index()["references"]
        if (reference.get("source") == "personal" and reference.get("user_id") == user_id)
        or reference.get("source") == "family"
    ]
    if selected:
        refs = [reference for reference in refs if selected.issubset(set(reference.get("tags", [])))]
    return refs


def tag_counts(user_id):
    counts = {}
    for reference in references_for_tags(user_id, []):
        for tag in set(reference.get("tags", [])):
            counts[tag] = counts.get(tag, 0) + 1
    return [{"name": tag, "count": count} for tag, count in sorted(counts.items())]
