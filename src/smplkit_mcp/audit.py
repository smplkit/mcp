"""Smpl Audit tools: the audit event log and its outbound forwarders.

The model expresses intent ("show me the ERROR events from last week", "forward
audit events to my Datadog/HTTP endpoint", "test that this forwarder URL works")
and these functions translate it to the Audit JSON:API surface — hiding the
JSON:API envelope, the per-environment forwarder enablement, and the
``test_forwarder`` RPC's flat (non-JSON:API) request/response shape.

Events are read-only (``query_events`` / ``get_event``); forwarders are
created, listed, deleted, and dry-run tested. Plain functions over an
:class:`AuditClient`, FastMCP-independent and fully unit-tested; ``server.py``
wraps each in an ``@mcp.tool``.
"""
from __future__ import annotations

import re
from typing import Any

from .client import JsonApiClient
from .errors import SmplkitApiError  # noqa: F401  (re-exported for tool error mapping)
from .urls import NonPublicTargetError, require_public_target  # noqa: F401

DEFAULT_ENVIRONMENT = "production"

# Forwarder destination types (matches the Audit API's ForwarderType enum).
FORWARDER_TYPES = (
    "datadog",
    "elastic",
    "honeycomb",
    "http",
    "new_relic",
    "splunk_hec",
    "sumo_logic",
)

# Event severity levels, low → high (matches the Audit API's Severity enum).
SEVERITIES = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")


class AuditClient(JsonApiClient):
    """Per-request client for the Audit REST API."""

    resource_type = "forwarder"

    # -- events (read-only) -------------------------------------------------

    def list_events(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get("/api/v1/events", params)

    def get_event(self, event_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/events/{event_id}")

    # -- forwarders ---------------------------------------------------------

    def list_forwarders(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get("/api/v1/forwarders", params)

    def create_forwarder(self, forwarder_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        return self._create("/api/v1/forwarders", forwarder_id, attributes)

    def delete_forwarder(self, forwarder_id: str) -> None:
        self._delete(f"/api/v1/forwarders/{forwarder_id}")

    def test_forwarder(self, body: dict[str, Any]) -> dict[str, Any]:
        # Flat application/json RPC, not a JSON:API envelope.
        return self._post_flat(
            "/api/v1/functions/test_forwarder/actions/execute", body
        )


# -- transforms -------------------------------------------------------------


def clean_event(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten an event resource into clean, model-facing attributes."""
    attrs = resource.get("attributes") or {}
    return {
        "id": resource.get("id"),
        "event_type": attrs.get("event_type"),
        "resource_type": attrs.get("resource_type"),
        "resource_id": attrs.get("resource_id"),
        "description": attrs.get("description"),
        "severity": attrs.get("severity"),
        "category": attrs.get("category"),
        "occurred_at": attrs.get("occurred_at"),
        "actor_type": attrs.get("actor_type"),
        "actor_id": attrs.get("actor_id"),
        "actor_label": attrs.get("actor_label"),
        "environment": attrs.get("environment"),
        "data": attrs.get("data"),
        "created_at": attrs.get("created_at"),
    }


def clean_forwarder(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten a forwarder resource into clean, model-facing attributes."""
    attrs = resource.get("attributes") or {}
    return {
        "id": resource.get("id"),
        "name": attrs.get("name"),
        "description": attrs.get("description"),
        "forwarder_type": attrs.get("forwarder_type"),
        "configuration": attrs.get("configuration"),
        "environments": attrs.get("environments"),
        "filter": attrs.get("filter"),
        "forward_smplkit_events": attrs.get("forward_smplkit_events"),
        "transform_type": attrs.get("transform_type"),
        "transform": attrs.get("transform"),
        "version": attrs.get("version"),
        "created_at": attrs.get("created_at"),
        "updated_at": attrs.get("updated_at"),
    }


def clean_test_result(result: dict[str, Any]) -> dict[str, Any]:
    """Flatten a ``test_forwarder`` result into a stable, model-facing shape."""
    return {
        "succeeded": result.get("succeeded"),
        "response_status": result.get("response_status"),
        "response_headers": result.get("response_headers"),
        "response_body": result.get("response_body"),
        "latency_ms": result.get("latency_ms"),
        "error": result.get("error"),
    }


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:200] or "forwarder"


def _normalize_severity(value: str) -> str:
    key = str(value).strip().upper()
    if key not in SEVERITIES:
        raise ValueError(
            f"Unknown severity {value!r}. Use one of: "
            "TRACE, DEBUG, INFO, WARN, ERROR, FATAL."
        )
    return key


def _normalize_forwarder_type(value: str) -> str:
    key = str(value).strip().lower()
    if key not in FORWARDER_TYPES:
        raise ValueError(
            f"Unknown forwarder type {value!r}. Use one of: "
            "datadog, elastic, honeycomb, http, new_relic, splunk_hec, sumo_logic."
        )
    return key


# -- tools: events ----------------------------------------------------------


def query_events(
    client: AuditClient,
    *,
    actor_type: str | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    environment: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search: str | None = None,
    limit: int | None = 50,
) -> dict[str, Any]:
    """Query the audit event log by actor, resource, category, severity, and/or time window."""
    params: dict[str, Any] = {}
    if actor_type:
        params["filter[actor_type]"] = actor_type
    if actor_id:
        params["filter[actor_id]"] = actor_id
    if event_type:
        params["filter[event_type]"] = event_type
    if resource_type:
        params["filter[resource_type]"] = resource_type
    if resource_id:
        params["filter[resource_id]"] = resource_id
    if category:
        params["filter[category]"] = category
    if severity:
        params["filter[severity]"] = _normalize_severity(severity)
    if environment:
        params["filter[environment]"] = environment
    if since or until:
        params["filter[occurred_at]"] = f"[{since or '*'},{until or '*'})"
    if search:
        params["filter[search]"] = search
    if limit:
        params["page[size]"] = limit

    body = client.list_events(params or None)
    events = [clean_event(resource) for resource in (body.get("data") or [])]
    return {"events": events, "count": len(events)}


def get_event(client: AuditClient, *, event_id: str) -> dict[str, Any]:
    """Fetch one audit event by id."""
    return clean_event(client.get_event(event_id)["data"])


# -- tools: forwarders ------------------------------------------------------


def list_forwarders(
    client: AuditClient,
    *,
    forwarder_type: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List configured forwarders and their per-environment enablement."""
    params: dict[str, Any] = {}
    if forwarder_type:
        params["filter[forwarder_type]"] = _normalize_forwarder_type(forwarder_type)
    if limit:
        params["page[size]"] = limit
    body = client.list_forwarders(params or None)
    forwarders = [clean_forwarder(resource) for resource in (body.get("data") or [])]
    return {"forwarders": forwarders, "count": len(forwarders)}


def create_forwarder(
    client: AuditClient,
    *,
    name: str,
    url: str,
    forwarder_type: str = "http",
    forwarder_id: str | None = None,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    success_status: str | None = None,
    filter: dict[str, Any] | None = None,
    environment: str = DEFAULT_ENVIRONMENT,
    enabled: bool = True,
    description: str | None = None,
    forward_smplkit_events: bool | None = None,
) -> dict[str, Any]:
    """Create an outbound forwarder that posts audit events to a public HTTP endpoint."""
    require_public_target(url)
    ftype = _normalize_forwarder_type(forwarder_type)

    configuration: dict[str, Any] = {"url": url, "method": method}
    if headers:
        configuration["headers"] = headers
    if success_status:
        configuration["success_status"] = success_status

    attributes: dict[str, Any] = {
        "name": name,
        "forwarder_type": ftype,
        "configuration": configuration,
        "environments": {environment: {"enabled": enabled}},
    }
    if filter is not None:
        attributes["filter"] = filter
    if description is not None:
        attributes["description"] = description
    if forward_smplkit_events is not None:
        attributes["forward_smplkit_events"] = forward_smplkit_events

    resolved_id = forwarder_id or _slugify(name)
    return clean_forwarder(client.create_forwarder(resolved_id, attributes)["data"])


def test_forwarder(
    client: AuditClient,
    *,
    url: str,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    success_status: str | None = None,
    body: str | None = None,
    timeout_ms: int | None = None,
    tls_verify: bool | None = None,
    ca_cert: str | None = None,
) -> dict[str, Any]:
    """Dry-run an endpoint: send a sample request and report status, latency, and body."""
    require_public_target(url)
    payload: dict[str, Any] = {"url": url, "method": method}
    if headers is not None:
        payload["headers"] = headers
    if success_status is not None:
        payload["success_status"] = success_status
    if body is not None:
        payload["body"] = body
    if timeout_ms is not None:
        payload["timeout_ms"] = timeout_ms
    if tls_verify is not None:
        payload["tls_verify"] = tls_verify
    if ca_cert is not None:
        payload["ca_cert"] = ca_cert
    return clean_test_result(client.test_forwarder(payload))


def delete_forwarder(client: AuditClient, *, forwarder_id: str) -> dict[str, Any]:
    """Delete a forwarder. Past forwarded events are unaffected."""
    client.delete_forwarder(forwarder_id)
    return {"deleted": True, "id": forwarder_id}
