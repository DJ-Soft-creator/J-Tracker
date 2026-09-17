"""Vertrag des Ticket-Abholers.

Geprueft werden die Regeln des Verarbeitungsvertrags: nur `freigegeben` ist ein
Auftrag, die Ticket-Markdown bleibt unangetastet, ein Lauf wird nachweisbar
festgehalten, und ein Fehlschlag bleibt liegen statt sich zu wiederholen.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ticket_runner  # noqa: E402

GIT_AVAILABLE = shutil.which("git") is not None

CLAUDE_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" > "$STUB_ARGV"
cat > "$STUB_INPUT"
[[ -z "${STUB_TOUCH:-}" ]] || printf 'geaendert\\n' > "$STUB_TOUCH"
printf '%s\\n' '{"type":"result","is_error":false,"session_id":"s1","result":"Bericht: app/ geprueft."}'
"""

ASKING_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" > "$STUB_ARGV"
cat > "$STUB_INPUT"
cat > .journl-frage.json <<'JSON'
{"questions": [
  {"type": "boolean", "question": "Gilt das auch fuer Mobile?"},
  {"type": "choice", "question": "Welche Variante?", "options": ["A", "B"]},
  {"type": "choice", "question": "Ohne Optionen?", "options": []}
]}
JSON
printf '%s\\n' '{"type":"result","is_error":false,"session_id":"sess-42","result":"Ich brauche eine Entscheidung."}'
"""

PI_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" > "$STUB_ARGV"
cat > "$STUB_INPUT"
printf '%s\\n' 'Bericht: app/ geprueft.'
"""

FAILING_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' '{"type":"result","is_error":true,"session_id":"s2","result":"model unreachable"}'
"""


@unittest.skipUnless(GIT_AVAILABLE, "git is required for worktree handling")
class TicketRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.inbox = self.repo / "docs" / "tickets" / "inbox"
        self.inbox.mkdir(parents=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()

        self._git("init", "--initial-branch=dev-environment")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        # Wie im echten Repo: eingereichte Tickets sind Nutzerinhalt und
        # bleiben unversioniert, damit der git-Status sauber bleibt (E-03).
        (self.repo / ".gitignore").write_text("docs/tickets/inbox/\n", encoding="utf-8")
        (self.repo / "CLAUDE.md").write_text("# Projektregeln\nKurz halten.\n", encoding="utf-8")
        (self.repo / "app.py").write_text("print('hallo')\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "erste Fassung")

        self.data_root = self.base / "data"
        (self.data_root / "user-1" / "tickets").mkdir(parents=True)
        self.config_path = self.base / "host_worker.json"
        self.config_path.write_text(json.dumps({
            "schema": 1,
            "tickets": {
                "enabled": True,
                "inbox": str(self.inbox),
                "repo": str(self.repo),
                "worktrees": str(self.base / "worktrees"),
                "base_branch": "dev-environment",
            },
        }), encoding="utf-8")

        self.argv_file = self.base / "argv.txt"
        self.input_file = self.base / "input.txt"
        os.environ.update(STUB_ARGV=str(self.argv_file), STUB_INPUT=str(self.input_file))
        os.environ.pop("STUB_TOUCH", None)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.old_path}"

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        for key in ("STUB_ARGV", "STUB_INPUT", "STUB_TOUCH"):
            os.environ.pop(key, None)
        self.temp.cleanup()

    # ─── Hilfen ─────────────────────────────────────────────────────
    def _git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.repo), *arguments],
                              check=True, capture_output=True, text=True)

    def _stub(self, name, body):
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _ticket(self, state="freigegeben", config=None, ticket_id="20260823-120000-aaaabbbb"):
        (self.inbox / f"{ticket_id}.md").write_text(
            "# Fehler im Heute-Tab\n\nDer Button reagiert nicht.\n", encoding="utf-8")
        (self.inbox / f"{ticket_id}.json").write_text(json.dumps({
            "schema": 1, "id": ticket_id, "title": "Fehler im Heute-Tab",
            "state": state, "created_at": "2026-08-23T12:00:00+02:00",
            "created_by": "user-1",
            "processing": [],
            "config": config if config is not None else {
                "endpoint": "openrouter", "tool": "claude",
                "model": "claude-opus-5", "mode": "read",
            },
        }), encoding="utf-8")
        return ticket_id

    def _sidecar(self, ticket_id):
        return json.loads((self.inbox / f"{ticket_id}.json").read_text(encoding="utf-8"))

    # ─── Auswahl ────────────────────────────────────────────────────
    def test_nothing_happens_without_configuration(self):
        self.config_path.write_text(json.dumps({"schema": 1}), encoding="utf-8")
        self._ticket()
        self.assertIsNone(ticket_runner.run_ticket_once(self.config_path))

    def test_only_released_tickets_are_picked_up(self):
        for state in ("neu", "in_arbeit", "pruefung", "fehlgeschlagen", "erledigt"):
            with self.subTest(state=state):
                ticket_id = self._ticket(state=state)
                self.assertIsNone(ticket_runner.run_ticket_once(self.config_path))
                self.assertEqual(self._sidecar(ticket_id)["state"], state)

    def test_a_ticket_without_model_fails_instead_of_running(self):
        ticket_id = self._ticket(config={"endpoint": "x", "tool": "claude", "mode": "read"})
        self.assertEqual(ticket_runner.run_ticket_once(self.config_path), ticket_id)
        item = self._sidecar(ticket_id)
        self.assertEqual(item["state"], "fehlgeschlagen")
        self.assertIn("Modellwahl", item["last_error"])

    def test_an_unknown_tool_is_refused(self):
        ticket_id = self._ticket(config={"endpoint": "x", "tool": "rm-rf",
                                         "model": "m", "mode": "read"})
        ticket_runner.run_ticket_once(self.config_path)
        self.assertEqual(self._sidecar(ticket_id)["state"], "fehlgeschlagen")

    # ─── Erfolgreicher Lauf ─────────────────────────────────────────
    def test_successful_run_moves_the_ticket_to_review(self):
        self._stub("claude", CLAUDE_STUB)
        ticket_id = self._ticket()
        markdown = self.inbox / f"{ticket_id}.md"
        before = markdown.read_text(encoding="utf-8")

        self.assertEqual(ticket_runner.run_ticket_once(self.config_path), ticket_id)

        item = self._sidecar(ticket_id)
        self.assertEqual(item["state"], "pruefung")
        # Regel 2: die Ticket-Markdown wird nie geschrieben.
        self.assertEqual(markdown.read_text(encoding="utf-8"), before)

        entry = item["processing"][-1]
        self.assertEqual(entry["tool"], "claude")
        self.assertEqual(entry["model"], "claude-opus-5")
        self.assertEqual(entry["branch"], f"ticket/{ticket_id}")
        self.assertEqual(entry["semantic_hash"], ticket_runner.semantic_hash(before))
        self.assertFalse(entry["changed"])

        report = self.inbox / f"{ticket_id}.bericht.md"
        self.assertIn("app/ geprueft", report.read_text(encoding="utf-8"))

    def test_the_agent_works_in_an_isolated_worktree(self):
        self._stub("claude", CLAUDE_STUB)
        ticket_id = self._ticket()
        ticket_runner.run_ticket_once(self.config_path)

        worktree = self.base / "worktrees" / ticket_id
        self.assertTrue((worktree / "app.py").is_file())
        # Das Hauptarbeitsverzeichnis bleibt sauber (E-20).
        status = self._git("status", "--porcelain")
        self.assertEqual(status.stdout.strip(), "")

    def test_changes_are_committed_on_the_ticket_branch(self):
        ticket_id = self._ticket(config={"endpoint": "x", "tool": "claude",
                                         "model": "claude-opus-5", "mode": "write"})
        os.environ["STUB_TOUCH"] = "neu.txt"
        self._stub("claude", CLAUDE_STUB)
        ticket_runner.run_ticket_once(self.config_path)

        entry = self._sidecar(ticket_id)["processing"][-1]
        self.assertTrue(entry["changed"])
        self.assertTrue(entry["commit"])
        listing = subprocess.run(
            ["git", "-C", str(self.repo), "show", "--name-only", "--format=", entry["commit"]],
            check=True, capture_output=True, text=True)
        self.assertIn("neu.txt", listing.stdout)

    def test_prompt_carries_ticket_and_names_the_tool_limits(self):
        self._stub("claude", CLAUDE_STUB)
        self._ticket()
        ticket_runner.run_ticket_once(self.config_path)
        prompt = self.input_file.read_text(encoding="utf-8")
        self.assertIn("Der Button reagiert nicht.", prompt)
        self.assertIn("nur analysieren", prompt)
        # Claude Code liest CLAUDE.md im Arbeitsverzeichnis selbst. Ein zweites
        # Mal im Prompt laege derselbe Text in jedem Durchlauf erneut im Kontext.
        self.assertNotIn("Kurz halten.", prompt)
        # Ohne diesen Satz probiert der Agent Bash-Kommandos durch, die die
        # Allowlist ohnehin ablehnt; jede Ablehnung kostet einen vollen Durchlauf.
        self.assertIn("kein Bash", prompt)
        self.assertIn("Read Glob Grep", prompt)

    def _entry_documentation(self):
        landkarte = self.repo / "docs" / "project" / "Feature-Landkarte.md"
        landkarte.parent.mkdir(parents=True, exist_ok=True)
        landkarte.write_text("# Landkarte\n\nSehr langer Volltext.\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "Landkarte")

    def test_entry_documentation_is_named_not_inlined_for_claude(self):
        self._entry_documentation()
        self._stub("claude", CLAUDE_STUB)
        self._ticket()
        ticket_runner.run_ticket_once(self.config_path)
        prompt = self.input_file.read_text(encoding="utf-8")
        self.assertIn("docs/project/Feature-Landkarte.md", prompt)
        self.assertNotIn("Sehr langer Volltext.", prompt)

    def test_tools_without_own_context_still_get_the_documentation_inline(self):
        self._entry_documentation()
        self._stub("pi", PI_STUB)
        self._ticket(config={"endpoint": "host-pi", "tool": "pi",
                             "model": "anthropic/claude", "mode": "read"})
        ticket_runner.run_ticket_once(self.config_path)
        prompt = self.input_file.read_text(encoding="utf-8")
        self.assertIn("Kurz halten.", prompt)
        self.assertIn("Sehr langer Volltext.", prompt)

    def test_attachments_are_named_with_their_absolute_path(self):
        self._stub("claude", CLAUDE_STUB)
        ticket_id = "20260823-120000-aaaabbbb"
        self._ticket()
        (self.inbox / ticket_id).mkdir()
        (self.inbox / ticket_id / "01-image.png").write_bytes(b"png")
        sidecar = self.inbox / f"{ticket_id}.json"
        item = json.loads(sidecar.read_text(encoding="utf-8"))
        item["attachments"] = ["01-image.png"]
        sidecar.write_text(json.dumps(item), encoding="utf-8")
        ticket_runner.run_ticket_once(self.config_path)
        prompt = self.input_file.read_text(encoding="utf-8")
        # Der Eingangsordner ist nicht versioniert und fehlt im Worktree. Ohne
        # den absoluten Pfad sucht der Agent den Anhang und verbrennt Durchlaeufe.
        self.assertIn(str(self.inbox / ticket_id / "01-image.png"), prompt)

    def test_read_mode_passes_a_tool_allowlist_without_write_tools(self):
        self._stub("claude", CLAUDE_STUB)
        self._ticket()
        ticket_runner.run_ticket_once(self.config_path)
        argv = self.argv_file.read_text(encoding="utf-8")
        self.assertIn("--allowedTools Read Glob Grep", argv)
        self.assertIn("--permission-mode plan", argv)
        self.assertNotIn("Write", argv.split("--allowedTools ")[1])

    # ─── Fehlschlag ─────────────────────────────────────────────────
    def test_failed_run_is_marked_and_not_retried(self):
        self._stub("claude", FAILING_STUB)
        ticket_id = self._ticket()
        ticket_runner.run_ticket_once(self.config_path)

        item = self._sidecar(ticket_id)
        self.assertEqual(item["state"], "fehlgeschlagen")
        self.assertIn("model unreachable", item["last_error"])
        # E-26: kein automatischer zweiter Versuch.
        self.assertIsNone(ticket_runner.run_ticket_once(self.config_path))
        self.assertEqual(self._sidecar(ticket_id)["state"], "fehlgeschlagen")

    # ─── Diff und Review-Entscheidung ───────────────────────────────
    def _completed_run(self):
        """Einen erfolgreichen Schreiblauf herstellen, der auf Pruefung wartet."""
        ticket_id = self._ticket(config={"endpoint": "x", "tool": "claude",
                                         "model": "claude-opus-5", "mode": "write"})
        os.environ["STUB_TOUCH"] = "neu.txt"
        self._stub("claude", CLAUDE_STUB)
        ticket_runner.run_ticket_once(self.config_path)
        os.environ.pop("STUB_TOUCH", None)
        return ticket_id

    def test_diff_file_is_written_for_review(self):
        ticket_id = self._completed_run()
        diff = (self.inbox / f"{ticket_id}.diff").read_text(encoding="utf-8")
        self.assertIn("neu.txt", diff)
        self.assertIn("+geaendert", diff)
        self.assertEqual(self._sidecar(ticket_id)["processing"][-1]["files"], ["neu.txt"])

    def test_a_run_without_changes_writes_no_diff(self):
        self._stub("claude", CLAUDE_STUB)
        ticket_id = self._ticket()
        ticket_runner.run_ticket_once(self.config_path)
        self.assertFalse((self.inbox / f"{ticket_id}.diff").exists())
        self.assertEqual(self._sidecar(ticket_id)["processing"][-1]["files"], [])

    def test_merge_brings_the_change_into_the_base_branch(self):
        ticket_id = self._completed_run()
        item = self._sidecar(ticket_id)
        item["requested_action"] = "merge"
        (self.inbox / f"{ticket_id}.json").write_text(json.dumps(item), encoding="utf-8")

        self.assertEqual(ticket_runner.run_ticket_once(self.config_path), ticket_id)

        self.assertTrue((self.repo / "neu.txt").is_file())
        item = self._sidecar(ticket_id)
        self.assertEqual(item["state"], "erledigt")
        self.assertNotIn("requested_action", item)
        # Worktree und Branch sind aufgeraeumt (E-23).
        self.assertFalse((self.base / "worktrees" / ticket_id).exists())
        branches = subprocess.run(["git", "-C", str(self.repo), "branch", "--list",
                                   f"ticket/{ticket_id}"], capture_output=True, text=True)
        self.assertEqual(branches.stdout.strip(), "")

    def test_discard_removes_the_branch_without_touching_the_base(self):
        ticket_id = self._completed_run()
        item = self._sidecar(ticket_id)
        item["requested_action"] = "discard"
        (self.inbox / f"{ticket_id}.json").write_text(json.dumps(item), encoding="utf-8")

        ticket_runner.run_ticket_once(self.config_path)

        self.assertFalse((self.repo / "neu.txt").exists())
        self.assertEqual(self._sidecar(ticket_id)["state"], "verworfen")

    def test_a_decision_is_handled_before_a_waiting_ticket(self):
        decided = self._completed_run()
        item = self._sidecar(decided)
        item["requested_action"] = "discard"
        (self.inbox / f"{decided}.json").write_text(json.dumps(item), encoding="utf-8")
        waiting = self._ticket(ticket_id="20260823-140000-eeeeffff")

        self.assertEqual(ticket_runner.run_ticket_once(self.config_path), decided)
        self.assertEqual(self._sidecar(waiting)["state"], "freigegeben")

    def test_a_failing_merge_returns_the_ticket_to_review(self):
        ticket_id = self._completed_run()
        # Ein Konflikt im Basisbranch laesst den Merge scheitern.
        (self.repo / "neu.txt").write_text("konflikt\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "konkurrierende Aenderung")

        item = self._sidecar(ticket_id)
        item["requested_action"] = "merge"
        (self.inbox / f"{ticket_id}.json").write_text(json.dumps(item), encoding="utf-8")
        ticket_runner.run_ticket_once(self.config_path)

        item = self._sidecar(ticket_id)
        self.assertEqual(item["state"], "pruefung")
        self.assertIn("Merge", item["last_error"])
        self.assertNotIn("requested_action", item)

    # ─── Rückfragen ─────────────────────────────────────────────────
    def _run_once(self):
        return ticket_runner.run_ticket_once(self.config_path, data_root=self.data_root)

    def _sessions(self):
        directory = self.data_root / "user-1" / "tickets"
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(directory.glob("*.json"))]

    def test_a_question_file_stops_the_run_and_opens_a_session(self):
        self._stub("claude", ASKING_STUB)
        ticket_id = self._ticket()
        self.assertEqual(self._run_once(), ticket_id)

        self.assertEqual(self._sidecar(ticket_id)["state"], "rueckfrage")
        sessions = self._sessions()
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session["status"], "offen")
        self.assertEqual(session["ticket_id"], ticket_id)
        self.assertEqual(session["runner_session"], "sess-42")

        kinds = [q["type"] for q in session["questions"]]
        # Eine Auswahl ohne Optionen ist keine Auswahl.
        self.assertEqual(kinds, ["boolean", "choice", "text"])
        self.assertEqual(session["questions"][1]["options"], ["A", "B"])

    def test_the_question_file_is_removed_so_it_cannot_ask_twice(self):
        self._stub("claude", ASKING_STUB)
        ticket_id = self._ticket()
        self._run_once()
        worktree = self.base / "worktrees" / ticket_id
        self.assertFalse((worktree / ticket_runner.QUESTION_FILE).exists())

    def test_an_unanswered_question_does_not_restart_the_ticket(self):
        self._stub("claude", ASKING_STUB)
        ticket_id = self._ticket()
        self._run_once()
        # Solange niemand antwortet, bleibt das Ticket liegen (unbegrenztes Warten).
        self.assertIsNone(self._run_once())
        self.assertEqual(self._sidecar(ticket_id)["state"], "rueckfrage")

    def test_answering_resumes_the_session_with_the_answers(self):
        self._stub("claude", ASKING_STUB)
        ticket_id = self._ticket()
        self._run_once()

        directory = self.data_root / "user-1" / "tickets"
        path = next(directory.glob("*.json"))
        session = json.loads(path.read_text(encoding="utf-8"))
        session["questions"][0]["answer"] = "ja"
        session["status"] = "beantwortet"
        path.write_text(json.dumps(session), encoding="utf-8")

        self._stub("claude", CLAUDE_STUB)
        self.assertEqual(self._run_once(), ticket_id)

        argv = self.argv_file.read_text(encoding="utf-8")
        self.assertIn("--resume sess-42", argv)
        prompt = self.input_file.read_text(encoding="utf-8")
        self.assertIn("Gilt das auch fuer Mobile?", prompt)
        self.assertIn("ja", prompt)
        self.assertEqual(self._sidecar(ticket_id)["state"], "pruefung")

        # Die Session ist verbraucht und löst keinen weiteren Lauf aus.
        self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["consumed_at"])
        self.assertIsNone(self._run_once())

    def test_the_prompt_explains_how_to_ask(self):
        self._stub("claude", CLAUDE_STUB)
        self._ticket()
        self._run_once()
        prompt = self.input_file.read_text(encoding="utf-8")
        self.assertIn(ticket_runner.QUESTION_FILE, prompt)
        self.assertIn("Rate nicht", prompt)

    def test_only_one_ticket_runs_per_cycle(self):
        self._stub("claude", CLAUDE_STUB)
        first = self._ticket(ticket_id="20260823-120000-aaaabbbb")
        second = self._ticket(ticket_id="20260823-130000-ccccdddd")
        self.assertEqual(ticket_runner.run_ticket_once(self.config_path), first)
        self.assertEqual(self._sidecar(second)["state"], "freigegeben")


if __name__ == "__main__":
    unittest.main()
