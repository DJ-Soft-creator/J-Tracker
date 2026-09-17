"""Historical LLM-assisted hashtag classification for journal files."""

import copy
import json
import logging
import re
import sys
from datetime import datetime, timezone

import background_runs
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


def _is_due(mode, stamp, stale_before):
    """Decide whether a journal still needs the model in this run.

    ``missing`` is the resume mode: a file already stamped by an earlier run is
    left alone, so an interrupted pass simply continues where it stopped.
    ``stale`` re-runs everything the model saw before a given date, which is how
    an updated vocabulary reaches old entries. ``refresh`` ignores the stamp.
    """
    if mode == "refresh":
        return True
    if mode == "stale":
        return not stamp or stamp[:10] < (stale_before or "")
    return not stamp


def run_historical_tagging(user_id, start_date, end_date, provider, prompt_config, call_ai,
                           mode="refresh", stale_before="", max_files=None, preview=False):
    """Classify journals in a date range and record the model's hashtags.

    The model never edits a journal block. Its assignments are written to the
    technical footer under ``ai``, next to the hashtags the user typed — which is
    what allows a later run to replace its own suggestions without touching a
    single one of theirs.

    ``max_files`` stops after that many journals and reports what is left, so the
    caller can drive a long pass in cancellable steps instead of one request that
    must not be interrupted. With ``preview`` the model runs but nothing is
    written and no file is stamped — the report shows what an actual run would
    assign. (A preview still records newly proposed hashtags in the catalog,
    otherwise it would show fewer tags than the run it is supposed to preview.)

    ``report["files"]`` names every journal the model actually saw, block by
    block, so a caller can show which entry received which hashtag instead of a
    bare count. Files that were skipped are only counted: a resume over a long
    range would otherwise repeat the whole backlog in every single response.
    """
    if mode not in {"missing", "refresh", "stale"}:
        raise ValueError("mode must be missing, refresh or stale")
    if mode == "stale" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stale_before or ""):
        raise ValueError("stale mode needs an ISO date in stale_before")
    ai_function, user_prompt_template = _prompt_settings(prompt_config)
    catalog = tagging_module.catalog_view(user_id)
    canonical = catalog["canonical"]
    report = {
        "processed": 0, "skipped": 0, "errors": [], "proposals": [],
        "remaining": 0, "next_date": "", "files": [],
    }

    candidates = []
    for path in tagging_module.journal_paths(user_id):
        date_match = re.search(r"Journal_(\d{4}-\d{2}-\d{2})\.md$", path.name)
        if date_match and start_date <= date_match.group(1) <= end_date:
            candidates.append((date_match.group(1), path))

    for journal_date, path in sorted(candidates):
        try:
            content = read_text_file(path)
            body, previous_footer = tagging_module.strip_footer(content)
            if not _is_due(mode, tagging_module.footer_ai_run(previous_footer), stale_before):
                report["skipped"] += 1
                continue
            if max_files is not None and report["processed"] + len(report["errors"]) >= max_files:
                report["remaining"] += 1
                report["next_date"] = report["next_date"] or journal_date
                continue
            blocks = tagging_module.journal_blocks(body, journal_date)
            if not blocks:
                # Named rather than silently counted: "why did this day get
                # nothing" is the first question a bare skip count provokes.
                report["skipped"] += 1
                report["files"].append({
                    "date": journal_date, "file": path.name, "status": "empty", "blocks": [],
                })
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
            proposed = [tagging_module.normalise_tag(tag) for tag in proposals]
            # Propose before filtering: a hashtag the model invents in this very
            # run must already be in the catalog, otherwise filterable_tag drops
            # it and the suggestion is lost until someone types it by hand. The
            # same holds for a hashtag the user typed into an old journal that no
            # write path has touched since.
            #
            # The block assignments count as proposals too, not just the model's
            # own ``proposed_tags`` list: local models routinely tag a block with
            # a word they never repeat there, and filtering those out left a run
            # that reported success while its hashtags existed nowhere — not in
            # the footer, not in the catalog, not in any filter.
            assigned_by_model = [
                tagging_module.normalise_tag(tag)
                for tags in classified.values() for tag in tags
            ]
            tagging_module.propose_tags(
                user_id,
                proposed + assigned_by_model + [tag for block in blocks for tag in block["raw_tags"]],
            )
            catalogs = [tagging_module.read_catalog(user_id), tagging_module.read_catalog(family=True)]
            assigned = {
                anchor: sorted({tagging_module.filterable_tag(tag, catalogs) for tag in tags} - {""})
                for anchor, tags in classified.items()
            }
            # The user's own hashtags stay untouched; only what the model adds on
            # top of them is recorded as machine origin.
            new_blocks = [
                {**block, "raw_tags": sorted(set(block["raw_tags"]) | set(assigned.get(block["anchor"], [])))}
                for block in blocks
            ]
            ai_tags = {
                block["anchor"]: sorted(set(assigned.get(block["anchor"], [])) - set(block["raw_tags"]))
                for block in blocks
            }
            detail = {
                "date": journal_date,
                "file": path.name,
                "status": "preview" if preview else "processed",
                "blocks": [
                    {
                        "anchor": block["anchor"],
                        "time": block["anchor"][11:],
                        "scope": block.get("scope", "block"),
                        "ai_tags": ai_tags.get(block["anchor"], []),
                        "own_tags": block["raw_tags"],
                    }
                    for block in blocks
                ],
            }
            if not preview:
                updated_content = body.rstrip() + "\n\n" + tagging_module.render_footer(
                    new_blocks, catalogs, ai_tags,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )

                def write_if_unchanged(current):
                    current_body, _ = tagging_module.strip_footer(current)
                    if current_body != body:
                        return current, {"conflict": True}
                    return updated_content, {"ok": True}

                result = update_text_file(path, write_if_unchanged)
                if result.get("conflict"):
                    raise ValueError("Journal changed while the model was classifying it")
            report["files"].append(detail)
            # Report what actually landed as a proposal, whether the model named
            # it under ``proposed_tags`` or only used it on a block.
            open_proposals = set(catalogs[0].get("proposals", []))
            report["proposals"].extend(
                tag for tag in set(proposed) | set(assigned_by_model)
                if tag and tag in open_proposals
            )
            report["processed"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad journal must not end the pass
            # Deliberately broad: whatever a provider, a codec or a malformed file
            # throws, the run has to continue and the caller has to learn what it
            # was. A narrow list turned an unexpected type into an HTTP 500 whose
            # body was not even JSON, so the browser could only say "failed".
            message = "{}: {}".format(type(exc).__name__, exc).strip(": ")
            # Die Fehlerart bleibt am Befund haengen, nicht nur im Text: ein
            # serverseitiger Lauf muss "Endpunkt weg" von "dieses eine Journal
            # war krumm" unterscheiden koennen, ohne Meldungen zu zerlegen.
            report["errors"].append({
                "file": path.name, "date": journal_date, "error": message,
                "kind": type(exc).__name__,
            })
            report["files"].append({
                "date": journal_date, "file": path.name, "status": "error",
                "error": message, "blocks": [],
            })
            logger.exception("Historical tagging failed for %s", path.name)

    report["proposals"] = sorted(set(report["proposals"]))
    return report


# ─── Serverseitiger Lauf ──────────────────────────────────────────────
# Der Zeitraum lief frueher als Schleife im Browser: ein Request je Journal.
# Sperrt sich das Handy, ist der Lauf tot. Dieselbe Schleife hier im Server
# haelt durch; der Browser fragt nur noch nach dem Stand.
RUN_KIND = "historical_tagging"

# Verbindungsfehler kommen aus main._call_ai_api als ConnectionError, egal ob
# der Endpunkt weg ist, das Modell nicht geladen ist oder eine Zeitueberschreitung
# zuschlaegt. Genau dann bricht der Lauf sofort ab, statt tausend Tage lang
# denselben Fehler zu protokollieren.
_PROVIDER_ERROR_KINDS = {"ConnectionError"}


def _core_module():
    return sys.modules.get("main") or sys.modules.get("__main__")


def resolve_provider(config, provider_id):
    """Der einzige erlaubte Anbieter fuer diesen Lauf: ein lokales LM Studio."""
    provider = next(
        (item for item in (config.get("ai_providers") or []) if item.get("id") == provider_id),
        None,
    )
    if not provider or not str(provider.get("id", "")).startswith("lm_"):
        return None
    return provider


def run_handler(handle, params):
    """Verschlagworte den ganzen Zeitraum, Journal fuer Journal, abbrechbar.

    Jede fertige Datei ist gestempelt, der Cursor wandert mit. Ein Abbruch —
    durch den Nutzer, durch einen toten Endpunkt oder durch einen Neustart —
    kostet deshalb nichts ausser der angefangenen Datei.
    """
    core = _core_module()
    config = core.load_config() if core else {}
    provider = resolve_provider(config, params.get("provider_id"))
    if provider is None:
        raise background_runs.RunError(
            "Der gewählte lokale Anbieter existiert nicht mehr. Bitte in den Einstellungen prüfen."
        )
    preview = bool(params.get("preview"))
    end_date = params.get("end_date", "")
    cursor = params.get("start_date", "")
    totals = {"processed": 0, "skipped": 0, "failed": 0}
    proposals = set()
    stopped = ""
    remaining = 0

    while True:
        if handle.cancelled:
            stopped = "Vom Nutzer abgebrochen."
            break
        report = run_historical_tagging(
            handle.user_id, cursor, end_date, provider,
            config.get("historical_tagging_ai"), core._call_ai_api,
            mode=params.get("mode", "missing"), stale_before=params.get("stale_before", ""),
            max_files=1, preview=preview,
        )
        totals["processed"] += report["processed"]
        totals["skipped"] += report["skipped"]
        totals["failed"] += len(report["errors"])
        proposals.update(report["proposals"])
        for entry in report["files"]:
            handle.log(entry)
        remaining = report["remaining"]
        handle.progress(
            processed=totals["processed"], skipped=totals["skipped"], failed=totals["failed"],
            remaining=remaining, proposals=len(proposals), preview=preview,
            current_date=report["next_date"] or end_date,
        )
        provider_failure = next(
            (item for item in report["errors"] if item.get("kind") in _PROVIDER_ERROR_KINDS), None,
        )
        if provider_failure:
            raise background_runs.RunError(
                "Das lokale Modell war nicht erreichbar, der Lauf wurde gestoppt: {}".format(
                    provider_failure["error"],
                )
            )
        if remaining <= 0:
            break
        previous, cursor = cursor, report["next_date"] or ""
        if not cursor or cursor <= previous:
            # Sicherheitsnetz wie in der alten Browserschleife: lieber anhalten,
            # als demselben Tag endlos dasselbe Modell vorzusetzen.
            stopped = "Kein Fortschritt gemeldet – der Lauf wurde gestoppt."
            handle.log({"date": previous, "status": "error", "error": stopped, "blocks": []})
            break

    if totals["processed"] and not preview and getattr(core, "brain_module", None):
        # Ueber die Fassade, nicht per Direktimport: dieses Modul soll fuer einen
        # Test nicht die halbe Anwendung nachziehen muessen.
        core.brain_module.enqueue_rebuild(handle.user_id)
    logger.info(
        "Historical tagging run for %s: processed=%d skipped=%d failed=%d remaining=%d preview=%s",
        handle.user_id, totals["processed"], totals["skipped"], totals["failed"], remaining, preview,
    )
    return {**totals, "remaining": remaining, "proposals": sorted(proposals),
            "preview": preview, "stopped": stopped}


background_runs.register(RUN_KIND, run_handler)
