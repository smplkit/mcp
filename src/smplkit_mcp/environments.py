"""Smpl Environments tools: the deployment targets flags and config vary across.

Environments are the named contexts ("production", "staging", "preview-42") that
Flags target and Config scopes to. This module exposes the one read tool the
model needs — listing them — so it can name a valid environment when setting a
flag or scoping a config value. The environment's ``id`` *is* its key (there is
no separate ``key`` attribute), mirroring how the API addresses them.

Plain functions over an :class:`EnvironmentsClient`, FastMCP-independent and
fully unit-tested; ``server.py`` wraps each in an ``@mcp.tool``.
"""
from __future__ import annotations

from typing import Any

from .client import JsonApiClient


class EnvironmentsClient(JsonApiClient):
    """Per-request client for the Environments REST API."""

    resource_type = "environment"

    def list_environments(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get("/api/v1/environments", params)


# -- transforms -------------------------------------------------------------


def clean_environment(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten an environment resource into clean, model-facing attributes.

    The resource ``id`` is the environment key — there is no separate ``key``
    attribute — so it is surfaced as ``key``.
    """
    attrs = resource.get("attributes") or {}
    return {
        "key": resource.get("id"),
        "name": attrs.get("name"),
        "classification": attrs.get("classification"),
        "managed": attrs.get("managed"),
        "color": attrs.get("color"),
        "created_at": attrs.get("created_at"),
        "updated_at": attrs.get("updated_at"),
    }


# -- tools ------------------------------------------------------------------


def list_environments(
    client: EnvironmentsClient,
    *,
    classification: str | None = None,
    managed: bool | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List environments, optionally filtered by classification, managed, or search."""
    params: dict[str, Any] = {}
    if classification:
        params["filter[classification]"] = classification
    if managed is not None:
        params["filter[managed]"] = managed
    if search:
        params["filter[search]"] = search
    if limit:
        params["page[size]"] = limit
    body = client.list_environments(params or None)
    environments = [clean_environment(resource) for resource in (body.get("data") or [])]
    return {"environments": environments, "count": len(environments)}
