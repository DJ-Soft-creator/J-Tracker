"""Repository structure guardrails for maintainable AI and human navigation."""

import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("app", "scripts", "whisper_service", "tests")
SOURCE_SUFFIXES = {".py", ".js", ".html", ".css", ".sh"}
MAX_SOURCE_LINES = 1000


class CodeStructureTests(unittest.TestCase):
    def test_source_files_do_not_exceed_one_thousand_lines(self):
        oversized = []
        for root_name in SOURCE_ROOTS:
            for path in (ROOT / root_name).rglob("*"):
                if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                line_count = len(path.read_text(encoding="utf-8").splitlines())
                if line_count > MAX_SOURCE_LINES:
                    oversized.append(f"{path.relative_to(ROOT)}: {line_count}")
        self.assertEqual(oversized, [], "Split oversized source files:\n" + "\n".join(oversized))

    def test_classic_frontend_assets_keep_their_contractual_order(self):
        expected = {
            "index.html": (
                "action-menu.js", "write-submit-state.js","mobile-app-core.js", "mobile-session.js", "dashboard-days.js", "mobile-pwa-gestures.js",
                "mobile-app-features.js", "settings-menu.js", "template-manager.js", "write-targets.js", "family-board.js", "brain.js", "brain-cards.js", "brain-tag-filters.js", "brain-select.js", "brain-content.js", "brain-replace.js",
                "write-history.js", "write-hashtags.js", "write-template-hashtags.js",
                "write-ai.js", "whisper-model.js", "journal-media.js", "write-media-strip.js", "write-media-menu.js", "write-session.js", "transcription-schedule.js", "write-deep-mode.js", "write-read-mode.js", "screenshot-inbox.js", "share-api.js",
                "write-source-edit.js", "write-agent.js", "agent-project-pickers.js", "agent-projects.js",
            ),
            "desktop.html": (
                "action-menu.js", "write-submit-state.js","desktop-app-core.js", "dashboard-days.js", "desktop-app-features.js",
                "settings-menu.js", "template-manager.js", "desktop-app-resize.js", "write-targets.js", "family-board.js", "brain.js", "brain-cards.js", "brain-tag-filters.js", "brain-select.js", "brain-content.js", "brain-replace.js",
                "write-history.js", "write-hashtags.js", "write-template-hashtags.js",
                "write-ai.js", "whisper-model.js", "journal-media.js", "write-media-strip.js", "write-media-menu.js", "write-session.js", "transcription-schedule.js", "write-deep-mode.js", "write-read-mode.js", "screenshot-inbox.js", "share-api.js",
                "write-source-edit.js", "write-agent.js", "agent-project-pickers.js", "agent-projects.js",
            ),
        }
        for template_name, assets in expected.items():
            template = (ROOT / "app/templates" / template_name).read_text(encoding="utf-8")
            positions = [template.index(f"filename='{asset}'") for asset in assets]
            self.assertEqual(positions, sorted(positions), f"Invalid asset order in {template_name}")

    def test_every_template_script_asset_is_documented(self):
        doc = (ROOT / "docs/project/Code-Struktur.md").read_text(encoding="utf-8")
        for template_name in ("index.html", "desktop.html"):
            template = (ROOT / "app/templates" / template_name).read_text(encoding="utf-8")
            assets = re.findall(r"filename='([a-z0-9-]+\.js)'", template)
            undocumented = sorted({a for a in assets if a not in doc})
            self.assertEqual(
                undocumented, [],
                f"{template_name} loads scripts missing from docs/project/Code-Struktur.md: {undocumented}",
            )

    def test_every_backend_module_is_documented(self):
        doc = (ROOT / "docs/project/Code-Struktur.md").read_text(encoding="utf-8")
        skip = {"__init__.py"}
        modules = sorted(
            p.name for p in (ROOT / "app").glob("*.py") if p.name not in skip
        )
        undocumented = [m for m in modules if f"`{m}`" not in doc]
        self.assertEqual(
            undocumented, [],
            "app/*.py modules missing from docs/project/Code-Struktur.md module table:\n"
            + "\n".join(undocumented),
        )


class DocumentationLinkTests(unittest.TestCase):
    """Guard the documentation contract: no dead local links, no stale archive paths."""

    @staticmethod
    def _markdown_files():
        for path in ROOT.rglob("*.md"):
            parts = path.parts
            if ".git" in parts or "node_modules" in parts or "fixtures" in parts:
                continue
            # Eingereichte Tickets sind Nutzerinhalt, keine Projektdokumentation:
            # sie sind gitignored, werden von der App geschrieben (Modus 0o660,
            # fremder Eigentuemer) und duerfen die Doku-Pruefungen weder
            # inhaltlich noch mit einem PermissionError beeinflussen.
            if "tickets" in parts and "inbox" in parts:
                continue
            yield path

    @staticmethod
    def _links_outside_code_fences(text):
        """Yield (line_no, target) for Markdown links, ignoring fenced examples."""
        in_fence = False
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", line):
                yield number, match.group(1).split("#")[0].strip().strip("<>")

    def test_local_markdown_links_resolve(self):
        broken = []
        for path in self._markdown_files():
            for number, target in self._links_outside_code_fences(
                path.read_text(encoding="utf-8", errors="replace")
            ):
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                base = ROOT if target.startswith("/") else path.parent
                if not (base / target.lstrip("/")).exists():
                    broken.append(f"{path.relative_to(ROOT)}:{number} -> {target}")
        self.assertEqual(broken, [], "Broken local documentation links:\n" + "\n".join(broken))

    def test_retired_paths_are_only_mentioned_inside_the_archive(self):
        """Renamed or removed paths must not survive in active docs or code.

        archive/ intentionally keeps the old names, because it records the state at
        its time. Everything else has to follow a rename.
        """
        retired = ("db_spike", "db-spike", "Screenshot_Version 3-2-1.png")
        searched = list(self._markdown_files())
        for root_name in SOURCE_ROOTS:
            for path in (ROOT / root_name).rglob("*"):
                if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES:
                    searched.append(path)

        offenders = []
        for path in searched:
            relative = path.relative_to(ROOT)
            if relative.parts[0] == "archive" or "__pycache__" in relative.parts:
                continue
            if path == Path(__file__).resolve():  # this file names them on purpose
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                for name in retired:
                    # db_spike_probe is an internal throwaway table, not a path.
                    if name in line and "db_spike_probe" not in line:
                        offenders.append(f"{relative}:{number} -> {name}")
        self.assertEqual(
            offenders, [],
            "Retired paths must not be referenced outside archive/:\n" + "\n".join(offenders),
        )

    def test_no_bytecode_or_cache_is_tracked(self):
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
        polluted = [f for f in tracked if "__pycache__" in f or f.endswith(".pyc")]
        self.assertEqual(polluted, [], "Bytecode/cache must not be tracked:\n" + "\n".join(polluted))


class PublicManifestTests(unittest.TestCase):
    """Guard the publication contract.

    `scripts/public-manifest.txt` is a whitelist: `deploy.sh public-update`
    publishes only what it lists and what git tracks. Two failure modes are
    invisible until someone looks at the public repository, so they are checked
    here instead: a rule that no longer matches anything (a file was renamed and
    silently stopped being published), and a published document that links to a
    document which stays private (a dead link for every outside reader).
    """

    @staticmethod
    def _manifest_rules():
        text = (ROOT / "scripts/public-manifest.txt").read_text(encoding="utf-8")
        allow, deny = [], []
        for line in text.splitlines():
            line = re.sub(r"\s*#.*$", "", line).strip()
            if not line:
                continue
            (deny if line.startswith("!") else allow).append(line.lstrip("!"))
        return allow, deny

    @classmethod
    def _published_files(cls):
        allow, deny = cls._manifest_rules()

        def matched_by(rules, relative):
            return any(
                relative.startswith(rule) if rule.endswith("/") else relative == rule
                for rule in rules
            )

        published = set()
        for rule in allow:
            target = ROOT / rule
            candidates = (
                (path for path in target.rglob("*") if path.is_file())
                if rule.endswith("/")
                else ([target] if target.is_file() else [])
            )
            for path in candidates:
                relative = path.relative_to(ROOT).as_posix()
                if "__pycache__" in relative or relative.endswith(".pyc"):
                    continue
                if matched_by(deny, relative):
                    continue
                published.add(relative)
        return published

    def test_every_manifest_rule_matches_something(self):
        allow, deny = self._manifest_rules()
        missing = [rule for rule in allow + deny if not (ROOT / rule).exists()]
        self.assertEqual(
            missing, [],
            "Manifest rules point at paths that no longer exist:\n" + "\n".join(missing),
        )

    def test_published_markdown_never_links_into_private_files(self):
        published = self._published_files()
        broken = []
        for relative in sorted(name for name in published if name.endswith(".md")):
            # Fixtures carry deliberately fake media references, not documentation.
            if "fixtures" in Path(relative).parts:
                continue
            path = ROOT / relative
            for number, target in DocumentationLinkTests._links_outside_code_fences(
                path.read_text(encoding="utf-8", errors="replace")
            ):
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                if target.startswith("/"):
                    resolved = target.lstrip("/")
                else:
                    resolved = os.path.normpath(
                        os.path.join(os.path.dirname(relative), target)
                    ).replace(os.sep, "/")
                if resolved in published:
                    continue
                if any(name.startswith(resolved.rstrip("/") + "/") for name in published):
                    continue
                broken.append(f"{relative}:{number} -> {target}")
        self.assertEqual(
            broken, [],
            "Published documents must not link to files that stay private:\n"
            + "\n".join(broken),
        )


if __name__ == "__main__":
    unittest.main()
