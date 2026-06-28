"""Shared per-request HTTP client for the smplkit product APIs.

Every product (flags, config, logging, audit, environments) speaks JSON:API over
HTTP with Bearer auth — the same surface the Jobs client established. This base
class carries that machinery so each product client is a thin set of named
methods over it. One client is built per MCP request from the customer's
forwarded API key — the server holds no platform credential and caches nothing
across requests (ADR-057 §2.4).

The Jobs client (:mod:`smplkit_mcp.jobs_client`) predates this base and stays
standalone as the reference implementation; new products subclass here.
"""
from __future__ import annotations

from typing import Any

import httpx

from .errors import SmplkitApiError

JSONAPI_MEDIA_TYPE = "application/vnd.api+json"
JSON_MEDIA_TYPE = "application/json"


class JsonApiClient:
    """Per-request JSON:API client. Subclasses set ``resource_type``."""

    #: JSON:API ``type`` string for this client's primary resource.
    resource_type: str = ""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # An injectable transport keeps the client unit-testable without network.
        self._transport = transport

    # -- low-level request --------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        media_type: str = JSONAPI_MEDIA_TYPE,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": media_type,
        }
        if json is not None:
            headers["Content-Type"] = media_type

        with httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            response = client.request(method, path, params=params, json=json, headers=headers)

        if response.status_code >= 400:
            raise self._to_error(response)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @staticmethod
    def _to_error(response: httpx.Response) -> SmplkitApiError:
        errors: list[dict[str, Any]] = []
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("errors"), list):
            errors = [e for e in body["errors"] if isinstance(e, dict)]
        return SmplkitApiError(response.status_code, errors)

    # -- JSON:API resource helpers ------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _create(
        self,
        path: str,
        resource_id: str,
        attributes: dict[str, Any],
        *,
        resource_type: str | None = None,
    ) -> Any:
        body = {
            "data": {
                "id": resource_id,
                "type": resource_type or self.resource_type,
                "attributes": attributes,
            }
        }
        return self._request("POST", path, json=body)

    def _replace(
        self,
        path: str,
        resource_id: str | None,
        attributes: dict[str, Any],
        *,
        resource_type: str | None = None,
    ) -> Any:
        data: dict[str, Any] = {
            "type": resource_type or self.resource_type,
            "attributes": attributes,
        }
        if resource_id is not None:
            data["id"] = resource_id
        return self._request("PUT", path, json={"data": data})

    def _delete(self, path: str) -> None:
        self._request("DELETE", path)

    def _post_flat(self, path: str, body: dict[str, Any]) -> Any:
        """POST a plain ``application/json`` body (non-JSON:API RPC endpoints)."""
        return self._request("POST", path, json=body, media_type=JSON_MEDIA_TYPE)
