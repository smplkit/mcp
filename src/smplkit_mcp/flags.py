"""Smpl Flags tools: feature flags with per-environment targeting.

The model expresses intent ("create a boolean flag", "turn flag X on in prod",
"serve 'modern' to enterprise users in staging") and these functions translate it
to the Flags JSON:API surface — hiding the FlagEnvironment / FlagRule / FlagValue
nesting, the JSON:API envelope, and the full-replace PUT. ``set_flag`` is a
GET-mutate-PUT, exactly like ``update_job``.

Plain functions over a :class:`FlagsClient`, FastMCP-independent and fully
unit-tested; ``server.py`` wraps each in an ``@mcp.tool``.
"""
from __future__ import annotations

from typing import Any

from .client import JsonApiClient

DEFAULT_ENVIRONMENT = "production"

# Tool-facing type words → the Flags API's `type` enum.
_TYPE_ALIASES = {
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "STRING": "STRING",
    "STR": "STRING",
    "NUMBER": "NUMERIC",
    "NUMERIC": "NUMERIC",
    "JSON": "JSON",
}

# Targeting operators the Flags product supports (matches the SDK's `Op`).
OPERATORS = frozenset({"==", "!=", ">", "<", ">=", "<=", "in", "contains"})

# Read-only attributes the API manages — never sent back on a PUT.
FLAG_READONLY_FIELDS = ("sources", "created_at", "updated_at")

# Sentinel for set_flag's `value`: distinguishes "leave the per-environment
# default unchanged" from explicitly setting it to JSON ``null`` (which makes the
# environment fall back to the flag's global default). ``None`` can't serve as the
# sentinel because ``null`` is itself a valid value for string/json flags.
UNSET: Any = object()


class FlagsClient(JsonApiClient):
    """Per-request client for the Flags REST API."""

    resource_type = "flag"

    def list_flags(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get("/api/v1/flags", params)

    def get_flag(self, key: str) -> dict[str, Any]:
        return self._get(f"/api/v1/flags/{key}")

    def create_flag(self, key: str, attributes: dict[str, Any]) -> dict[str, Any]:
        return self._create("/api/v1/flags", key, attributes)

    def replace_flag(self, key: str, attributes: dict[str, Any]) -> dict[str, Any]:
        return self._replace(f"/api/v1/flags/{key}", key, attributes)

    def delete_flag(self, key: str) -> None:
        self._delete(f"/api/v1/flags/{key}")


# -- transforms -------------------------------------------------------------


def clean_flag(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten a flag resource into clean, model-facing attributes."""
    attrs = resource.get("attributes") or {}
    return {
        "id": resource.get("id"),
        "name": attrs.get("name"),
        "description": attrs.get("description"),
        "type": attrs.get("type"),
        "default": attrs.get("default"),
        "values": attrs.get("values"),
        "environments": _clean_environments(attrs.get("environments") or {}),
        "managed": attrs.get("managed"),
        "created_at": attrs.get("created_at"),
        "updated_at": attrs.get("updated_at"),
    }


def _clean_environments(envs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, entry in envs.items():
        entry = entry or {}
        out[name] = {
            "enabled": bool(entry.get("enabled", True)),
            "default": entry.get("default"),
            "rules": entry.get("rules") or [],
        }
    return out


def _attributes_for_replace(attrs: dict[str, Any]) -> dict[str, Any]:
    """A fetched flag's attributes, ready to PUT back (read-only fields stripped)."""
    return {k: v for k, v in attrs.items() if k not in FLAG_READONLY_FIELDS}


def _normalize_type(value: str) -> str:
    key = str(value).strip().upper()
    if key not in _TYPE_ALIASES:
        raise ValueError(
            f"Unknown flag type {value!r}. Use one of: boolean, string, number, json."
        )
    return _TYPE_ALIASES[key]


def _normalize_values(values: list[Any]) -> list[dict[str, Any]]:
    """Accept either bare scalars or {name, value} dicts as the allowed-value set."""
    out: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict) and "value" in item:
            out.append({"name": item.get("name") or str(item["value"]), "value": item["value"]})
        else:
            out.append({"name": str(item), "value": item})
    return out


def _build_rule(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a FlagRule (logic + value) from a simple targeting spec.

    ``spec`` is ``{"when": [{"attribute", "operator", "value"}, ...], "serve":
    <value>, "description"?: str}``. Multiple ``when`` conditions are AND-ed,
    matching the SDK's rule builder.
    """
    if "serve" not in spec:
        raise ValueError("each targeting rule needs a `serve` value.")
    conditions: list[dict[str, Any]] = []
    for cond in spec.get("when") or []:
        try:
            attribute = cond["attribute"]
            operator = cond["operator"]
            value = cond["value"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "each `when` condition needs `attribute`, `operator`, and `value`."
            ) from exc
        if operator not in OPERATORS:
            raise ValueError(
                f"Unsupported operator {operator!r}. Use one of: "
                "==, !=, >, <, >=, <=, in, contains."
            )
        if operator == "contains":
            # JSON Logic `in` with reversed operands: value in var.
            conditions.append({"in": [value, {"var": attribute}]})
        else:
            conditions.append({operator: [{"var": attribute}, value]})
    if not conditions:
        raise ValueError("each targeting rule needs at least one `when` condition.")
    logic = conditions[0] if len(conditions) == 1 else {"and": conditions}
    rule: dict[str, Any] = {"logic": logic, "value": spec["serve"]}
    if spec.get("description"):
        rule["description"] = spec["description"]
    return rule


# -- tools ------------------------------------------------------------------


def create_flag(
    client: FlagsClient,
    *,
    key: str,
    type: str,
    default: Any,
    name: str | None = None,
    description: str | None = None,
    values: list[Any] | None = None,
) -> dict[str, Any]:
    """Create a flag with an explicit type, key, and default value."""
    attributes: dict[str, Any] = {
        "name": name or key,
        "type": _normalize_type(type),
        "default": default,
    }
    if description is not None:
        attributes["description"] = description
    if values is not None:
        attributes["values"] = _normalize_values(values)
    return clean_flag(client.create_flag(key, attributes)["data"])


def list_flags(
    client: FlagsClient,
    *,
    type: str | None = None,
    search: str | None = None,
    managed: bool | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List flags with their per-environment state."""
    params: dict[str, Any] = {}
    if type:
        params["filter[type]"] = _normalize_type(type)
    if search:
        params["filter[search]"] = search
    if managed is not None:
        params["filter[managed]"] = managed
    if limit:
        params["page[size]"] = limit
    body = client.list_flags(params or None)
    flags = [clean_flag(resource) for resource in (body.get("data") or [])]
    return {"flags": flags, "count": len(flags)}


def get_flag(client: FlagsClient, *, key: str) -> dict[str, Any]:
    """Fetch one flag's full config: values plus targeting per environment."""
    return clean_flag(client.get_flag(key)["data"])


def set_flag(
    client: FlagsClient,
    *,
    key: str,
    environment: str = DEFAULT_ENVIRONMENT,
    value: Any = UNSET,
    enabled: bool | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Set a flag's value, kill switch, and targeting in one environment.

    GET-mutate-PUT full replace: other environments are preserved untouched.
    ``value`` is left unchanged when omitted; pass ``None`` to clear the
    environment's default so it falls back to the flag's global default.
    """
    current = client.get_flag(key)["data"]
    attributes = _attributes_for_replace(current.get("attributes") or {})

    envs = dict(attributes.get("environments") or {})
    entry = dict(envs.get(environment) or {})
    entry.setdefault("enabled", True)
    entry.setdefault("rules", [])

    if enabled is not None:
        entry["enabled"] = enabled
    if value is not UNSET:
        entry["default"] = value
    if rules is not None:
        entry["rules"] = [_build_rule(rule) for rule in rules]

    envs[environment] = entry
    attributes["environments"] = envs
    return clean_flag(client.replace_flag(key, attributes)["data"])


def delete_flag(client: FlagsClient, *, key: str) -> dict[str, Any]:
    """Delete a flag."""
    client.delete_flag(key)
    return {"deleted": True, "id": key}
