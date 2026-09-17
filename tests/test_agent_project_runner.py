"""Host runner contract for Agent-Projekte.

These tests execute the real ``scripts/run-agent-session.sh`` with stub
``opencode``/``pi`` executables on PATH.  They pin the part of the contract this
repository owns: the argv and environment handed to the agent, the per-mode
permission matrix, the answer extraction and the revision-checked write-back
into the session document — without depending on a reachable model.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-agent-session.sh"

OPENCODE_STUB = """#!/usr/bin/env bash
set -euo pipefail
: > "$STUB_RECORD"
printf 'ARGV %s\\n' "$*" >> "$STUB_RECORD"
printf 'CWD %s\\n' "$PWD" >> "$STUB_RECORD"
printf 'PERMISSION %s\\n' "${OPENCODE_PERMISSION:-unset}" >> "$STUB_RECORD"
printf 'CONFIG %s\\n' "${OPENCODE_CONFIG_CONTENT:-unset}" >> "$STUB_RECORD"
printf 'PROJECT_CONFIG %s\\n' "${OPENCODE_DISABLE_PROJECT_CONFIG:-unset}" >> "$STUB_RECORD"
while (($#)); do
  if [[ "$1" == "--" ]]; then shift; printf 'MESSAGE %s\\n' "$1" >> "$STUB_RECORD"; break; fi
  shift
done
cat <<'EOF'
{"type":"step_start","sessionID":"ses_stub123","part":{"type":"step-start"}}
{"type":"text","sessionID":"ses_stub123","part":{"type":"text","text":"## Bericht"}}
{"type":"text","sessionID":"ses_stub123","part":{"type":"text","text":"app/ enthaelt die Flask-App."}}
{"type":"step_finish","sessionID":"ses_stub123","part":{"type":"step-finish","reason":"stop"}}
EOF
"""

PI_STUB = """#!/usr/bin/env bash
set -euo pipefail
: > "$STUB_RECORD"
printf 'ARGV %s\\n' "$*" >> "$STUB_RECORD"
printf 'CWD %s\\n' "$PWD" >> "$STUB_RECORD"
cat > /dev/null
printf 'Pi-Bericht: nur gelesen.\\n'
"""

CLAUDE_STUB = """#!/usr/bin/env bash
set -euo pipefail
: > "$STUB_RECORD"
printf 'ARGV %s\\n' "$*" >> "$STUB_RECORD"
printf 'CWD %s\\n' "$PWD" >> "$STUB_RECORD"
printf 'MESSAGE %s\\n' "$(cat)" >> "$STUB_RECORD"
cat <<'EOF'
{"type":"result","subtype":"success","is_error":false,"session_id":"claude_stub123","result":"## Bericht\\napp/ enthaelt die Flask-App."}
EOF
"""

FAILING_CLAUDE_STUB = """#!/usr/bin/env bash
set -euo pipefail
cat <<'EOF'
{"type":"result","subtype":"error_during_execution","is_error":true,"session_id":"claude_stub999","result":"model unreachable"}
EOF
"""

FAILING_OPENCODE_STUB = """#!/usr/bin/env bash
set -euo pipefail
cat <<'EOF'
{"type":"step_start","sessionID":"ses_stub999","part":{"type":"step-start"}}
{"type":"error","sessionID":"ses_stub999","error":{"name":"ProviderAuthError"}}
EOF
"""

CONFIG_MARKER = "<!-- jt:agent-session-config"


@unittest.skipUnless(RUNNER.is_file() and shutil.which("flock") and shutil.which("realpath"),
                     "the host runner needs flock and realpath")
class AgentProjectRunnerTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(os.environ.get("TMPDIR", "/tmp")) / f"journl-runner-{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.data_root = self.base / "data"
        self.user_root = self.data_root / "user-a"
        self.jobs = self.user_root / "ai_jobs"
        self.sessions = self.user_root / "ai-agent"
        self.project = self.base / "code" / "journl"
        for path in (self.jobs, self.sessions, self.project / "app"):
            path.mkdir(parents=True)
        (self.project / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")

        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.record = self.base / "stub-record.txt"

        self.job_id = "11111111-2222-3333-4444-555555555555"
        self.session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.session_path = self.sessions / "Agent-Session_2026-08-16_12-00-00.md"
        self.prompt = self.jobs / f"{self.job_id}.prompt.md"
        self.request = self.jobs / f"{self.job_id}.request.md"
        self.manifest = self.jobs / f"{self.job_id}.connection.json"
        self.prompt.write_text("Du bist der Journl-Agent.\n", encoding="utf-8")
        self.request.write_text("Beschreibe app/ in zwei Saetzen.\n", encoding="utf-8")
        self.manifest.write_text(json.dumps({
            "kind": "lmstudio", "base_url": "http://127.0.0.1:8778", "provider_id": "lmstudio",
            "model": "fake-coder-7b", "api_key_env": "", "runner": "opencode",
        }, indent=2) + "\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # ─── fixture helpers ──────────────────────────────────────────────
    def _stub(self, name, body):
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _write_session(self, mode="read"):
        config = {
            "schema": 1, "session_id": self.session_id, "status": "active",
            "workflow_tag": "projekt-journl", "agent": "opencode", "model": "fake-coder-7b",
            "kind": "agent-project", "project_mode": mode, "runner_session": "",
            "source_revision": "",
        }
        content = (
            f"---\nid: {self.session_id}\ntype: ai-agent-session\nstatus: active\n---\n\n"
            "# Agent-Projekt: Journl\n\n" + CONFIG_MARKER + "\n"
            + json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n-->\n"
            "\n\n___\n\n## Benutzeranfrage | Datum & Uhrzeit: 2026-08-16T12:01:00+02:00\n"
            '<!-- jt:agent-session-event {"at": "2026-08-16T12:01:00+02:00"} -->\n\n'
            "Beschreibe app/ in zwei Saetzen.\n\n___\n"
        )
        self.session_path.write_text(content, encoding="utf-8")
        neutral = re.sub(r'"source_revision"\s*:\s*"[^"]*"', '"source_revision":""', content)
        return hashlib.sha256(neutral.encode("utf-8")).hexdigest()

    def _run(self, *, agent="opencode", mode="read", revision=None, extra=()):
        command = [
            str(RUNNER), "--agent", agent, "--model", "lmstudio/fake-coder-7b",
            "--context", "none", "--source", str(self.session_path),
            "--prompt-file", str(self.prompt), "--request-file", str(self.request),
            "--data-root", str(self.data_root), "--user-root", str(self.user_root),
            "--session-id", self.session_id, "--actor", "user-a", "--actor-name", "Alex",
            "--expected-revision", revision or "",
            "--project-dir", str(self.project), "--project-mode", mode,
            "--connection-manifest", str(self.manifest), *extra,
        ]
        environment = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "STUB_RECORD": str(self.record),
        }
        return subprocess.run(command, capture_output=True, text=True, timeout=120, env=environment)

    def _record(self):
        return self.record.read_text(encoding="utf-8")

    def _session(self):
        return self.session_path.read_text(encoding="utf-8")

    # ─── opencode ─────────────────────────────────────────────────────
    def test_opencode_receives_project_dir_model_and_message(self):
        revision = self._write_session()
        self._stub("opencode", OPENCODE_STUB)
        result = self._run(revision=revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self._record()
        self.assertIn("--dir " + str(self.project), record)
        self.assertIn("--model lmstudio/fake-coder-7b", record)
        self.assertIn("--format json", record)
        self.assertIn("PROJECT_CONFIG 1", record)
        self.assertIn("Beschreibe app/ in zwei Saetzen.", record)

    def test_injected_provider_points_at_the_selected_endpoint(self):
        revision = self._write_session()
        self._stub("opencode", OPENCODE_STUB)
        self._run(revision=revision)
        config_line = next(line for line in self._record().splitlines() if line.startswith("CONFIG "))
        config = json.loads(config_line[len("CONFIG "):])
        provider = config["provider"]["lmstudio"]
        self.assertEqual(provider["options"]["baseURL"], "http://127.0.0.1:8778/v1")
        self.assertEqual(provider["npm"], "@ai-sdk/openai-compatible")
        self.assertIn("fake-coder-7b", provider["models"])

    def test_permission_matrix_matches_the_mode(self):
        expected = {
            "read": {"edit": "deny", "bash": "deny", "webfetch": "deny"},
            "write": {"edit": "allow", "bash": "deny", "webfetch": "deny"},
            "execute": {"edit": "allow", "bash": "allow", "webfetch": "deny"},
        }
        for mode, permission in expected.items():
            with self.subTest(mode=mode):
                revision = self._write_session(mode)
                self._stub("opencode", OPENCODE_STUB)
                result = self._run(mode=mode, revision=revision)
                self.assertEqual(result.returncode, 0, result.stderr)
                line = next(item for item in self._record().splitlines()
                            if item.startswith("PERMISSION "))
                self.assertEqual(json.loads(line[len("PERMISSION "):]), permission)

    # ─── Claude Code als Runner ───────────────────────────────────────
    def test_claude_runs_non_interactively_in_the_project_folder(self):
        revision = self._write_session()
        self._stub("claude", CLAUDE_STUB)
        result = self._run(agent="claude", revision=revision)
        self.assertEqual(result.returncode, 0, result.stderr)

        record = self._record()
        argv = next(line for line in record.splitlines() if line.startswith("ARGV "))
        # Non-interaktiv, maschinenlesbar, und im freigegebenen Projektordner.
        self.assertIn("-p", argv)
        self.assertIn("--output-format json", argv)
        self.assertIn(f"CWD {self.project}", record)

        session = self._session()
        self.assertIn("app/ enthaelt die Flask-App.", session)
        self.assertIn('"runner_session": "claude_stub123"', session)

    def test_claude_tool_allowlist_matches_the_mode(self):
        """Fail-closed wie bei Pi: der Lesemodus kennt keine Schreibwerkzeuge."""
        expected = {
            "read": ("Read Glob Grep", "plan", ("Edit", "Write", "Bash")),
            "write": ("Read Glob Grep Edit Write", "acceptEdits", ("Bash",)),
            "execute": ("Read Glob Grep Edit Write Bash", "acceptEdits", ()),
        }
        for mode, (tools, permission, forbidden) in expected.items():
            with self.subTest(mode=mode):
                revision = self._write_session(mode)
                self._stub("claude", CLAUDE_STUB)
                result = self._run(agent="claude", mode=mode, revision=revision)
                self.assertEqual(result.returncode, 0, result.stderr)
                argv = next(line for line in self._record().splitlines()
                            if line.startswith("ARGV "))
                self.assertIn(f"--allowedTools {tools}", argv)
                self.assertIn(f"--permission-mode {permission}", argv)
                for tool in forbidden:
                    self.assertNotIn(tool, argv.split("--allowedTools ")[1])

    def test_claude_continues_an_existing_runner_session(self):
        revision = self._write_session()
        self._stub("claude", CLAUDE_STUB)
        result = self._run(agent="claude", revision=revision,
                           extra=("--runner-session", "claude_previous"))
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = next(line for line in self._record().splitlines() if line.startswith("ARGV "))
        # Fortsetzen statt den Verlauf erneut zu senden (D-016).
        self.assertIn("--resume claude_previous", argv)
        self.assertNotIn("--session-id", argv)

    def test_a_failing_claude_run_leaves_the_session_untouched(self):
        revision = self._write_session()
        before = self.session_path.read_text(encoding="utf-8")
        self._stub("claude", FAILING_CLAUDE_STUB)
        result = self._run(agent="claude", revision=revision)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.session_path.read_text(encoding="utf-8"), before)

    def test_answer_is_appended_and_the_runner_session_is_captured(self):
        revision = self._write_session()
        self._stub("opencode", OPENCODE_STUB)
        result = self._run(revision=revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        session = self._session()
        self.assertIn("## Bericht", session)
        self.assertIn("app/ enthaelt die Flask-App.", session)
        self.assertIn("jt:agent-session ", session)
        self.assertIn('"runner_session": "ses_stub123"', session)
        self.assertTrue((self.session_path.parent / (self.session_path.name + ".agent-backup")).is_file())

    def test_continuing_a_session_forwards_the_runner_session(self):
        revision = self._write_session()
        self._stub("opencode", OPENCODE_STUB)
        result = self._run(revision=revision, extra=("--runner-session", "ses_previous"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--session ses_previous", self._record())

    def test_an_agent_error_event_fails_without_touching_the_session(self):
        revision = self._write_session()
        self._stub("opencode", FAILING_OPENCODE_STUB)
        before = self._session()
        result = self._run(revision=revision)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._session(), before)

    def test_a_stale_revision_refuses_the_write_back(self):
        self._write_session()
        self._stub("opencode", OPENCODE_STUB)
        result = self._run(revision="0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("revision changed", result.stderr)
        self.assertNotIn("## Bericht", self._session())

    # ─── pi ───────────────────────────────────────────────────────────
    def test_pi_project_mode_uses_a_strict_tool_allowlist(self):
        for mode, expected in (("read", "--tools read"), ("write", "--tools read,edit,write")):
            with self.subTest(mode=mode):
                revision = self._write_session(mode)
                self._stub("pi", PI_STUB)
                result = self._run(agent="pi", mode=mode, revision=revision)
                self.assertEqual(result.returncode, 0, result.stderr)
                record = self._record()
                self.assertIn(expected, record)
                self.assertIn("CWD " + str(self.project), record)
                self.assertIn("Pi-Bericht: nur gelesen.", self._session())

    def test_pi_execute_mode_keeps_every_builtin_tool(self):
        revision = self._write_session("execute")
        self._stub("pi", PI_STUB)
        result = self._run(agent="pi", mode="execute", revision=revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--tools", self._record())

    # ─── guards ───────────────────────────────────────────────────────
    def test_a_project_dir_inside_the_data_root_is_refused(self):
        revision = self._write_session()
        self._stub("opencode", OPENCODE_STUB)
        command_project = self.user_root / "notes"
        command_project.mkdir()
        result = subprocess.run([
            str(RUNNER), "--agent", "opencode", "--model", "lmstudio/fake-coder-7b",
            "--context", "none", "--source", str(self.session_path),
            "--prompt-file", str(self.prompt), "--request-file", str(self.request),
            "--data-root", str(self.data_root), "--user-root", str(self.user_root),
            "--session-id", self.session_id, "--expected-revision", revision,
            "--project-dir", str(command_project), "--project-mode", "write",
            "--connection-manifest", str(self.manifest),
        ], capture_output=True, text=True, timeout=60,
            env={**os.environ, "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
                 "STUB_RECORD": str(self.record)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside --data-root", result.stderr)

    def test_a_symlinked_project_dir_is_refused(self):
        revision = self._write_session()
        self._stub("opencode", OPENCODE_STUB)
        link = self.base / "code" / "link"
        link.symlink_to(self.project)
        result = self._run(revision=revision, extra=())
        self.assertEqual(result.returncode, 0, result.stderr)  # control: the real folder works
        revision = self._write_session()
        command = [
            str(RUNNER), "--agent", "opencode", "--model", "lmstudio/fake-coder-7b",
            "--context", "none", "--source", str(self.session_path),
            "--prompt-file", str(self.prompt), "--request-file", str(self.request),
            "--data-root", str(self.data_root), "--user-root", str(self.user_root),
            "--session-id", self.session_id, "--expected-revision", revision,
            "--project-dir", str(link), "--project-mode", "read",
            "--connection-manifest", str(self.manifest),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                env={**os.environ, "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
                                     "STUB_RECORD": str(self.record)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-symlinked directory", result.stderr)

    def test_an_invalid_project_mode_is_refused(self):
        revision = self._write_session()
        self._stub("opencode", OPENCODE_STUB)
        result = self._run(mode="root", revision=revision)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--project-mode must be", result.stderr)

    def test_dry_run_validates_without_calling_an_agent(self):
        revision = self._write_session()
        result = self._run(revision=revision, extra=("--dry-run",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mode=read", result.stdout)
        self.assertFalse(self.record.exists())


def _load_worker():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "journl_agent_worker", ROOT / "scripts" / "journal-agent-worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentProjectWorkerGuardTests(unittest.TestCase):
    """The host worker must re-derive every project decision from its own config."""

    def setUp(self):
        self.worker = _load_worker()
        self.base = Path(os.environ.get("TMPDIR", "/tmp")) / f"journl-worker-{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.writable = self.base / "code"
        self.readable = self.base / "docs"
        (self.writable / "journl").mkdir(parents=True)
        (self.readable / "manuals").mkdir(parents=True)
        self.jobs = self.base / "data" / "user-a" / "ai_jobs"
        self.jobs.mkdir(parents=True)
        self.config = self.base / "host_worker.json"
        self.config.write_text(json.dumps({
            "external_write_roots": [{"id": "code", "path": str(self.writable), "label": "Code"}],
            "external_read_roots": [{"id": "docs", "path": str(self.readable), "label": "Docs"}],
        }), encoding="utf-8")
        self.connection = {"kind": "lmstudio", "base_url": "http://127.0.0.1:1234",
                           "provider_id": "lmstudio", "model": "qwen3", "api_key_env": "",
                           "runner": "opencode"}

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _job(self, *, mode="write", path=None, connection=None):
        job_id = "11111111-2222-3333-4444-555555555555"
        connection = connection or self.connection
        (self.jobs / f"{job_id}.connection.json").write_text(
            json.dumps(connection), encoding="utf-8")
        return {
            "id": job_id, "user_id": "user-a", "job_kind": "agent_project", "agent": "opencode",
            "project": {"mode": mode, "path": str(path or (self.writable / "journl"))},
            "connection": connection, "runner_session": "",
            "connection_manifest_path": f"user-a/ai_jobs/{job_id}.connection.json",
        }

    def _resolve(self, job):
        return self.worker.agent_project_manifest(
            job, self.jobs / f"{job['id']}.json", self.config)

    def test_a_writable_root_allows_write_mode(self):
        resolved = self._resolve(self._job(mode="write"))
        self.assertEqual(resolved["project_dir"], (self.writable / "journl").resolve())
        self.assertEqual(resolved["mode"], "write")

    def test_a_read_only_root_allows_only_read_mode(self):
        self.assertEqual(self._resolve(self._job(mode="read", path=self.readable / "manuals"))["mode"], "read")
        with self.assertRaises(ValueError):
            self._resolve(self._job(mode="write", path=self.readable / "manuals"))

    def test_a_folder_outside_every_root_is_refused(self):
        outside = self.base / "elsewhere"
        outside.mkdir()
        with self.assertRaises(ValueError):
            self._resolve(self._job(mode="read", path=outside))

    def test_a_manifest_that_drifted_from_the_job_is_refused(self):
        job = self._job()
        (self.jobs / f"{job['id']}.connection.json").write_text(
            json.dumps({**self.connection, "base_url": "http://10.0.0.9:1234"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self._resolve(job)

    def test_only_local_network_endpoints_are_accepted(self):
        for base_url in ("http://93.184.216.34:1234", "https://example.com",
                         "http://169.254.169.254", "http://127.0.0.1:1234/v1/chat"):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    self.worker.validate_connection({**self.connection, "base_url": base_url})
        self.worker.validate_connection({**self.connection, "base_url": "http://127.0.0.1:1234"})
        self.worker.validate_connection({**self.connection, "base_url": "http://192.168.7.5:11434"})

    def test_an_unknown_runner_or_model_is_refused(self):
        for patch in ({"runner": "sh"}, {"model": "bad model"}, {"provider_id": "Bad"},
                      {"kind": "anything"}, {"api_key_env": "lower"}):
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):
                    self.worker.validate_connection({**self.connection, **patch})

    def test_a_forged_runner_session_is_refused(self):
        job = self._job()
        job["runner_session"] = "ses_x; rm -rf /"
        with self.assertRaises(ValueError):
            self._resolve(job)


if __name__ == "__main__":
    unittest.main()
