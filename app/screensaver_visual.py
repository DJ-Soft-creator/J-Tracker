"""Klangbild des Bildschirmschoners: erlaubte Werte fuer ``settings.visual``.

Gezeichnet wird im Browser (``screensaver-visual.js``); hier steht nur, was
gespeichert werden darf. Der Abschnitt gehoert zu ``user["screensaver"]`` und
reist damit in Presets mit. Aus per Voreinstellung; die Form schaltet ein
Kuerzel im laufenden Schoner reihum.

``reaction``: wie hart die Musik durchschlaegt - ``free`` gar nicht
(eigenes, schwankendes Tempo), ``soft`` wabert nur mit,
``beat`` stoesst bei jedem Schlag, ``wild`` dazu mit Farbspruengen.
"""

DEFAULT_VISUAL = {
    "enabled": False, "form": "lava", "blend": "glow", "strength": 70, "quality": "balanced", "reaction": "beat",
}
FORMS = ("lava", "blobs", "ink", "kaleido", "mandala", "tunnel", "fractal", "plasma")
BLENDS = ("glow", "tint", "bold")
QUALITIES = ("saver", "balanced", "full")
REACTIONS = ("free", "soft", "beat", "wild")
STRENGTH_MIN = 10
STRENGTH_MAX = 100
# Bildquelle „Keine Bilder“: nur das Klangbild auf Schwarz. Gilt nur allein,
# neben echten Quellen faellt sie weg (screensaver.validate_settings).
NO_IMAGES = "none"


def validate(value):
    """Unbekanntes faellt auf die Voreinstellung, die Staerke wird begrenzt."""
    wanted = value if isinstance(value, dict) else {}
    visual = dict(DEFAULT_VISUAL)
    if isinstance(wanted.get("enabled"), bool):
        visual["enabled"] = wanted["enabled"]
    for key, choices in (("form", FORMS), ("blend", BLENDS), ("quality", QUALITIES), ("reaction", REACTIONS)):
        if wanted.get(key) in choices:
            visual[key] = wanted[key]
    strength = wanted.get("strength")
    if isinstance(strength, (int, float)) and not isinstance(strength, bool):
        visual["strength"] = max(STRENGTH_MIN, min(STRENGTH_MAX, int(strength)))
    return visual


def no_images(sources):
    return list(sources or []) == [NO_IMAGES]


__all__ = ["DEFAULT_VISUAL", "FORMS", "BLENDS", "QUALITIES", "REACTIONS", "NO_IMAGES", "validate", "no_images"]
