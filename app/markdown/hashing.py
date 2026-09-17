"""Deterministic file and semantic hashes for the staged Document Store.

``file_hash`` is intentionally a SHA-256 over the untouched bytes.  The semantic
variant is also deliberately conservative: it normalizes only CRLF/CR line endings
to LF.  It never trims whitespace, changes Unicode, or removes a final newline,
because those changes can be visible Markdown content.
"""

import codecs
import hashlib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CHUNK_SIZE = 1024 * 1024


class FileValidationError(ValueError):
    """Raised before a file is eligible for parsing or migration."""


@dataclass(frozen=True)
class FileHashes:
    size: int
    file_hash: str
    semantic_hash: str


def _validate_limit(max_bytes):
    if max_bytes is not None and (not isinstance(max_bytes, int) or max_bytes < 0):
        raise ValueError("max_bytes muss eine nicht-negative ganze Zahl sein")


def file_hash(content):
    """Return SHA-256 over exact bytes, rejecting implicit text encoding."""
    if not isinstance(content, bytes):
        raise TypeError("file_hash erwartet Bytes")
    return hashlib.sha256(content).hexdigest()


def semantic_hash(content):
    """Return the documented semantic hash for valid UTF-8 text."""
    if not isinstance(content, str):
        raise TypeError("semantic_hash erwartet Text")
    return hashlib.sha256(_normalize_newlines(content).encode("utf-8")).hexdigest()


def _normalize_newlines(content, pending_carriage_return=False):
    """Normalize a text fragment and retain a trailing CR for the next fragment."""
    if pending_carriage_return:
        content = "\r" + content
    return content.replace("\r\n", "\n").replace("\r", "\n")


def hash_file(path, *, max_bytes, chunk_size=DEFAULT_CHUNK_SIZE):
    """Stream a UTF-8 Markdown file once and return exact and semantic SHA-256 hashes.

    ``max_bytes`` is explicit so callers must choose the product limit at their
    gate.  The file is never decoded with replacement: malformed UTF-8 is blocked
    before a caller can mistake it for a safely importable document.
    """
    _validate_limit(max_bytes)
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size muss positiv sein")
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
    except OSError as error:
        raise FileValidationError("Datei kann nicht gelesen werden") from error
    if size > max_bytes:
        raise FileValidationError("Datei ueberschreitet die erlaubte Groesse")

    exact = hashlib.sha256()
    semantic = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    pending_carriage_return = False
    bytes_read = 0
    try:
        with file_path.open("rb") as source:
            while chunk := source.read(chunk_size):
                bytes_read += len(chunk)
                if bytes_read > max_bytes:
                    raise FileValidationError("Datei ueberschreitet die erlaubte Groesse")
                exact.update(chunk)
                text = decoder.decode(chunk, final=False)
                if pending_carriage_return:
                    text = "\r" + text
                pending_carriage_return = text.endswith("\r")
                if pending_carriage_return:
                    text = text[:-1]
                semantic.update(_normalize_newlines(text).encode("utf-8"))
            tail = decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise FileValidationError("Datei ist nicht gueltig UTF-8") from error
    except OSError as error:
        raise FileValidationError("Datei kann nicht gelesen werden") from error
    if pending_carriage_return:
        tail = "\r" + tail
    semantic.update(_normalize_newlines(tail).encode("utf-8"))
    return FileHashes(size=bytes_read, file_hash=exact.hexdigest(), semantic_hash=semantic.hexdigest())
