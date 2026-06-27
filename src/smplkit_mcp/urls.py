"""Public-URL constraint (ADR-057 §2.5).

smplkit calls the target URL from the cloud, so it must be reachable from the
public internet. A ``localhost`` or private-IP target can never fire. We catch
that locally and return the actionable fork (deploy, or tunnel) rather than
letting the run fail silently at fire time.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

PUBLIC_URL_GUIDANCE = (
    "smplkit runs your jobs from the cloud, so the target URL must be reachable "
    "from the public internet — a localhost or private-network address can't be "
    "called. Either point the job at your deployed/public URL, or expose your "
    "local server with a tunnel (e.g. `cloudflared tunnel --url "
    "http://localhost:PORT` or `ngrok http PORT`) and use the public URL it "
    "prints. Then set a secret auth header on the job (e.g. `Authorization`) so "
    "only smplkit can call the tunnelled endpoint."
)

_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal")


class NonPublicTargetError(ValueError):
    """Raised when a job target URL is not reachable from the public internet."""

    def __init__(self, message: str = PUBLIC_URL_GUIDANCE) -> None:
        super().__init__(message)


def is_public_target(url: str) -> bool:
    """Return ``True`` when *url* is a public ``http(s)`` URL smplkit can reach."""
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES or host.endswith(_LOCAL_SUFFIXES):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A regular hostname that isn't an obvious local name — assume public.
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
    )


def require_public_target(url: str) -> None:
    """Raise :class:`NonPublicTargetError` if *url* is not publicly reachable."""
    if not is_public_target(url):
        raise NonPublicTargetError()
