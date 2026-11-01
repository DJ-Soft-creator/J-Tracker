"""Preview and apply revision-safe hashtag replacements in personal journals.

AI maintenance contract:
Own exact hashtag matching and bulk journal mutation. HTTP validation belongs in
brain_routes.py; generic document editing remains in brain_content.py.
"""

import sys

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before domain modules are imported")
globals().update({name: getattr(_brain, name) for name in dir(_brain) if not name.startswith("__")})


def _sync_from_facade():
    for name in ("DATA_DIR", "FAMILY_DIR", "enqueue_rebuild"):
        if hasattr(_brain, name):
            globals()[name] = getattr(_brain, name)


def _replacement_tag(raw):
    """Return a literal hashtag name without silently rewriting user input."""
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if value.startswith("#"):
        value = value[1:]
    return value if value and re.fullmatch(r"[\w-]+", value, re.UNICODE) else ""


def _replacement_pattern(tag):
    return re.compile(rf"(?<![\w#])#{re.escape(tag)}(?![\w-])", re.IGNORECASE | re.UNICODE)


def _replacement_preview_line(content, match):
    line_start = content.rfind("\n", 0, match.start()) + 1
    line_end = content.find("\n", match.end())
    if line_end < 0:
        line_end = len(content)
    line = content[line_start:line_end].strip()
    return line if len(line) <= 240 else line[:237].rstrip() + "…"


def hashtag_replacement_preview(user_id, old_tag):
    """Read current personal journals and return exact matches plus hashes."""
    tag = _replacement_tag(old_tag)
    if not tag:
        raise ValueError("Altes Hashtag ist ungültig")
    pattern = _replacement_pattern(tag)
    root = _user_root(user_id)
    journals = []
    for path in tagging_module.journal_paths(user_id):
        content, _ = _read_source_snapshot(path)
        if content is None:
            continue
        body, _footer = tagging_module.strip_footer(content)
        matches = list(pattern.finditer(body))
        if not matches:
            continue
        relative = _relative_path(root, path)
        if not relative:
            continue
        previews = []
        for match in matches:
            line = _replacement_preview_line(body, match)
            if line and line not in previews:
                previews.append(line)
            if len(previews) == 3:
                break
        date_match = _JOURNAL_DATE_RE.fullmatch(path.name)
        journals.append({
            "doc_id": "personal:" + relative,
            "path": relative,
            "date": date_match.group(1) if date_match else "",
            "occurrences": len(matches),
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "previews": previews,
        })
    journals.sort(key=lambda item: (item["date"], item["path"]), reverse=True)
    return {
        "old_tag": tag,
        "journals": journals,
        "journal_count": len(journals),
        "occurrence_count": sum(item["occurrences"] for item in journals),
    }


def _updated_footer(existing_footer, old_tag, new_tag, user_id):
    if not isinstance(existing_footer, dict):
        return None
    blocks = existing_footer.get("blocks")
    if not isinstance(blocks, dict):
        return None
    catalogs = [tagging_module.read_catalog(user_id), tagging_module.read_catalog(family=True)]
    old_names = {tagging_module.normalise_tag(old_tag)}
    old_canonical = tagging_module.canonical_tag(old_tag, catalogs)
    if old_canonical:
        old_names.add(old_canonical)
    new_name = tagging_module.canonical_tag(new_tag, catalogs)
    def renamed(tags):
        values = []
        for tag in tags:
            normalized = tagging_module.normalise_tag(tag) if isinstance(tag, str) else ""
            if normalized in old_names:
                if new_name:
                    values.append(new_name)
            elif normalized:
                values.append(normalized)
        return sorted(set(values))

    updated = {
        anchor: renamed(tags) for anchor, tags in blocks.items() if isinstance(tags, list)
    }
    result = {"blocks": updated}
    # A rename must not silently turn a model's hashtag into the user's own, so
    # the machine-origin map is carried through the same replacement.
    machine = {
        anchor: renamed(tags)
        for anchor, tags in tagging_module.footer_ai_tags(existing_footer).items()
    }
    machine = {anchor: tags for anchor, tags in machine.items() if tags}
    if machine:
        result["ai"] = machine
    stamp = tagging_module.footer_ai_run(existing_footer)
    if stamp:
        result["ai_run"] = stamp
    seen = tagging_module.footer_ai_seen(existing_footer)
    if seen is not None:
        result["ai_seen"] = seen
    # A hashtag the user took back from a model stays taken back under its new name.
    rejected = {
        anchor: renamed(tags)
        for anchor, tags in tagging_module.footer_ai_rejected(existing_footer).items()
    }
    rejected = {anchor: tags for anchor, tags in rejected.items() if tags}
    if rejected:
        result["ai_rejected"] = rejected
    return result


def _replace_journal_content(content, user_id, path, old_tag, new_tag):
    body, footer = tagging_module.strip_footer(content)
    pattern = _replacement_pattern(old_tag)
    updated_body, count = pattern.subn("#" + new_tag, body)
    if not count:
        return content, 0
    date_match = _JOURNAL_DATE_RE.fullmatch(path.name)
    updated = tagging_module.refresh_journal_footer(
        user_id,
        updated_body,
        date_match.group(1) if date_match else None,
        existing_footer=_updated_footer(footer, old_tag, new_tag, user_id),
    )
    return updated, count


def apply_hashtag_replacement(user_id, old_tag, new_tag, selected_documents):
    """Replace selected journals, rechecking every preview hash before writes."""
    old_name = _replacement_tag(old_tag)
    new_name = _replacement_tag(new_tag)
    if not old_name or not new_name:
        raise ValueError("Altes und neues Hashtag müssen gültig sein")
    if old_name == new_name:
        raise ValueError("Altes und neues Hashtag sind identisch")
    if not isinstance(selected_documents, list) or not selected_documents:
        raise ValueError("Mindestens ein Journal muss ausgewählt sein")
    if len(selected_documents) > 5000:
        raise ValueError("Zu viele Journale in einer Ersetzung")

    root = _user_root(user_id)
    resolved = []
    seen = set()
    for item in selected_documents:
        if not isinstance(item, dict):
            raise ValueError("Ungültige Journalauswahl")
        doc_id = item.get("doc_id")
        expected_hash = item.get("content_hash")
        if doc_id in seen or not isinstance(expected_hash, str) or not _HASH_RE.fullmatch(expected_hash):
            raise ValueError("Ungültige oder doppelte Journalauswahl")
        document, path, _document_root, content = _resolve_document(user_id, doc_id)
        if not document or document["source"] != "personal" or document["kind"] != "journal":
            raise ValueError("Auswahl enthält kein persönliches Journal")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash:
            return {"conflict": True, "changed": [], "conflicts": [doc_id]}
        if not _replacement_pattern(old_name).search(tagging_module.strip_footer(content)[0]):
            return {"conflict": True, "changed": [], "conflicts": [doc_id]}
        relative = _relative_path(root, path)
        resolved.append((doc_id, path, relative, expected_hash))
        seen.add(doc_id)

    main = _main_module()
    changed = []
    conflicts = []
    for doc_id, path, relative, expected_hash in resolved:
        def update(current):
            if hashlib.sha256(current.encode("utf-8")).hexdigest() != expected_hash:
                return current, {"conflict": True}
            updated, count = _replace_journal_content(current, user_id, path, old_name, new_name)
            return updated, {"count": count}

        try:
            result = main._update_journal_file(path, update) if main else update_text_file(path, update)
        except Exception as exc:
            logger.warning("Brain hashtag replacement failed for %s: %s", path, exc)
            conflicts.append(doc_id)
            continue
        if not result or result.get("conflict") or not result.get("count"):
            conflicts.append(doc_id)
            continue
        changed.append({"doc_id": doc_id, "path": relative, "occurrences": result["count"]})

    if changed:
        # The confirmed replacement is an explicit catalog decision, so the
        # new cluster must immediately be available as a Brain facet.
        tagging_module.update_catalog(user_id, False, "approve", new_name)
        with _index_guard("personal", user_id, exclusive=True):
            for item in changed:
                document, _path, _root, _content = _resolve_document(user_id, item["doc_id"])
                if document:
                    _write_index_document("personal", document, user_id)
        enqueue_rebuild(user_id)
    return {
        "conflict": bool(conflicts),
        "changed": changed,
        "conflicts": conflicts,
        "journal_count": len(changed),
        "occurrence_count": sum(item["occurrences"] for item in changed),
    }


__all__ = [
    "_replacement_tag",
    "hashtag_replacement_preview",
    "apply_hashtag_replacement",
]
