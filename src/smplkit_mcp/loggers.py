"""Smpl Logging tools: runtime log levels per logger, per environment.

The model expresses intent ("turn on debug logging for sqlalchemy.engine in
prod", "list managed loggers", "reset a logger to its default") and these
functions translate it to the Logging JSON:API surface — hiding the JSON:API
envelope, the per-environment ``{"level": ...}`` shape, and the full-replace
PUT. ``set_log_level`` is a GET-mutate-PUT, exactly like ``set_flag``, except
the PUT is an upsert: a logger that doesn't exist yet is created on first write.

A logger's ``id`` is its dot-separated key (e.g. ``"sqlalchemy.engine"``), not a
UUID. There is no POST create endpoint — creation happens via PUT upsert, so
``set_log_level`` does GET-then-PUT and falls back to a fresh attribute set when
the GET 404s.

Plain functions over a :class:`LoggerClient`, FastMCP-independent and fully
unit-tested; ``server.py`` wraps each in an ``@mcp.tool``.
"""
from __future__ import annotations

from typing import Any

from .client import JsonApiClient
from .errors import SmplkitApiError

DEFAULT_ENVIRONMENT = "production"

# The Logging product's LogLevel enum, in increasing-severity order.
LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL", "SILENT")

# Read-only attributes the API manages — never sent back on a PUT.
LOGGER_READONLY_FIELDS = ("sources", "effective_levels", "created_at", "updated_at")


class LoggerClient(JsonApiClient):
    """Per-request client for the Logging REST API."""

    resource_type = "logger"

    def list_loggers(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get("/api/v1/loggers", params)

    def get_logger(self, logger_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/loggers/{logger_id}")

    def replace_logger(self, logger_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        # PUT is an upsert: it creates the logger if it doesn't exist.
        return self._replace(f"/api/v1/loggers/{logger_id}", logger_id, attributes)

    def delete_logger(self, logger_id: str) -> None:
        self._delete(f"/api/v1/loggers/{logger_id}")


# -- transforms -------------------------------------------------------------


def clean_logger(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten a logger resource into clean, model-facing attributes."""
    attrs = resource.get("attributes") or {}
    return {
        "id": resource.get("id"),
        "name": attrs.get("name"),
        "level": attrs.get("level"),
        "group": attrs.get("group"),
        "managed": attrs.get("managed"),
        "environments": attrs.get("environments"),
        "effective_levels": attrs.get("effective_levels"),
        "created_at": attrs.get("created_at"),
        "updated_at": attrs.get("updated_at"),
    }


def _attributes_for_replace(attrs: dict[str, Any]) -> dict[str, Any]:
    """A fetched logger's attributes, ready to PUT back (read-only fields stripped)."""
    return {k: v for k, v in attrs.items() if k not in LOGGER_READONLY_FIELDS}


def _normalize_level(level: str) -> str:
    normalized = str(level).strip().upper()
    if normalized not in LOG_LEVELS:
        raise ValueError(
            f"Unknown log level {level!r}. Use one of: " + ", ".join(LOG_LEVELS) + "."
        )
    return normalized


# -- tools ------------------------------------------------------------------


def set_log_level(
    client: LoggerClient,
    *,
    logger_id: str,
    level: str,
    environment: str = DEFAULT_ENVIRONMENT,
) -> dict[str, Any]:
    """Set a logger's level in one environment.

    GET-mutate-PUT full replace: other environments are preserved untouched. The
    PUT is an upsert, so a logger that doesn't exist yet is created with just its
    name and the requested per-environment level.
    """
    normalized = _normalize_level(level)
    try:
        current = client.get_logger(logger_id)["data"]
        attributes = _attributes_for_replace(current.get("attributes") or {})
    except SmplkitApiError as exc:
        if exc.status_code != 404:
            raise
        # Upsert create path: name is the only required attribute.
        attributes = {"name": logger_id}

    envs = dict(attributes.get("environments") or {})
    envs[environment] = {"level": normalized}
    attributes["environments"] = envs
    return clean_logger(client.replace_logger(logger_id, attributes)["data"])


def list_loggers(
    client: LoggerClient,
    *,
    managed: bool | None = None,
    service: str | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List loggers with their account-wide and per-environment levels."""
    params: dict[str, Any] = {}
    if managed is not None:
        params["filter[managed]"] = managed
    if service:
        params["filter[service]"] = service
    if search:
        params["filter[search]"] = search
    if limit:
        params["page[size]"] = limit
    body = client.list_loggers(params or None)
    loggers = [clean_logger(resource) for resource in (body.get("data") or [])]
    return {"loggers": loggers, "count": len(loggers)}


def get_logger(client: LoggerClient, *, logger_id: str) -> dict[str, Any]:
    """Fetch one logger's full config: account-wide level plus per-environment levels."""
    return clean_logger(client.get_logger(logger_id)["data"])


def reset_logger(client: LoggerClient, *, logger_id: str) -> dict[str, Any]:
    """Reset a logger to its default by removing its managed config."""
    client.delete_logger(logger_id)
    return {"deleted": True, "id": logger_id}
