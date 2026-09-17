"""Brain composition facade: shared state, blueprint and stable imports.

AI maintenance contract: feature logic belongs in brain_documents.py,
brain_index.py, brain_content.py or brain_routes.py. Import `brain`, not those
implementation modules, when callers need test-replaceable runtime paths.

Brain View: local Markdown search, timeline, tasks, and metadata.

The source Markdown files remain authoritative.  This module keeps a rebuildable
file-per-document index beside each user's data and a separate metadata file for
manual Brain annotations.
"""

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import unicodedata
import fcntl
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

from flask import Blueprint, jsonify, request
import family as family_module
import historical_tagging
import tagging as tagging_module
import ai_sessions as ai_sessions_module
import write_sessions as write_sessions_module
from scheduling import path_lock, read_text_file, update_text_file, write_text_file


logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
FAMILY_DIR = DATA_DIR / "family"
INDEX_DIR_NAME = "brain_index"
METADATA_FILE_NAME = "brain_metadata.json"
INDEX_INTERVAL_SECONDS = max(300, int(os.environ.get("BRAIN_INDEX_INTERVAL_SECONDS", "3600")))

brain_bp = Blueprint("brain", __name__, url_prefix="/api/brain")

_INDEXED_TECHNICAL_NAMES = {
    "readme.md",
    "changelog.md",
    "license.md",
    "copying.md",
    "contributing.md",
    "code_of_conduct.md",
    "security.md",
    "index.md",
}
_TECHNICAL_DIRS = {"indexes", "brain_index", "__pycache__"}
_TASK_RE = re.compile(r"^(?P<indent>\s*)(?P<bullet>[-*+])\s+\[(?P<state>[ xX])\]\s*(?P<text>.*)$")
_TASK_STATE_RE = re.compile(r"^(?P<prefix>\s*[-*+]\s+)\[[ xX]\]", re.MULTILINE)
_FAMILY_TASK_ID_RE = re.compile(
    r"^id:\s*(?P<id>[^|]+?)\s*\|\s*title:\s*[^|]*?\s*\|\s*"
    r"user:\s*[^|]*?\s*\|\s*target-date:\s*[^|]*?(?:\s*\||$)"
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_PATH_RE = re.compile(
    r"^(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"Journal_(?P=year)-(?P=month)-(?P=day)\.md$"
)
_JOURNAL_DATE_RE = re.compile(r"Journal_(\d{4}-\d{2}-\d{2})\.md$")
_TIME_RE = re.compile(r"(?:Time:\s*|~~|^\s*[-*]\s*)(\d{2}:\d{2}:\d{2})", re.IGNORECASE | re.MULTILINE)
_QUICK_DATE_TIME_RE = re.compile(
    r"^\s*[-*]\s*(\d{2})\.(\d{2})\.\s+(\d{2}:\d{2}:\d{2})",
    re.MULTILINE,
)
_QUICK_END_TIME_RE = re.compile(r"~~(?:(\d{2})\.(\d{2})\.\s+)?(\d{2}:\d{2}:\d{2})~~")
_DATETIME_RE = re.compile(
    r"Datum\s*&\s*Uhrzeit:\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})",
    re.IGNORECASE,
)
_BRAIN_TAG_RE = re.compile(r"Brainablage:\s*Tags:\s*(.*?)(?:\*\*Datum|\n|$)", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"(?<![\w#])#([\w-]+)", re.UNICODE)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_index_queue = queue.Queue()
_queued_rebuilds = set()
_running_rebuilds = set()
_dirty_rebuilds = set()
_queued_rebuilds_lock = threading.Lock()
_index_worker = None
_index_worker_lock_file = None
_index_snapshot_lock = threading.Lock()
_index_snapshots = {}




def _install_domain_module(module):
    """Expose stable brain.py call paths while keeping domain state isolated."""
    for name in module.__all__:
        target = getattr(module, name)
        if isinstance(target, type):
            globals()[name] = target
            continue

        def forward(*args, _name=name, _module=module, **kwargs):
            _module._sync_from_facade()
            return getattr(_module, _name)(*args, **kwargs)

        forward.__name__ = name
        forward.__doc__ = getattr(target, "__doc__", None)
        globals()[name] = forward


# Load order is part of the architecture: document parsing -> index/visibility
# -> editor/content services -> HTTP routes. Later domains consume the stable
# wrappers exported by earlier ones, never private implementation modules.
import brain_documents as _brain_documents
_install_domain_module(_brain_documents)

import brain_index as _brain_index
_install_domain_module(_brain_index)

import brain_tag_filters as _brain_tag_filters
_install_domain_module(_brain_tag_filters)

import brain_search as _brain_search
_install_domain_module(_brain_search)

import brain_content as _brain_content
_install_domain_module(_brain_content)

import brain_blocks as _brain_blocks
_install_domain_module(_brain_blocks)

import brain_bulk_replace as _brain_bulk_replace
_install_domain_module(_brain_bulk_replace)

import brain_agent_sessions as _brain_agent_sessions
_install_domain_module(_brain_agent_sessions)

import brain_diagnostics as _brain_diagnostics
_install_domain_module(_brain_diagnostics)

import brain_routes as _brain_routes
for _export_name in _brain_routes.__all__:
    globals()[_export_name] = getattr(_brain_routes, _export_name)
