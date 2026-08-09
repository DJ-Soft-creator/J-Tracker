#!/usr/bin/env python3
"""Host-side worker for explicit Journl AI jobs.

This process never receives browser keystrokes.  It only processes immutable
``queued`` JSON jobs created after an explicit KI-Senden action and records the
complete (potentially sensitive) request in a per-user audit log.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def atomic_json(path, value):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def under(root, relative):
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("job path escapes data root")
    return candidate


def audit(job_path, event):
    path = job_path.parent / "audit.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": now(), **event}, ensure_ascii=False) + "\n")


def terminate_process(process):
    """Stop only the isolated Pi process group started for this job."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def legacy_manifest(job, snapshot_paths):
    """Build safe default metadata for jobs created before Knowledge manifests."""
    snapshots = job.get("knowledge_snapshots")
    if not isinstance(snapshots, list) or len(snapshots) != len(snapshot_paths):
        raise ValueError("legacy knowledge snapshots are invalid")
    manifest = []
    for snapshot, snapshot_path in zip(snapshots, snapshot_paths):
        if not isinstance(snapshot, dict) or snapshot.get("tag") is None:
            raise ValueError("legacy knowledge snapshot is invalid")
        manifest.append({
            "tag": snapshot.get("tag"), "kind": "reference", "description": "",
            "scope": snapshot.get("scope"), "path": snapshot.get("path"),
            "snapshot_path": snapshot_path,
        })
    return manifest


def external_roots(config_path):
    """Read host-only external write roots; never trust the web job for them."""
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("host worker config is unavailable") from exc
    roots = {}
    for item in config.get("external_write_roots", []) if isinstance(config, dict) else []:
        if not isinstance(item, dict):
            continue
        root_id, path = item.get("id"), item.get("path")
        if not isinstance(root_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", root_id):
            continue
        try:
            candidate = Path(path)
            resolved = candidate.resolve(strict=True)
            if not candidate.is_absolute() or candidate.is_symlink() or not resolved.is_dir():
                continue
        except (OSError, TypeError):
            continue
        roots[root_id] = resolved
    return roots


def external_target(job, config_path):
    target = job.get("write_target")
    if not isinstance(target, dict) or target.get("scope") != "host":
        return None
    roots = external_roots(config_path)
    root = roots.get(target.get("root_id"))
    try:
        requested = Path(target.get("path"))
        current = Path(requested.anchor)
        for part in requested.parts[1:]:
            current /= part
            if current.is_symlink():
                raise ValueError
        resolved = requested.resolve(strict=True)
        if not root or requested.is_symlink() or not resolved.is_dir() or (resolved != root and root not in resolved.parents):
            raise ValueError
    except (OSError, TypeError, ValueError):
        raise ValueError("external write target is outside an allowed host root")
    if target.get("file_policy") not in {"markdown_only", "all_regular_files"}:
        raise ValueError("external write target has invalid file policy")
    return resolved


def build_external_manifest(job, job_path, config_path):
    target_root = external_target(job, config_path)
    if target_root is None:
        return None
    files, total = [], 0
    for path in sorted(target_root.rglob("*"), key=lambda item: item.as_posix()):
        try:
            if path.is_symlink() or not path.is_file() or ".write-ai-backup-" in path.name or path.name.endswith((".lock", ".tmp", ".bak")):
                continue
            if job["write_target"].get("file_policy") == "markdown_only" and path.suffix.casefold() != ".md":
                continue
            raw = path.read_bytes()
            if len(raw) > 250_000 or b"\0" in raw:
                continue
            content = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        total += len(raw)
        if len(files) >= 40 or total > 1_500_000:
            raise ValueError("external write target is too large")
        files.append({"path": path.relative_to(target_root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "content": content})
    if not files:
        raise ValueError("external write target has no eligible UTF-8 text files")
    manifest = {"tag": job["write_target"]["tag"], "root_id": job["write_target"]["root_id"],
                "path": job["write_target"]["path"], "file_policy": job["write_target"]["file_policy"], "files": files}
    path = job_path.with_name(f"{job['id']}.write-target.json")
    atomic_json(path, manifest)
    return path


def apply_external_proposal(job, job_path, config_path):
    target_root = external_target(job, config_path)
    manifest_path = job_path.with_name(f"{job['id']}.write-target.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshots = {item["path"]: item for item in manifest.get("files", []) if isinstance(item, dict)}
    edits = job.get("proposal", {}).get("edits", [])
    prepared = []
    for edit in edits:
        path_name = edit.get("path") if isinstance(edit, dict) else None
        if path_name not in snapshots or not isinstance(edit.get("content"), str) or "\0" in edit["content"]:
            raise ValueError("external proposal is invalid")
        relative = Path(path_name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != path_name:
            raise ValueError("external proposal path is invalid")
        path = target_root / relative
        if path.is_symlink() or not path.is_file() or path.resolve().parent != path.parent.resolve():
            raise ValueError("external proposal file is unavailable")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != edit.get("expected_sha256"):
            raise ValueError(f"external file changed: {path_name}")
        prepared.append((path, raw, edit["content"]))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for path, raw, content in prepared:
        backup = path.with_name(f"{path.name}.write-ai-backup-{stamp}")
        backup.write_bytes(raw)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.write-ai.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    return f"Externes Schreibziel angewendet: {', '.join(path.relative_to(target_root).as_posix() for path, _, _ in prepared)}. Backups wurden neben den Dateien angelegt."


def process(job_path, root, runner, config_path):
    # ``scheduling.write_text_file`` intentionally leaves ``.json.lock`` files
    # behind as stable lock paths.  Do not mistake them for an active job lock.
    lock = job_path.with_suffix(job_path.suffix + ".worker.lock")
    lock_handle = lock.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        return
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        # Jobs are discovered below <data-root>/<user-id>/ai_jobs.  Do not trust
        # paths or user_id merely because they came from the JSON payload: a
        # compromised/misconfigured producer must not make the host worker read
        # or write another user's directory.
        owner_dir = job_path.parent.parent
        owner_id = owner_dir.name
        if owner_dir.parent != root or job_path.parent.name != "ai_jobs":
            raise ValueError("job is outside a per-user ai_jobs directory")
        if not owner_id or Path(owner_id).name != owner_id or owner_id in {".", ".."}:
            raise ValueError("invalid job owner")
        if job.get("user_id") != owner_id:
            raise ValueError("job owner does not match its directory")
        if job.get("id") != job_path.stem:
            raise ValueError("job id does not match its filename")
        user_root = root / owner_id
        if job.get("status") == "apply_requested":
            if job.get("write_target", {}).get("scope") != "host":
                raise ValueError("only external write targets are host-applied")
            summary = apply_external_proposal(job, job_path, config_path)
            job.update(status="applied", applied_at=now(), apply_summary=summary)
            atomic_json(job_path, job)
            audit(job_path, {"event": "applied", "job": job})
            return
        if job.get("status") != "queued":
            return
        if job.get("agent") != "pi":
            job.update(status="error", completed_at=now(), error="host worker only accepts agent=pi")
            atomic_json(job_path, job)
            audit(job_path, {"event": "rejected", "job": job})
            return
        source = under(root, str(job["source_path"]))
        prompt = under(root, str(job["prompt_path"]))
        section = under(root, str(job["section_path"]))
        for path in (source, prompt, section):
            path.relative_to(user_root)
        if not source.is_file() or not prompt.is_file() or not section.is_file():
            raise ValueError("one or more required job files are missing")
        snapshot_paths = job.get("knowledge_snapshot_paths", [])
        if not isinstance(snapshot_paths, list) or any(not isinstance(item, str) for item in snapshot_paths):
            raise ValueError("knowledge snapshot paths are invalid")
        context_files = []
        for relative in snapshot_paths:
            expected_prefix = f"{job.get('user_id', '')}/ai_jobs/{job.get('id', '')}.knowledge-"
            if not relative.startswith(expected_prefix):
                raise ValueError("knowledge snapshot path is not owned by this job")
            snapshot = under(root, relative)
            if snapshot.suffix != ".md" or not snapshot.is_file():
                raise ValueError("knowledge snapshot is missing or invalid")
            context_files.append(snapshot)
        manifest = None
        manifest_relative = job.get("knowledge_manifest_path")
        if context_files:
            expected_manifest = f"{job.get('user_id', '')}/ai_jobs/{job.get('id', '')}.knowledge.json"
            if manifest_relative is None:
                # A queued job can have been created by the previous web app
                # version while this worker was being upgraded.  Preserve its
                # explicit snapshots, but classify them conservatively.
                manifest_relative = expected_manifest
                manifest = under(root, manifest_relative)
                atomic_json(manifest, legacy_manifest(job, snapshot_paths))
                job["knowledge_manifest_path"] = manifest_relative
            elif manifest_relative != expected_manifest:
                raise ValueError("knowledge manifest path is invalid")
            manifest = under(root, manifest_relative)
            if not manifest.is_file():
                raise ValueError("knowledge manifest is missing")
            try:
                manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("knowledge manifest is invalid") from exc
            if not isinstance(manifest_data, list) or len(manifest_data) != len(context_files):
                raise ValueError("knowledge manifest does not match snapshots")
            for item, snapshot_path in zip(manifest_data, snapshot_paths):
                if not isinstance(item, dict) or item.get("snapshot_path") != snapshot_path:
                    raise ValueError("knowledge manifest does not own snapshot")
                tag, scope, path = item.get("tag"), item.get("scope"), item.get("path")
                if not isinstance(tag, str) or not re.fullmatch(r"[\w-]{1,80}", tag, re.UNICODE):
                    raise ValueError("knowledge manifest has invalid tag")
                if scope not in {"personal", "family"} or not isinstance(path, str) or len(path) > 240 or "\n" in path or "\r" in path:
                    raise ValueError("knowledge manifest has invalid source")
                if item.get("kind") not in {"reference", "constraints", "glossary", "examples"}:
                    raise ValueError("knowledge manifest has invalid kind")
                description = item.get("description", "")
                if not isinstance(description, str) or len(description) > 240 or "\n" in description or "\r" in description:
                    raise ValueError("knowledge manifest has invalid description")
        elif manifest_relative is not None:
            raise ValueError("empty job must not include a knowledge manifest")
        command = [str(runner), "--agent", "pi", "--model", str(job["model"]), "--context", "none",
                   "--source", str(source), "--prompt-file", str(prompt), "--request-file", str(source),
                   "--data-root", str(root), "--user-root", str(user_root),
                   "--session-id", str(job["session_id"]),
                   "--actor", str(owner_id), "--expected-revision", str(job.get("expected_revision", ""))]
        if manifest:
            command.extend(["--knowledge-manifest", str(manifest)])
            for snapshot in context_files:
                command.extend(["--context-file", str(snapshot)])
        write_manifest = None
        write_manifest_relative = job.get("write_target_manifest_path")
        if job.get("write_target", {}).get("scope") == "host":
            if write_manifest_relative is not None:
                raise ValueError("external write job already has a manifest before worker snapshot")
            write_manifest = build_external_manifest(job, job_path, config_path)
            write_manifest_relative = f"{owner_id}/ai_jobs/{job['id']}.write-target.json"
            job["write_target_manifest_path"] = write_manifest_relative
        if write_manifest_relative is not None:
            expected_write_manifest = f"{owner_id}/ai_jobs/{job['id']}.write-target.json"
            if write_manifest_relative != expected_write_manifest:
                raise ValueError("write target manifest path is invalid")
            write_manifest = under(root, write_manifest_relative)
            if not write_manifest.is_file():
                raise ValueError("write target manifest is missing")
            data = json.loads(write_manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("files"), list) or len(data["files"]) > 40:
                raise ValueError("write target manifest is invalid")
            command.extend(["--write-target-manifest", str(write_manifest)])
        document_relative = job.get("document_context_path")
        if document_relative is not None:
            expected_context = f"{job.get('user_id', '')}/ai_jobs/{job.get('id', '')}.context.md"
            if document_relative != expected_context:
                raise ValueError("document context path is invalid")
            document_context = under(root, document_relative)
            if not document_context.is_file():
                raise ValueError("document context is missing")
            command.extend(["--document-context-file", str(document_context), "--document-context-label", "heutiges Journal"])
        # Honour a cancellation written between initial discovery and the Pi
        # process start.  This closes the queued-job race without touching Pi.
        latest = json.loads(job_path.read_text(encoding="utf-8"))
        if latest.get("status") != "queued":
            return
        job.update(status="running", started_at=now(), runner_args=command[1:])
        atomic_json(job_path, job)
        # The full job contains the exact prompt/context sent to Pi.  It is
        # deliberately audit-logged only on the protected host data volume.
        audit(job_path, {"event": "started", "job": job})
        process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        cancelled = timed_out = False
        deadline = time.monotonic() + 360
        while process.poll() is None:
            try:
                current = json.loads(job_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = {}
            if current.get("status") == "cancelling":
                cancelled = True
                terminate_process(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                terminate_process(process)
                break
            time.sleep(0.2)
        stdout, stderr = process.communicate()
        if cancelled:
            job.update(status="cancelled", cancelled_at=now(), exit_code=process.returncode,
                       stdout=stdout[-4000:], stderr=stderr[-4000:])
        elif timed_out:
            job.update(status="error", completed_at=now(), exit_code=process.returncode,
                       stdout=stdout[-4000:], stderr=stderr[-4000:], error="Pi runner timed out")
        else:
            job.update(status="completed" if process.returncode == 0 else "error", completed_at=now(),
                       exit_code=process.returncode, stdout=stdout[-4000:], stderr=stderr[-4000:])
            if process.returncode == 0 and write_manifest:
                source_text = source.read_text(encoding="utf-8")
                match = re.search(r"<!-- jt:agent-session \{.*?\} -->\s*\n\s*(.*?)\s*\n\s*___\s*$", source_text, re.S)
                try:
                    proposal = json.loads(match.group(1).strip() if match else "")
                    files = {item["path"]: item for item in json.loads(write_manifest.read_text(encoding="utf-8"))["files"]}
                    edits = proposal.get("edits") if isinstance(proposal, dict) else None
                    if not isinstance(edits, list) or len(edits) > len(files):
                        raise ValueError("invalid edits")
                    clean = []
                    for edit in edits:
                        path = edit.get("path") if isinstance(edit, dict) else None
                        if path not in files or edit.get("expected_sha256") != files[path]["sha256"] or not isinstance(edit.get("content"), str):
                            raise ValueError("invalid edit")
                        if len(edit["content"].encode("utf-8")) > 250_000:
                            raise ValueError("edit too large")
                        clean.append({"path": path, "expected_sha256": edit["expected_sha256"], "content": edit["content"]})
                    job.update(status="proposed", proposal={"summary": str(proposal.get("summary") or "Änderungsvorschlag bereit.")[:1000], "edits": clean})
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    job.update(status="error", error="Pi hat keinen gültigen Schreibvorschlag geliefert")
        if job.get("status") == "error" and not job.get("error"):
            job["error"] = "Pi runner failed; inspect the protected audit log."
        atomic_json(job_path, job)
        audit(job_path, {"event": "finished", "job": job})
    except Exception as exc:
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception:
            job = {}
        job.update(status="error", completed_at=now(), error=str(exc)[:500])
        atomic_json(job_path, job)
        audit(job_path, {"event": "error", "job": job})
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--runner", required=True, type=Path)
    parser.add_argument("--host-config", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    root = args.data_root.resolve()
    config_path = args.host_config.resolve()
    if not root.is_dir() or not args.runner.is_file() or not config_path.is_file():
        raise SystemExit("data root or runner is unavailable")
    while True:
        for job_path in root.glob("*/ai_jobs/*.json"):
            # Knowledge manifests live beside jobs but are JSON arrays, not
            # executable jobs.  Only canonical UUID job filenames are valid.
            if not re.fullmatch(r"[0-9a-f-]{36}\.json", job_path.name):
                continue
            process(job_path, root, args.runner, config_path)
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    main()
