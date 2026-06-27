"""Shared test fixtures.

``FakeJobsClient`` mimics the JSON:API surface of :class:`JobsClient` with an
in-memory store, so the tool logic (kind inference, GET-mutate-PUT, run polling,
envelope handling) is exercised without any network.
"""
from __future__ import annotations

from typing import Any

import pytest

from smplkit_mcp.errors import JobsApiError
from smplkit_mcp.kinds import infer_kind


def _with_next_run(environments: dict[str, Any]) -> dict[str, Any]:
    """Fold a read-only ``next_run_at`` into each environment entry, as the API does."""
    out: dict[str, Any] = {}
    for name, entry in environments.items():
        entry = dict(entry or {})
        entry["next_run_at"] = "2099-01-01T00:00:00Z" if entry.get("enabled") else None
        out[name] = entry
    return out


def _store_job(attributes: dict[str, Any], *, version: int) -> dict[str, Any]:
    stored = dict(attributes)
    stored["kind"] = infer_kind(attributes.get("schedule"))
    stored["version"] = version
    stored["created_at"] = "2026-06-27T00:00:00Z"
    stored["updated_at"] = "2026-06-27T00:00:00Z"
    stored["deleted_at"] = None
    stored["environments"] = _with_next_run(attributes.get("environments") or {})
    return stored


class FakeJobsClient:
    """In-memory stand-in for :class:`JobsClient`."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # run_job returns this status; queue states in run_state_sequence for polling.
        self.run_now_status = "PENDING"
        self.run_state_sequence: list[dict[str, Any]] = []
        self._run_counter = 0

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    def calls_named(self, name: str) -> list[dict[str, Any]]:
        return [kw for n, kw in self.calls if n == name]

    # -- jobs ---------------------------------------------------------------

    def list_jobs(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._record("list_jobs", params=params)
        data = [
            {"id": jid, "type": "job", "attributes": attrs}
            for jid, attrs in self.jobs.items()
        ]
        return {"data": data, "meta": {"pagination": {"page": 1, "size": len(data)}}}

    def get_job(self, job_id: str) -> dict[str, Any]:
        self._record("get_job", job_id=job_id)
        if job_id not in self.jobs:
            raise JobsApiError(404, [{"status": "404", "title": "Not Found",
                                      "detail": f"No job '{job_id}'."}])
        return {"data": {"id": job_id, "type": "job", "attributes": self.jobs[job_id]}}

    def create_job(self, job_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        self._record("create_job", job_id=job_id, attributes=attributes)
        if job_id in self.jobs:
            raise JobsApiError(409, [{"status": "409", "title": "Conflict",
                                      "detail": f"Job '{job_id}' already exists."}])
        stored = _store_job(attributes, version=1)
        self.jobs[job_id] = stored
        return {"data": {"id": job_id, "type": "job", "attributes": stored}}

    def replace_job(self, job_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        self._record("replace_job", job_id=job_id, attributes=attributes)
        if job_id not in self.jobs:
            raise JobsApiError(404, [{"status": "404", "title": "Not Found",
                                      "detail": f"No job '{job_id}'."}])
        version = self.jobs[job_id].get("version", 1) + 1
        stored = _store_job(attributes, version=version)
        self.jobs[job_id] = stored
        return {"data": {"id": job_id, "type": "job", "attributes": stored}}

    def delete_job(self, job_id: str) -> None:
        self._record("delete_job", job_id=job_id)
        self.jobs.pop(job_id, None)

    def run_job(self, job_id: str, environment: str | None = None) -> dict[str, Any]:
        self._record("run_job", job_id=job_id, environment=environment)
        self._run_counter += 1
        run_id = f"run-{self._run_counter}"
        attrs = {
            "job": job_id,
            "environment": environment or "production",
            "trigger": "MANUAL",
            "status": self.run_now_status,
            "result": None,
            "created_at": f"2026-06-27T00:00:0{self._run_counter}Z",
        }
        self.runs[run_id] = attrs
        return {"data": {"id": run_id, "type": "run", "attributes": attrs}}

    def get_run(self, run_id: str) -> dict[str, Any]:
        self._record("get_run", run_id=run_id)
        if self.run_state_sequence:
            self.runs[run_id] = {**self.runs.get(run_id, {}), **self.run_state_sequence.pop(0)}
        return {"data": {"id": run_id, "type": "run", "attributes": self.runs[run_id]}}

    def list_runs(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._record("list_runs", params=params)
        data = [
            {"id": rid, "type": "run", "attributes": attrs}
            for rid, attrs in self.runs.items()
        ]
        return {"data": data}


@pytest.fixture()
def fake_client() -> FakeJobsClient:
    return FakeJobsClient()
