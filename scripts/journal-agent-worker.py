#!/usr/bin/env python3
"""Host-side worker for explicit Journl AI jobs.

This process never receives browser keystrokes.  It only processes immutable
``queued`` JSON jobs created after an explicit KI-Senden action and records the
complete (potentially sensitive) request in a per-user audit log.
"""
import argparse
import fcntl
import json
import os
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


def process(job_path, root, runner):
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
        command = [str(runner), "--agent", "pi", "--model", str(job["model"]), "--context", "section",
                   "--source", str(source), "--prompt-file", str(prompt), "--section-file", str(section),
                   "--data-root", str(root), "--session-id", str(job["session_id"]),
                   "--actor", str(job.get("user_id", "")), "--expected-revision", str(job.get("expected_revision", ""))]
        for snapshot in context_files:
            command.extend(["--context-file", str(snapshot)])
        job.update(status="running", started_at=now(), runner_args=command[1:])
        atomic_json(job_path, job)
        # The full job contains the exact prompt/context sent to Pi.  It is
        # deliberately audit-logged only on the protected host data volume.
        audit(job_path, {"event": "started", "job": job})
        result = subprocess.run(command, text=True, capture_output=True, timeout=360)
        job.update(status="completed" if result.returncode == 0 else "error", completed_at=now(),
                   exit_code=result.returncode, stdout=result.stdout[-4000:], stderr=result.stderr[-4000:])
        if result.returncode != 0:
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
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    root = args.data_root.resolve()
    if not root.is_dir() or not args.runner.is_file():
        raise SystemExit("data root or runner is unavailable")
    while True:
        for job_path in root.glob("*/ai_jobs/*.json"):
            process(job_path, root, args.runner)
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    main()
