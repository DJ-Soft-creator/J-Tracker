"""Deterministic renderers with an explicit safe-cleaning boundary."""

from .parser import ParseStatus, normalize_newlines


class UnsafeRenderError(ValueError):
    """Raised when a document would need an unapproved metadata-cleanup decision."""


def render_preserving(document):
    """Return the lossless canonical-LF representation for review and roundtrips."""
    return normalize_newlines(document.source)


def render_clean(document):
    """Render only documents that contain no technical J-Tracker metadata.

    Legacy and blocked documents must first be migrated by a later, explicitly
    reviewed operation. This prevents a parser spike from deleting visible content.
    """
    if document.status is not ParseStatus.CLEAN:
        raise UnsafeRenderError("Dokument enthaelt nicht freigegebene technische Metadaten")
    rendered = render_preserving(document)
    if "jt:" in rendered:
        raise UnsafeRenderError("Unklassifizierter technischer Marker")
    return rendered
