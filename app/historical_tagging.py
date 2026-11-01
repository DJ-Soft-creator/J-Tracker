"""Historical LLM-assisted hashtag classification for journal files."""

import copy
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

import agent_projects
import background_runs
import tagging as tagging_module
import tagging_job
from scheduling import read_text_file, update_text_file, write_text_file


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


# So viele Hashtags nennt der Hinweis auf die etablierten Hashtags mit ihrer
# Haeufigkeit. Die vollstaendige Liste steht ohnehin im Platzhalter.
ESTABLISHED_HINT_LIMIT = 40
# Ab so vielen Eintraegen gilt ein noch nicht freigegebener Hashtag als etabliert.
ESTABLISHED_MIN_USES = 3


def established_tags(user_id):
    """Die Hashtags, die das Modell bevorzugen soll, haeufigste zuerst.

    Etabliert ist, was freigegeben ist, und was schon oft genug verwendet wird,
    auch ohne Freigabe. Die Zaehlung kommt aus dem Hashtag-Index (eigene Journale
    plus Familie). Workflow-, Wissens- und Ziel-Hashtags loesen Aktionen aus und
    bleiben deshalb draussen — ein Modell darf sie nie vergeben.
    """
    view = tagging_module.catalog_view(user_id)
    action_tags = set()
    for catalog in (view["personal"], view["family"]):
        for field in ("ai_workflows", "knowledge_sources", "write_targets"):
            action_tags |= set(catalog.get(field) or {})
    counts = Counter()
    for reference in tagging_module.get_index().get("references", []):
        if reference.get("source") == "personal" and reference.get("user_id") != user_id:
            continue
        counts.update(tag for tag in reference.get("tags") or [] if isinstance(tag, str))
    pool = set(view["canonical"]) | {tag for tag, count in counts.items() if count >= ESTABLISHED_MIN_USES}
    pool -= action_tags
    # Global ausgeschlossene Hashtags empfiehlt der Prompt gar nicht erst.
    pool -= tagging_module.ai_excluded_tags(user_id)
    return [(tag, counts[tag]) for tag in sorted(pool, key=lambda tag: (-counts[tag], tag))]


def _render_prompt(template, canonical_tags, journal_body, established=None):
    """Den Prompt fuellen; die etablierten Hashtags stehen haeufigste zuerst.

    ``canonical_tags`` bleibt eine reine JSON-Liste, damit eigene Vorlagen weiter
    passen. Der Hinweis mit den Haeufigkeiten haengt hinten an: er sagt dem
    Modell, welche Woerter sich schon eingebuergert haben.
    """
    prompt = template.replace(
        _CANONICAL_TAGS_PLACEHOLDER,
        json.dumps(canonical_tags, ensure_ascii=False),
    ).replace(_JOURNAL_BODY_PLACEHOLDER, journal_body)
    used = [(tag, count) for tag, count in (established or []) if count][:ESTABLISHED_HINT_LIMIT]
    if used:
        prompt += (
            "\n\nEtablierte Hashtags mit Anzahl der Einträge – verwende bevorzugt diese und "
            "erfinde nur dann einen neuen, wenn keiner davon passt: "
            + ", ".join("{} ({})".format(tag, count) for tag, count in used) + "."
        )
    return prompt


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


def _untagged_blocks(blocks, footer):
    """Die Blöcke, für die weder der Nutzer noch ein Modell ein Hashtag hat.

    Der Footer führt jeden Block, der mindestens ein filterbares Hashtag trägt.
    Fehlt ein Anker dort, ist der Eintrag ohne Schlagwort — genau der Fall, den
    ein erneuter Lauf nachpflegen soll.
    """
    tagged = (footer or {}).get("blocks")
    if not isinstance(tagged, dict):
        tagged = {}
    return [block for block in blocks or [] if not tagged.get(block["anchor"])]


def _local_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
    except Exception:  # noqa: BLE001 - ohne Zeitzonendaten gilt die Systemzeit
        return None


def _seen_anchors(blocks, footer):
    """Die Bloecke, die ein Modell schon klassifiziert hat.

    Neue Fussnoten fuehren das als ``ai_seen``. Aeltere tragen nur den Stempel
    ``ai_run``: dort gilt als gesehen, was das Modell getaggt hat, und jeder
    Block, dessen Uhrzeit vor dem Stempel liegt. Was spaeter am Tag geschrieben
    wurde, ist neu — genau die Eintraege, die der alte Modus uebersprungen hat,
    sobald sie irgendein eigenes Hashtag trugen.
    """
    # Was der Nutzer einem Modell wieder weggenommen hat, war zwangslaeufig
    # schon beim Modell - auch in einer alten Fussnote ohne ``ai_seen``.
    rejected = set(tagging_module.footer_ai_rejected(footer))
    seen = tagging_module.footer_ai_seen(footer)
    if seen is not None:
        return set(seen) | rejected
    stamp = tagging_module.footer_ai_run(footer)
    if not stamp:
        return rejected
    seen = set(tagging_module.footer_ai_tags(footer)) | rejected
    try:
        run_at = datetime.fromisoformat(stamp)
    except ValueError:
        return seen
    if run_at.tzinfo is not None:
        run_at = run_at.astimezone(_local_tz()).replace(tzinfo=None)
    for block in blocks or []:
        try:
            written = datetime.fromisoformat(block["anchor"])
        except ValueError:
            continue
        if written <= run_at:
            seen.add(block["anchor"])
    return seen


def _pending_blocks(blocks, footer):
    """Was der Nachpflege-Lauf dem Modell vorlegt: neue Bloecke und Bloecke ganz ohne Hashtag.

    Neu heisst: noch nie vom Modell gesehen — egal, ob der Nutzer selbst schon
    ein Hashtag gesetzt hat. Ohne Hashtag heisst: auch ein gesehener Block, dem
    das Modell beim ersten Mal nichts gab, bekommt eine zweite Chance. Ausser der
    Nutzer hat dem Block KI-Hashtags weggenommen: dann hat er ihn sich angesehen,
    und ein leerer Block ist so gewollt.
    """
    seen = _seen_anchors(blocks, footer)
    curated = set(tagging_module.footer_ai_rejected(footer))
    untagged = {block["anchor"] for block in _untagged_blocks(blocks, footer)} - curated
    return [block for block in blocks or [] if block["anchor"] not in seen or block["anchor"] in untagged]


def _is_due(mode, stamp, stale_before, blocks=None, footer=None):
    """Decide whether a journal still needs the model in this run.

    ``missing`` ("Nur neue") is the resume mode **and** the gap-fill path: a file
    is due when it was never stamped or when it holds a block the model has not
    seen yet or that still carries no hashtag at all (``_pending_blocks``).
    ``stale`` re-runs everything the model saw before a given date, which is how
    an updated vocabulary reaches old entries. ``refresh`` ignores the stamp.
    """
    if mode == "refresh":
        return True
    if mode == "stale":
        return not stamp or stamp[:10] < (stale_before or "")
    return not stamp or bool(_pending_blocks(blocks, footer))


def _skip_reason(mode):
    if mode == "stale":
        return "nach dem Stichdatum schon verschlagwortet"
    return "alle Einträge schon von der KI gesehen und mit Hashtag"


def run_historical_tagging(user_id, start_date, end_date, provider, prompt_config, call_ai,
                           mode="refresh", stale_before="", max_files=None, preview=False,
                           name_skipped=False):
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
    ``name_skipped`` lists them anyway (status ``skipped``) — for the run log,
    where "why was this day left out" is exactly the question. Days after the
    ``max_files`` limit are neither counted nor named: the next call starts
    there and would report them a second time.
    """
    if mode not in {"missing", "refresh", "stale"}:
        raise ValueError("mode must be missing, refresh or stale")
    if mode == "stale" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stale_before or ""):
        raise ValueError("stale mode needs an ISO date in stale_before")
    ai_function, user_prompt_template = _prompt_settings(prompt_config)
    established = established_tags(user_id)
    canonical = [tag for tag, _count in established]
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
            blocks = tagging_module.journal_blocks(body, journal_date)
            due = _is_due(
                mode, tagging_module.footer_ai_run(previous_footer), stale_before,
                blocks=blocks, footer=previous_footer,
            )
            if max_files is not None and report["processed"] + len(report["errors"]) >= max_files:
                if due:
                    report["remaining"] += 1
                    report["next_date"] = report["next_date"] or journal_date
                continue
            if not due:
                report["skipped"] += 1
                if name_skipped:
                    report["files"].append({
                        "date": journal_date, "file": path.name, "status": "skipped",
                        "reason": _skip_reason(mode), "blocks": [],
                    })
                continue
            if not blocks:
                # Named rather than silently counted: "why did this day get
                # nothing" is the first question a bare skip count provokes.
                report["skipped"] += 1
                report["files"].append({
                    "date": journal_date, "file": path.name, "status": "empty", "blocks": [],
                })
                continue
            if len({block["anchor"] for block in blocks}) != len(blocks):
                raise ValueError("Journal contains duplicate timestamps and cannot be classified safely")
            # "Nur neue" legt dem Modell nur die offenen Bloecke vor; was es schon
            # gesehen hat, behaelt seine Hashtags. Die anderen Modi bewerten den
            # ganzen Tag neu.
            targets = _pending_blocks(blocks, previous_footer) if mode == "missing" else blocks
            if mode == "missing" and not targets:
                targets = blocks
            anchors = {block["anchor"] for block in targets}
            seen_before = _seen_anchors(blocks, previous_footer) if mode == "missing" else set()
            classification_body = "\n\n___\n\n".join(block["text"] for block in targets)
            prompt = _render_prompt(user_prompt_template, canonical, classification_body, established)
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
            # Global ausgeschlossene Hashtags verwirft der Lauf, bevor sie
            # irgendwo landen - auch nicht als Vorschlag im Katalog.
            excluded = tagging_module.ai_excluded_tags(user_id)
            proposed = [tag for tag in proposed if tag not in excluded]
            assigned_by_model = [
                tag for tag in (
                    tagging_module.normalise_tag(tag)
                    for tags in classified.values() for tag in tags
                )
                if tag not in excluded
            ]
            added = set(tagging_module.propose_tags(
                user_id,
                proposed + assigned_by_model + [tag for block in targets for tag in block["raw_tags"]],
            ) or [])
            catalogs = [tagging_module.read_catalog(user_id), tagging_module.read_catalog(family=True)]
            # Was der Nutzer einem Block schon einmal weggenommen hat, vergibt
            # auch ein spaeterer Lauf nicht wieder - in keinem Modus.
            # Global ausgeschlossene Hashtags fallen in jedem Modus heraus, auch
            # wenn das Modell einen Alias darauf nennt.
            rejected = tagging_module.footer_ai_rejected(previous_footer)
            assigned = {
                anchor: sorted(
                    {
                        tagging_module.filterable_tag(tag, catalogs) for tag in tags
                        if tagging_module.normalise_tag(tag) not in excluded
                    } - {""} - excluded
                    - {tagging_module.normalise_tag(tag) for tag in rejected.get(anchor, [])}
                )
                for anchor, tags in classified.items()
            }
            # The user's own hashtags stay untouched; only what the model adds on
            # top of them is recorded as machine origin. A block outside this
            # run's targets keeps what its footer already said, the model's
            # earlier hashtags included.
            previous_tags = (previous_footer or {}).get("blocks", {})
            previous_ai = tagging_module.footer_ai_tags(previous_footer)
            new_blocks, ai_tags = [], {}
            for block in blocks:
                anchor = block["anchor"]
                if anchor in anchors:
                    tags = set(block["raw_tags"]) | set(assigned.get(anchor, []))
                    ai_tags[anchor] = sorted(set(assigned.get(anchor, [])) - set(block["raw_tags"]))
                else:
                    # Was ein frueherer Lauf vergeben hat, bevor das Hashtag
                    # ausgeschlossen wurde, faellt beim Neuschreiben mit weg;
                    # selbst geschriebene Hashtags bleiben.
                    dropped = (set(previous_ai.get(anchor, [])) & excluded) - set(block["raw_tags"])
                    kept = previous_tags.get(anchor, [])
                    tags = set(block["raw_tags"]) | {tag for tag in kept if isinstance(tag, str) and tag not in dropped}
                    ai_tags[anchor] = [tag for tag in previous_ai.get(anchor, []) if tag not in dropped]
                new_blocks.append({**block, "raw_tags": sorted(tags)})
            ai_seen = seen_before | anchors
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
                    for block in targets
                ],
            }
            if not preview:
                updated_content = body.rstrip() + "\n\n" + tagging_module.render_footer(
                    new_blocks, catalogs, ai_tags,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    sorted(ai_seen), rejected,
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
            # Nur was dieser Lauf erstmals in den Katalog gebracht hat: ein
            # Vorschlag, der dort schon wartete, ist nicht "neu" und meldete
            # sich sonst bei jedem Lauf wieder. Selbst geschriebene Hashtags
            # aus alten Eintraegen zaehlen nicht, die kennt der Nutzer.
            open_proposals = set(catalogs[0].get("proposals", []))
            report["proposals"].extend(
                tag for tag in set(proposed) | set(assigned_by_model)
                if tag and tag in added and tag in open_proposals
                and tag not in {own for block in targets for own in block["raw_tags"]}
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


# ─── Lokale Verbindung (wie „Chat with your Notes") ───────────────────
# Die Verschlagwortung bekommt ihre eigene Verbindung statt eines Eintrags in
# ``ai_providers``: der Nutzer traegt Endpunkt und Modell direkt dort ein, wo er
# den Lauf startet, liest die Modellliste live aus und speichert. Damit braucht
# dieser Lauf weder eine Kennung ``lm_*`` noch einen Umweg ueber die
# Anbieterverwaltung — lokal bleibt er trotzdem, weil ``validated_base_url``
# ausschliesslich Loopback- oder private Adressen zulaesst (D-015).
STORE_NAME = "historical-tagging.json"


def _settings_path(user_id):
    return _core_module().DATA_DIR / user_id / ".config" / STORE_NAME


def _data_dir():
    return _core_module().DATA_DIR


def _empty_settings():
    return {"base_url": "", "model": "", "api_key": "", **tagging_job.stored_job({})}


def load_settings(user_id):
    """Verbindung und Job-Felder samt Geheimnis — nur serverseitig.

    Der Job „Hashtags nachtragen“ (Zeitplan, Zeitraum, Modus, Meldungen) steht in
    derselben Datei wie die Verbindung: es gibt pro Nutzer genau einen, und ein
    Speichern der Verbindung darf ihn nicht verlieren.
    """
    path = _settings_path(user_id)
    if not path.is_file() or path.is_symlink():
        return _empty_settings()
    try:
        stored = json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return _empty_settings()
    if not isinstance(stored, dict):
        return _empty_settings()
    return {
        "base_url": str(stored.get("base_url") or ""),
        "model": str(stored.get("model") or ""),
        "api_key": str(stored.get("api_key") or ""),
        **tagging_job.stored_job(stored),
    }


def public_settings(settings):
    """Die Sicht der Oberflaeche: der Schluessel selbst verlaesst den Server nie."""
    return {
        "base_url": settings["base_url"],
        "model": settings["model"],
        "has_api_key": bool(settings["api_key"]),
        "configured": bool(settings["base_url"] and settings["model"]),
        **{key: copy.deepcopy(settings[key]) for key in tagging_job.DEFAULTS},
    }


def save_settings(user_id, raw):
    """Verbindung und/oder Job pruefen und speichern.

    Die Verbindung wird nur geprueft, wenn ``base_url`` oder ``model`` mitkommt —
    so kann die Verwaltung den Zeitplan aendern, ohne die Verbindung erneut zu
    schicken. ``api_key`` ist optional und klebrig: ein Aufruf ohne das Feld
    erhaelt den gespeicherten Schluessel, ein leerer Wert loescht ihn. Ein
    aktiver Zeitplan ohne Verbindung wird abgelehnt: er liefe sonst jede Nacht
    ins Leere.
    """
    if not isinstance(raw, dict):
        raise ValueError("Ungueltige Einstellungen.")
    current = load_settings(user_id)
    if "base_url" in raw or "model" in raw:
        try:
            base_url = agent_projects.validated_base_url(raw.get("base_url"))
        except agent_projects.AgentProjectError as exc:
            raise ValueError(str(exc)) from exc
        model = str(raw.get("model") or "").strip()
        if not agent_projects.valid_model_name(model):
            raise ValueError("Bitte ein Modell waehlen.")
    else:
        base_url, model = current["base_url"], current["model"]
    if "api_key" in raw:
        api_key = str(raw.get("api_key") or "").strip()
        if len(api_key) > 400:
            raise ValueError("Der API-Schluessel ist zu lang.")
    else:
        api_key = current["api_key"]
    job = tagging_job.clean_job(raw, current)
    if job["enabled"] and job["schedule_mode"] != "manual" and not (base_url and model):
        raise ValueError("Für den Zeitplan zuerst eine Verbindung (Endpunkt und Modell) speichern.")
    settings = {"schema": 2, "base_url": base_url, "model": model, "api_key": api_key, **job}
    write_text_file(
        _settings_path(user_id),
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
    )
    return {key: value for key, value in settings.items() if key != "schema"}


def is_configured(settings):
    return bool(settings.get("base_url") and settings.get("model"))


def provider_from_settings(settings):
    """Eine gespeicherte Verbindung in die Form bringen, die ``_call_ai_api`` erwartet.

    Die Kennung beginnt bewusst mit ``lm_``: damit nutzt der Aufruf dieselbe
    Modellverfuegbarkeitspruefung wie ein LM-Studio-Anbieter und scheitert frueh,
    wenn ein Modell nicht geladen ist.
    """
    if not is_configured(settings):
        return None
    base = str(settings["base_url"]).rstrip("/")
    return {
        "id": "lm_historical_tagging",
        "api_url": base + "/v1/chat/completions",
        "model": str(settings["model"]),
        "api_key": str(settings.get("api_key") or ""),
    }


def discover_models(base_url):
    """Die lebende Modellliste eines Endpunkts — nie eine geratene."""
    try:
        return agent_projects.discover_models("openai", base_url)
    except agent_projects.AgentProjectError as exc:
        raise ValueError(str(exc)) from exc


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


def run_handler(handle, params):
    """Verschlagworte den ganzen Zeitraum, Journal fuer Journal, abbrechbar.

    Jede fertige Datei ist gestempelt, der Cursor wandert mit. Ein Abbruch —
    durch den Nutzer, durch einen toten Endpunkt oder durch einen Neustart —
    kostet deshalb nichts ausser der angefangenen Datei.
    """
    core = _core_module()
    config = core.load_config() if core else {}
    settings = load_settings(handle.user_id)
    provider = provider_from_settings(settings)
    if provider is None:
        raise background_runs.RunError(
            "Keine lokale Verbindung eingerichtet. Bitte unter Hashtags nachtragen "
            "einen Endpunkt und ein Modell speichern."
        )
    try:
        _prompt_settings(config.get("historical_tagging_ai"))
    except ValueError as exc:
        # Sonst scheiterte jeder Tag einzeln an derselben Ursache.
        raise background_runs.RunError(
            "Die Prompts der Verschlagwortung (historical_tagging_ai) fehlen in der "
            "Konfiguration: {}".format(exc)
        ) from exc
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
            max_files=1, preview=preview, name_skipped=True,
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


def run_params(settings, today, *, preview=False):
    """Die Parameter eines Job-Laufs: Zeitraum und Modus aus dem gespeicherten Job."""
    start, end = tagging_job.scheduled_range(settings, today)
    return {"start_date": start, "end_date": end, "mode": settings["mode"],
            "stale_before": "", "preview": bool(preview), "job": True}


def run_label(params):
    return "{} {} bis {}".format(
        "Vorschau Verschlagwortung" if params.get("preview") else "Verschlagwortung",
        params.get("start_date", ""), params.get("end_date", ""),
    )


def _local_now():
    return datetime.now(_local_tz()) if _local_tz() else datetime.now().astimezone()


def on_run_finished(record):
    """Nach jedem Lauf: Meldungen nach den Schaltern des Jobs ablegen.

    Ein Lauf des Zeitplans, der noch einen Wiederholungsversuch vor sich hat,
    meldet seinen Fehler nicht — das entscheidet der Sidecar (``notify_failure``).
    """
    params = record.get("params") or {}
    if record.get("status") == "failed" and params.get("defer_failure_notice"):
        return
    user_id = record.get("user_id") or ""
    if not user_id:
        return
    settings = load_settings(user_id)
    events = tagging_job.events_for_run(
        settings, status=record.get("status", ""), result=record.get("result") or {},
        error=record.get("error", ""), trigger=record.get("trigger", "manual"),
    )
    tagging_job.add_events(_data_dir(), user_id, events, _local_now())


def notify_failure(user_id, record):
    """Ein Fehlschlag des Zeitplans nach dem letzten Versuch: jetzt doch melden."""
    settings = load_settings(user_id)
    events = tagging_job.events_for_run(
        settings, status="failed", result=record.get("result") or {},
        error=record.get("error", ""), trigger=record.get("trigger", "schedule"),
    )
    tagging_job.add_events(_data_dir(), user_id, events, _local_now())


# Laeufe dieser Art sind das Protokoll des Jobs — sie bleiben laenger als ein
# Chat-Auftrag, damit ein Fehler der letzten Wochen noch nachlesbar ist.
RUN_RETENTION_DAYS = 30

background_runs.register(
    RUN_KIND, run_handler, on_finish=on_run_finished, retention_days=RUN_RETENTION_DAYS,
)
