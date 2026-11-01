"""Lokale Anbieterwahl der Journal-Analyse.

Doppelte Absicherung gegenüber der Verschlagwortung (``historical_tagging``):
Neben der Kennung ``lm_*`` wird zusätzlich geprüft, dass der Endpunkt
tatsächlich im lokalen Netz liegt (Loopback oder privater IP-Bereich, D-015 in
``AI_CONTEXT.md``). Damit kann ein vertippter oder nachträglich geänderter
Anbieter nicht versehentlich Journalinhalte an eine öffentliche Adresse senden.
Die Verschlagwortung erledigt dieselbe Prüfung bereits beim Speichern ihrer
Verbindung über ``agent_projects.validated_base_url``.

Reine Funktionen; Auflösung nur über ``socket.getaddrinfo``.
"""

import ipaddress
import socket
from urllib.parse import urlparse


LOCAL_URL_PLACEHOLDER = "{locale_LLM_IP}"
LOCAL_PROVIDER_PREFIX = "lm_"
CHAT_COMPLETIONS_SUFFIX = "/chat/completions"


class ProviderDiscoveryError(ValueError):
    """Der Endpunkt hat keine nutzbare Modell-Liste gemeldet."""


def provider_base_url(api_url):
    """Aus einer Chat-URL die Basis fuer ``/v1/models`` ableiten.

    ``api_providers`` tragen die volle Adresse (``…/v1/chat/completions``);
    die Modellerkennung erwartet die reine Basis (``http://host:port``).
    """
    text = str(api_url or "").strip()
    if text.endswith(CHAT_COMPLETIONS_SUFFIX):
        text = text[: -len(CHAT_COMPLETIONS_SUFFIX)]
    return text


def discover_models(provider):
    """Live-Modell-Liste eines lokalen Endpunkts — dieselbe wie beim Chat."""
    from agent_projects import AgentProjectError, discover_models as _discover
    try:
        return _discover("openai", provider_base_url(provider.get("api_url")))
    except AgentProjectError as exc:
        raise ProviderDiscoveryError(str(exc)) from exc


def resolve_effective_model(provider, discover=None):
    """Das Modell fuer einen Lauf: konfiguriert oder live ausgelesen.

    Ist im Anbieter kein Modell hinterlegt, wird — wie bei „Chat with your
    Notes“ — die Live-Liste des Endpunkts gelesen und das erste Modell
    genommen. Das Ergebnis wird bewusst **nicht** gespeichert, damit ein
    Wechsel im LM Studio beim naechsten Lauf wirkt.
    """
    model = str(provider.get("model") or "").strip()
    if model:
        return model
    discover = discover or discover_models
    models = discover(provider)
    return models[0] if models else ""


def _addresses(host):
    """IP-Adressen zu einem Host — Literale ohne DNS, sonst per Auflösung."""
    try:
        return {ipaddress.ip_address(str(host).split("%", 1)[0])}
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return set()
    result = set()
    for info in infos:
        try:
            result.add(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except ValueError:
            return set()
    return result


def is_local_host(host):
    """Loopback oder privater Bereich — sonst nichts."""
    if not host:
        return False
    addresses = _addresses(host)
    if not addresses:
        return False
    for address in addresses:
        if address.is_link_local or address.is_multicast:
            return False
        # ``::1`` gilt neben ``loopback``/``private`` zusaetzlich als reserved —
        # ein Reserved-Check zuerst wuerde IPv6-Loopback faelschlich ablehnen.
        if not (address.is_loopback or address.is_private):
            return False
    return True


def is_local_endpoint(url):
    if not isinstance(url, str) or not url.strip():
        return False
    if LOCAL_URL_PLACEHOLDER in url:
        # Unaufgelöster Platzhalter — nicht raten.
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    return is_local_host(parsed.hostname)


def resolve_local_provider(config, provider_id):
    """Der einzige erlaubte Anbieter für eine Analyse: lokal und ``lm_*``.

    Gibt ``None`` zurück, wenn keine der beiden Bedingungen erfüllt ist.
    """
    provider = next(
        (item for item in (config.get("ai_providers") or []) if item.get("id") == provider_id),
        None,
    )
    if not provider or not str(provider.get("id", "")).startswith(LOCAL_PROVIDER_PREFIX):
        return None
    if not is_local_endpoint(provider.get("api_url") or ""):
        return None
    return provider


def is_cloud_provider(provider):
    """Ein ausdruecklich freigegebener Cloud-Anbieter (kein lokaler Endpunkt)."""
    return bool(provider) and not (
        str(provider.get("id", "")).startswith(LOCAL_PROVIDER_PREFIX)
        and is_local_endpoint(provider.get("api_url") or ""))


def resolve_analysis_provider(config, provider_id):
    """Der Anbieter eines Analyse-Laufs: lokal wie bisher - oder ein Cloud-Anbieter,
    den die Anbieterverwaltung ausdruecklich fuer die Journal-Analyse freigegeben
    hat (``journal_analysis: true``) und der ueber HTTPS spricht. Dann gehen die
    Journalinhalte des Jobs an diesen Anbieter; das Job-Formular sagt das deutlich.
    """
    local = resolve_local_provider(config, provider_id)
    if local is not None:
        return local
    provider = next(
        (item for item in (config.get("ai_providers") or []) if item.get("id") == provider_id),
        None,
    )
    if not provider or provider.get("journal_analysis") is not True:
        return None
    if not str(provider.get("api_url") or "").strip().startswith("https://"):
        return None
    return provider


__all__ = ["is_local_host", "is_local_endpoint", "resolve_local_provider",
           "resolve_analysis_provider", "is_cloud_provider",
           "provider_base_url", "discover_models", "resolve_effective_model",
           "ProviderDiscoveryError", "LOCAL_PROVIDER_PREFIX"]