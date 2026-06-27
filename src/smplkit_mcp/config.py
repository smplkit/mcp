"""Runtime configuration for the smplkit Jobs MCP server.

The server is a thin HTTP client to the Jobs API. The only thing it needs to
know is where that API lives. The base host is configurable so tests and the
local platform (ADR-042) can point elsewhere, mirroring the SDK's
``base_domain`` pattern.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_JOBS_BASE_DOMAIN = "jobs.smplkit.com"
DEFAULT_SCHEME = "https"


@dataclass(frozen=True)
class Settings:
    """Resolved server settings."""

    jobs_base_url: str


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from the environment.

    Resolution order for the Jobs base URL:

    1. ``JOBS_BASE_URL`` — a full URL override (e.g. ``http://localhost:8005``),
       used by tests and local runs.
    2. ``JOBS_BASE_DOMAIN`` (default ``jobs.smplkit.com``) combined with
       ``JOBS_SCHEME`` (default ``https``) — the SDK base-domain pattern.
    """
    env = os.environ if env is None else env

    override = env.get("JOBS_BASE_URL")
    if override:
        return Settings(jobs_base_url=override.rstrip("/"))

    domain = env.get("JOBS_BASE_DOMAIN", DEFAULT_JOBS_BASE_DOMAIN)
    scheme = env.get("JOBS_SCHEME", DEFAULT_SCHEME)
    return Settings(jobs_base_url=f"{scheme}://{domain}".rstrip("/"))
