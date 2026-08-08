"""Historical LLM-assisted hashtag classification for journal files."""

import copy
import json
import logging
import re

import tagging as tagging_module
from scheduling import read_text_file, update_text_file


logger = logging.getLogger(__name__)

_CANONICAL_TAGS_PLACEHOLDER = "{canonical_tags_json}"
_JOURNAL_BODY_PLACEHOLDER = "{journal_body}"
_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "historical_journal_tagging",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "blocks": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "proposed_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["blocks", "proposed_tags"],
            "additionalProperties": False,
        },
    },
}


def _prompt_settings(config):
    if not isinstance(config, dict):
        raise ValueError("historical_tagging_ai must be configured as an object")

    system_prompt = config.get("system_prompt")
    user_prompt_template = config.get("user_prompt_template")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError("historical_tagging_ai.system_prompt must not be empty")
    if not isinstance(user_prompt_template, str) or not user_prompt_template.strip():
        raise ValueError("historical_tagging_ai.user_prompt_template must not be empty")
    if _CANONICAL_TAGS_PLACEHOLDER not in user_prompt_template:
        raise ValueError(f"user_prompt_template must contain {_CANONICAL_TAGS_PLACEHOLDER}")
    if _JOURNAL_BODY_PLACEHOLDER not in user_prompt_template:
        raise ValueError(f"user_prompt_template must contain {_JOURNAL_BODY_PLACEHOLDER}")
    max_tokens = config.get("max_tokens", 2000)
    temperature = config.get("temperature", 0)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("historical_tagging_ai.max_tokens must be a positive integer")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
        raise ValueError("historical_tagging_ai.temperature must be between 0 and 2")
    return {
        "system_prompt": system_prompt.strip(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": _RESPONSE_FORMAT,
        "require_content": True,
    }, user_prompt_template


def _render_prompt(template, canonical_tags, journal_body):
    return template.replace(
        _CANONICAL_TAGS_PLACEHOLDER,
        json.dumps(canonical_tags, ensure_ascii=False),
    ).replace(_JOURNAL_BODY_PLACEHOLDER, journal_body)


def _ai_function_for_anchors(ai_function, anchors):
    configured = copy.deepcopy(ai_function)
    blocks_schema = configured["response_format"]["json_schema"]["schema"]["properties"]["blocks"]
    tag_list_schema = blocks_schema["additionalProperties"]
    ordered_anchors = sorted(anchors)
    blocks_schema["properties"] = {
        anchor: copy.deepcopy(tag_list_schema)
        for anchor in ordered_anchors
    }
    blocks_schema["required"] = ordered_anchors
    blocks_schema["additionalProperties"] = False
    return configured


def _reject_duplicate_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Model response contains duplicate JSON key: {key}")
        value[key] = item
    return value


def _parse_model_response(value):
    """Strictly accept the documented JSON shape, not prose or fenced JSON."""
    parsed = json.loads(value, object_pairs_hook=_reject_duplicate_json_keys)
    if not isinstance(parsed, dict) or set(parsed) != {"blocks", "proposed_tags"}:
        raise ValueError("Model response must contain blocks and proposed_tags")
    blocks = parsed["blocks"]
    proposals = parsed["proposed_tags"]
    if not isinstance(blocks, dict) or not isinstance(proposals, list):
        raise ValueError("Model response has an invalid schema")
    if any(not isinstance(anchor, str) or not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags) for anchor, tags in blocks.items()):
        raise ValueError("Model block tags have an invalid schema")
    if any(not isinstance(tag, str) for tag in proposals):
        raise ValueError("Model proposed_tags has an invalid schema")
    return blocks, proposals


def run_historical_tagging(user_id, start_date, end_date, provider, prompt_config, call_ai):
    """Classify journals in a date range and write their canonical hashtag footers."""
    ai_function, user_prompt_template = _prompt_settings(prompt_config)
    catalog = tagging_module.catalog_view(user_id)
    canonical = catalog["canonical"]
    report = {"processed": 0, "skipped": 0, "errors": [], "proposals": []}

    for path in tagging_module.journal_paths(user_id):
        date_match = re.search(r"Journal_(\d{4}-\d{2}-\d{2})\.md$", path.name)
        if not date_match or not start_date <= date_match.group(1) <= end_date:
            continue
        try:
            content = read_text_file(path)
            body, _ = tagging_module.strip_footer(content)
            blocks = tagging_module.journal_blocks(body, date_match.group(1))
            if not blocks:
                report["skipped"] += 1
                continue
            anchors = {block["anchor"] for block in blocks}
            if len(anchors) != len(blocks):
                raise ValueError("Journal contains duplicate timestamps and cannot be classified safely")
            classification_body = "\n\n___\n\n".join(block["text"] for block in blocks)
            prompt = _render_prompt(user_prompt_template, canonical, classification_body)
            response = call_ai(
                provider,
                _ai_function_for_anchors(ai_function, anchors),
                prompt,
            )
            classified, proposals = _parse_model_response(response)
            if set(classified) != anchors:
                missing = sorted(anchors - set(classified))
                unknown = sorted(set(classified) - anchors)
                raise ValueError(
                    f"Model timestamps differ from journal (missing: {missing or 'none'}; "
                    f"unknown: {unknown or 'none'})"
                )
            proposed = [
                tagging_module.normalise_tag(tag)
                for tag in proposals
            ]
            catalogs = [tagging_module.read_catalog(user_id), tagging_module.read_catalog(family=True)]
            assigned = {
                anchor: [tagging_module.canonical_tag(tag, catalogs) for tag in tags]
                for anchor, tags in classified.items()
            }
            assigned = {anchor: sorted(set(tags) - {""}) for anchor, tags in assigned.items()}
            new_blocks = [{**block, "raw_tags": assigned.get(block["anchor"], [])} for block in blocks]
            updated_content = body.rstrip() + "\n\n" + tagging_module.render_footer(new_blocks, catalogs)

            def write_if_unchanged(current):
                current_body, _ = tagging_module.strip_footer(current)
                if current_body != body:
                    return current, {"conflict": True}
                return updated_content, {"ok": True}

            result = update_text_file(path, write_if_unchanged)
            if result.get("conflict"):
                raise ValueError("Journal changed while the model was classifying it")
            tagging_module.propose_tags(user_id, proposed)
            report["proposals"].extend(tag for tag in proposed if tag)
            report["processed"] += 1
        except (OSError, UnicodeDecodeError, ValueError, ConnectionError, json.JSONDecodeError) as exc:
            report["errors"].append({"file": path.name, "error": str(exc)})
            logger.warning("Historical tagging failed for %s: %s", path.name, exc)

    report["proposals"] = sorted(set(report["proposals"]))
    return report
