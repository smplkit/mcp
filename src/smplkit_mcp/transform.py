"""Translate JSON:API job/run envelopes into clean, model-facing attributes.

Tools never return raw JSON:API envelopes (ADR-057 §2.3). These helpers flatten
a resource into a plain dict, and prepare a fetched job for a full-replace PUT
by stripping server-managed read-only fields.
"""
from __future__ import annotations

from typing import Any

# Read-only job attributes the API manages — never sent back on a PUT.
JOB_READONLY_FIELDS = ("kind", "created_at", "updated_at", "deleted_at", "version")


def clean_job(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten a job resource into clean attributes."""
    attrs = resource.get("attributes") or {}
    config = attrs.get("configuration") or {}
    return {
        "id": resource.get("id"),
        "name": attrs.get("name"),
        "description": attrs.get("description"),
        "kind": attrs.get("kind"),
        "schedule": attrs.get("schedule"),
        "timezone": attrs.get("timezone"),
        "request": {
            "method": config.get("method"),
            "url": config.get("url"),
            "headers": config.get("headers"),
            "body": config.get("body"),
            "timeout": config.get("timeout"),
            "success_status": config.get("success_status"),
        },
        "retry_policy": attrs.get("retry_policy"),
        "environments": _clean_environments(attrs.get("environments") or {}),
        "created_at": attrs.get("created_at"),
        "updated_at": attrs.get("updated_at"),
        "version": attrs.get("version"),
    }


def _clean_environments(envs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, entry in envs.items():
        entry = entry or {}
        cleaned: dict[str, Any] = {
            "enabled": bool(entry.get("enabled", False)),
            "next_run_at": entry.get("next_run_at"),
        }
        overrides = {
            key: value
            for key, value in entry.items()
            if key not in ("enabled", "next_run_at")
        }
        if overrides:
            cleaned["overrides"] = overrides
        out[name] = cleaned
    return out


def clean_run(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten a run resource into clean attributes, including the captured result."""
    attrs = resource.get("attributes") or {}
    return {
        "id": resource.get("id"),
        "job": attrs.get("job"),
        "environment": attrs.get("environment"),
        "trigger": attrs.get("trigger"),
        "status": attrs.get("status"),
        "scheduled_for": attrs.get("scheduled_for"),
        "started_at": attrs.get("started_at"),
        "finished_at": attrs.get("finished_at"),
        "durations_ms": {
            "pending": attrs.get("pending_duration_ms"),
            "run": attrs.get("run_duration_ms"),
            "total": attrs.get("total_duration_ms"),
        },
        "failure_reason": attrs.get("failure_reason"),
        "error": attrs.get("error"),
        "result": _clean_result(attrs.get("result")),
        "created_at": attrs.get("created_at"),
    }


def _clean_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    return {
        "status": result.get("status"),
        "headers": result.get("headers"),
        "body": result.get("body"),
        "body_truncated": result.get("body_truncated"),
        "body_bytes": result.get("body_bytes"),
    }


def attributes_for_replace(attrs: dict[str, Any]) -> dict[str, Any]:
    """Return a fetched job's attributes ready to PUT back (full replace).

    Strips read-only top-level fields and the read-only per-environment
    ``next_run_at`` so the round-tripped body is a clean writable replacement.
    """
    out = {key: value for key, value in attrs.items() if key not in JOB_READONLY_FIELDS}
    envs = out.get("environments")
    if isinstance(envs, dict):
        out["environments"] = {
            name: {
                key: value
                for key, value in (entry or {}).items()
                if key != "next_run_at"
            }
            for name, entry in envs.items()
        }
    return out
