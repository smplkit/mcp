"""Smpl Config tools: hierarchical configuration with per-environment overrides.

The model expresses intent ("create a config", "set database.host to db-prod in
production", "list managed configs") and these functions translate it to the
Config JSON:API surface — hiding the ConfigItemDefinition shape, the flat
per-environment override map, the JSON:API envelope, and the full-replace PUT.
``set_config_value`` is a GET-mutate-PUT, exactly like ``set_flag``.

Plain functions over a :class:`ConfigClient`, FastMCP-independent and fully
unit-tested; ``server.py`` wraps each in an ``@mcp.tool``.
"""
from __future__ import annotations

import re
from typing import Any

from .client import JsonApiClient

DEFAULT_ENVIRONMENT = "production"

# Config item value types — the Config API's item `type` enum.
CONFIG_ITEM_TYPES = ("STRING", "NUMBER", "BOOLEAN", "JSON")

# Read-only attributes the API manages — never sent back on a PUT.
CONFIG_READONLY_FIELDS = ("created_at", "updated_at")


class ConfigClient(JsonApiClient):
    """Per-request client for the Config REST API."""

    resource_type = "config"

    def list_configs(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get("/api/v1/configs", params)

    def get_config(self, config_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/configs/{config_id}")

    def create_config(self, config_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        return self._create("/api/v1/configs", config_id, attributes)

    def replace_config(self, config_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        return self._replace(f"/api/v1/configs/{config_id}", config_id, attributes)

    def delete_config(self, config_id: str) -> None:
        self._delete(f"/api/v1/configs/{config_id}")


# -- transforms -------------------------------------------------------------


def clean_config(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten a config resource into clean, model-facing attributes."""
    attrs = resource.get("attributes") or {}
    return {
        "id": resource.get("id"),
        "name": attrs.get("name"),
        "description": attrs.get("description"),
        "parent": attrs.get("parent"),
        "items": attrs.get("items"),
        "environments": attrs.get("environments"),
        "managed": attrs.get("managed"),
        "created_at": attrs.get("created_at"),
        "updated_at": attrs.get("updated_at"),
    }


def _attributes_for_replace(attrs: dict[str, Any]) -> dict[str, Any]:
    """A fetched config's attributes, ready to PUT back (read-only stripped)."""
    return {k: v for k, v in attrs.items() if k not in CONFIG_READONLY_FIELDS}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:200] or "config"


def _infer_item_type(value: Any) -> str:
    """Infer a config item's type from a Python value."""
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float)):
        return "NUMBER"
    if isinstance(value, str):
        return "STRING"
    return "JSON"


def _normalize_items(items: dict[str, Any]) -> dict[str, Any]:
    """Normalize an items map into ConfigItemDefinition shape.

    Each value may be a bare value (type inferred) or a dict
    ``{"value", "type"?, "description"?}``. An explicit ``type`` is upper-cased
    and validated against :data:`CONFIG_ITEM_TYPES`.
    """
    out: dict[str, Any] = {}
    for key, spec in items.items():
        if isinstance(spec, dict) and "value" in spec:
            value = spec["value"]
            if spec.get("type") is not None:
                item_type = str(spec["type"]).strip().upper()
                if item_type not in CONFIG_ITEM_TYPES:
                    raise ValueError(
                        f"Unknown config item type {spec['type']!r}. "
                        "Use one of: string, number, boolean, json."
                    )
            else:
                item_type = _infer_item_type(value)
            definition: dict[str, Any] = {"value": value, "type": item_type}
            if "description" in spec:
                definition["description"] = spec["description"]
        else:
            definition = {"value": spec, "type": _infer_item_type(spec)}
        out[key] = definition
    return out


# -- tools ------------------------------------------------------------------


def create_config(
    client: ConfigClient,
    *,
    name: str,
    config_id: str | None = None,
    description: str | None = None,
    parent: str | None = None,
    items: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a config, optionally inheriting from a parent and declaring items."""
    resolved_id = config_id or _slugify(name)
    attributes: dict[str, Any] = {"name": name}
    if description is not None:
        attributes["description"] = description
    if parent is not None:
        attributes["parent"] = parent
    if items:
        attributes["items"] = _normalize_items(items)
    return clean_config(client.create_config(resolved_id, attributes)["data"])


def list_configs(
    client: ConfigClient,
    *,
    parent: str | None = None,
    search: str | None = None,
    managed: bool | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List configs with their items and per-environment overrides."""
    params: dict[str, Any] = {}
    if parent:
        params["filter[parent]"] = parent
    if search:
        params["filter[search]"] = search
    if managed is not None:
        params["filter[managed]"] = managed
    if limit:
        params["page[size]"] = limit
    body = client.list_configs(params or None)
    configs = [clean_config(resource) for resource in (body.get("data") or [])]
    return {"configs": configs, "count": len(configs)}


def get_config(client: ConfigClient, *, config_id: str) -> dict[str, Any]:
    """Fetch one config's full state: items plus per-environment overrides."""
    return clean_config(client.get_config(config_id)["data"])


def set_config_value(
    client: ConfigClient,
    *,
    config_id: str,
    key: str,
    value: Any,
    environment: str = DEFAULT_ENVIRONMENT,
) -> dict[str, Any]:
    """Set one item's value in one environment.

    GET-mutate-PUT full replace: other items and environments are preserved.
    The item is auto-declared (with an inferred type) if it isn't already, so
    this tool is fluent even on a config created with no items.
    """
    current = client.get_config(config_id)["data"]
    attributes = _attributes_for_replace(current.get("attributes") or {})

    items = dict(attributes.get("items") or {})
    if key not in items:
        items[key] = {"value": None, "type": _infer_item_type(value), "description": None}
    attributes["items"] = items

    envs = dict(attributes.get("environments") or {})
    entry = dict(envs.get(environment) or {})
    entry[key] = value
    envs[environment] = entry
    attributes["environments"] = envs

    return clean_config(client.replace_config(config_id, attributes)["data"])


def delete_config(client: ConfigClient, *, config_id: str) -> dict[str, Any]:
    """Delete a config."""
    client.delete_config(config_id)
    return {"deleted": True, "id": config_id}
