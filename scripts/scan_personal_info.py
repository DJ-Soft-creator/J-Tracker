#!/usr/bin/env python3
"""Search code comments and configuration files for selected personal terms.

Code is searched only in comments; configuration files are searched as a whole.
The script prints filenames, line numbers, and short matching snippets.
"""
from __future__ import annotations

import argparse
import io
import re
import tokenize
from pathlib import Path

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php", ".rb", ".swift",
    ".kt", ".kts", ".sql", ".sh", ".bash", ".zsh", ".fish", ".ps1",
}
CODE_FILENAMES = {"Dockerfile", "Makefile"}
CONFIG_EXTENSIONS = {".env", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf"}
CONFIG_FILENAMES = {".env", ".env.example", "docker-compose.yml", "docker-compose.yaml"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def is_config(path: Path) -> bool:
    return path.name in CONFIG_FILENAMES or path.suffix.lower() in CONFIG_EXTENSIONS or path.name.startswith(".env.")


def is_code(path: Path) -> bool:
    return path.name in CODE_FILENAMES or path.suffix.lower() in CODE_EXTENSIONS


def python_comments(text: str):
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                yield tok.start[0], tok.string
    except (IndentationError, SyntaxError, tokenize.TokenError):
        # A partially edited Python file is still scanned with the generic fallback.
        yield from generic_comments(text)


def generic_comments(text: str):
    """Extract common //, #, -- and /* */ comments outside quoted strings."""
    in_block = False
    quote = None
    escaped = False
    for line_no, line in enumerate(text.splitlines(), 1):
        comments = []
        i = 0
        while i < len(line):
            if in_block:
                end = line.find("*/", i)
                if end < 0:
                    comments.append(line[i:])
                    i = len(line)
                    continue
                comments.append(line[i:end])
                i = end + 2
                in_block = False
                continue
            if quote:
                if escaped:
                    escaped = False
                elif line[i] == "\\":
                    escaped = True
                elif line[i] == quote:
                    quote = None
                i += 1
                continue
            if line[i] in "'\"`":
                quote = line[i]
                i += 1
            elif line.startswith("/*", i):
                in_block = True
                i += 2
            elif line.startswith("//", i) or line.startswith("--", i) or line[i] == "#":
                comments.append(line[i:])
                break
            else:
                i += 1
        if comments:
            yield line_no, " ".join(comments)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def report(path: Path, text: str, terms: list[re.Pattern], source: str) -> int:
    hits = 0
    for line_no, line in enumerate(text.splitlines(), 1):
        for pattern in terms:
            if pattern.search(line):
                if source == "comments":
                    # generic_comments/tokenize results are supplied below; this branch is unused
                    pass
                snippet = line.strip().replace("\t", " ")
                print(f"{source}: {path}:{line_no}: {snippet[:240]}")
                hits += 1
                break
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--term", action="append", dest="terms", default=None,
                        help="case-insensitive term (repeatable)")
    args = parser.parse_args()
    root = args.root.resolve()
    patterns = [re.compile(re.escape(term), re.IGNORECASE) for term in args.terms]
    totals = {"comments": 0, "config": 0}

    for path in sorted(p for p in root.rglob("*") if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)):
        source = "config" if is_config(path) else "comments" if is_code(path) else None
        if source is None:
            continue
        text = read_text(path)
        if text is None:
            continue
        if source == "config":
            totals[source] += report(path, text, patterns, source)
        else:
            comments = python_comments(text) if path.suffix.lower() == ".py" else generic_comments(text)
            for line_no, comment in comments:
                if any(pattern.search(comment) for pattern in patterns):
                    print(f"comments: {path}:{line_no}: {comment.strip()[:240]}")
                    totals[source] += 1

    print(f"\nTreffer: {totals['comments']} in Code-Kommentaren, {totals['config']} in Config-Dateien.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
