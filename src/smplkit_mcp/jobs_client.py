"""Thin HTTP client for the smplkit Jobs API.

Handles the JSON:API request/response envelopes and Bearer auth. One client is
built per MCP request from the customer's forwarded API key — the server holds
no platform credential and caches nothing across requests (ADR-057 §2.4).
"""
from __future__ import annotations

from typing import Any

import httpx

from .errors import JobsApiError

JSONAPI_MEDIA_TYPE = "application/vnd.api+json"


class JobsClient:
    """Per-request client for the Jobs REST API."""

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
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": JSONAPI_MEDIA_TYPE,
        }
        if json is not None:
            headers["Content-Type"] = JSONAPI_MEDIA_TYPE

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
    def _to_error(response: httpx.Response) -> JobsApiError:
        errors: list[dict[str, Any]] = []
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("errors"), list):
            errors = [e for e in body["errors"] if isinstance(e, dict)]
        return JobsApiError(response.status_code, errors)

    # -- jobs ---------------------------------------------------------------

    def list_jobs(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v1/jobs", params=params)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/jobs/{job_id}")

    def create_job(self, job_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        body = {"data": {"id": job_id, "type": "job", "attributes": attributes}}
        return self._request("POST", "/api/v1/jobs", json=body)

    def replace_job(self, job_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        body = {"data": {"id": job_id, "type": "job", "attributes": attributes}}
        return self._request("PUT", f"/api/v1/jobs/{job_id}", json=body)

    def delete_job(self, job_id: str) -> None:
        self._request("DELETE", f"/api/v1/jobs/{job_id}")

    def run_job(self, job_id: str, environment: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if environment is not None:
            body["environment"] = environment
        return self._request("POST", f"/api/v1/jobs/{job_id}/actions/run", json=body)

    # -- runs ---------------------------------------------------------------

    def list_runs(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v1/runs", params=params)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/runs/{run_id}")
