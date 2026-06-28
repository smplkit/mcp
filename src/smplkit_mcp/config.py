"""Runtime configuration for the smplkit MCP server.

The server is a thin HTTP client to each smplkit product API. The only thing it
needs to know is where those APIs live. Each product's base host is configurable
so tests and the local platform (ADR-042) can point elsewhere, mirroring the
SDK's ``base_domain`` pattern.

For every product ``X`` (jobs, flags, config, logging, audit, app) the base URL
resolves as:

1. ``X_BASE_URL`` — a full URL override (e.g. ``http://localhost:8005``), used by
   tests and local runs.
2. ``X_BASE_DOMAIN`` (default ``x.smplkit.com``) combined with ``X_SCHEME``
   (default ``https``) — the SDK base-domain pattern.

``app`` is the API that owns environments (``list_environments``); it defaults to
``app.smplkit.com``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SCHEME = "https"

# Per-product env-var prefix → default base domain.
_PRODUCT_DOMAINS = {
    "JOBS": "jobs.smplkit.com",
    "FLAGS": "flags.smplkit.com",
    "CONFIG": "config.smplkit.com",
    "LOGGING": "logging.smplkit.com",
    "AUDIT": "audit.smplkit.com",
    "APP": "app.smplkit.com",
}


@dataclass(frozen=True)
class Settings:
    """Resolved server settings — one base URL per product API."""

    jobs_base_url: str
    flags_base_url: str
    config_base_url: str
    logging_base_url: str
    audit_base_url: str
    app_base_url: str


def _resolve_base_url(env: dict[str, str], prefix: str, default_domain: str) -> str:
    """Resolve one product's base URL from the environment."""
    override = env.get(f"{prefix}_BASE_URL")
    if override:
        return override.rstrip("/")
    domain = env.get(f"{prefix}_BASE_DOMAIN", default_domain)
    scheme = env.get(f"{prefix}_SCHEME", DEFAULT_SCHEME)
    return f"{scheme}://{domain}".rstrip("/")


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from the environment."""
    env = os.environ if env is None else env
    resolved = {
        prefix: _resolve_base_url(env, prefix, domain)
        for prefix, domain in _PRODUCT_DOMAINS.items()
    }
    return Settings(
        jobs_base_url=resolved["JOBS"],
        flags_base_url=resolved["FLAGS"],
        config_base_url=resolved["CONFIG"],
        logging_base_url=resolved["LOGGING"],
        audit_base_url=resolved["AUDIT"],
        app_base_url=resolved["APP"],
    )
