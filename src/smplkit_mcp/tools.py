"""The eight Jobs tools, as plain functions over a :class:`JobsClient`.

This is the testable core: kind inference, the ``update_job`` GET-mutate-PUT,
JSON:API envelope handling and run polling all live here, independent of
FastMCP. ``server.py`` wraps each function in an ``@mcp.tool`` that supplies the
per-request client and maps errors to ``ToolError``.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from .errors import JobsApiError
from .jobs_client import JobsClient
from .kinds import infer_kind, resolve_schedule
from .transform import attributes_for_replace, clean_job, clean_run
from .urls import PUBLIC_URL_GUIDANCE, require_public_target

DEFAULT_ENVIRONMENT = "production"
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELED"})

# Failure reasons that have a specific, actionable hint for the agent.
_FAILURE_HINTS = {
    "SSRF_BLOCKED": PUBLIC_URL_GUIDANCE,
    "QUOTA_EXCEEDED": (
        "This run failed because the account is over its run allotment for the "
        "period. Upgrade your plan at https://smplkit.com to raise the limit."
    ),
}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:200] or "job"


def _annotate_failure(run: dict[str, Any]) -> dict[str, Any]:
    hint = _FAILURE_HINTS.get(run.get("failure_reason") or "")
    if hint:
        run["hint"] = hint
    return run


# -- jobs -------------------------------------------------------------------


def list_jobs(
    client: JobsClient,
    *,
    name: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List jobs, each enriched with its latest completed run."""
    params: dict[str, Any] = {}
    if name:
        params["filter[name]"] = name
    if kind:
        params["filter[kind]"] = kind
    if limit:
        params["page[size]"] = limit

    body = client.list_jobs(params=params or None)
    jobs = [clean_job(resource) for resource in (body.get("data") or [])]

    latest = _latest_runs_by_job(client)
    for job in jobs:
        job["last_run"] = latest.get(job["id"])
    return {"jobs": jobs, "count": len(jobs)}


def _latest_runs_by_job(client: JobsClient) -> dict[str, dict[str, Any]]:
    """Map each job id to a summary of its most recent completed run.

    Uses ``last_run_only`` (one completed run per job-and-environment) and keeps
    the newest across environments. Best-effort: enrichment never fails a list.
    """
    try:
        body = client.list_runs(params={"last_run_only": "true", "page[size]": 1000})
    except JobsApiError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for resource in body.get("data") or []:
        run = clean_run(resource)
        job_id = run.get("job")
        if job_id is None:
            continue
        previous = out.get(job_id)
        if previous is None or (run.get("created_at") or "") > (previous["_created_at"] or ""):
            result = run.get("result") or {}
            out[job_id] = {
                "id": run["id"],
                "status": run["status"],
                "environment": run["environment"],
                "trigger": run["trigger"],
                "finished_at": run["finished_at"],
                "result_status": result.get("status"),
                "failure_reason": run.get("failure_reason"),
                "_created_at": run.get("created_at"),
            }
    for summary in out.values():
        summary.pop("_created_at", None)
    return out


def get_job(client: JobsClient, *, job_id: str) -> dict[str, Any]:
    """Fetch a single job's configuration."""
    return clean_job(client.get_job(job_id)["data"])


def create_job(
    client: JobsClient,
    *,
    name: str,
    url: str,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout: int | None = None,
    schedule: str | None = None,
    run_at: str | None = None,
    timezone: str | None = None,
    retry_policy: str | None = None,
    environment: str = DEFAULT_ENVIRONMENT,
    description: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Create a job; the kind is inferred from ``schedule``/``run_at``."""
    require_public_target(url)
    schedule_value = resolve_schedule(schedule=schedule, run_at=run_at)
    if timezone is not None and infer_kind(schedule_value) != "recurring":
        raise ValueError(
            "`timezone` only applies to a recurring job — provide a cron `schedule` too."
        )

    configuration: dict[str, Any] = {"url": url, "method": method}
    if headers:
        configuration["headers"] = headers
    if body is not None:
        configuration["body"] = body
    if timeout is not None:
        configuration["timeout"] = timeout

    attributes: dict[str, Any] = {
        "name": name,
        "configuration": configuration,
        "environments": {environment: {"enabled": True}},
    }
    if schedule_value is not None:
        attributes["schedule"] = schedule_value
    if timezone is not None:
        attributes["timezone"] = timezone
    if retry_policy is not None:
        attributes["retry_policy"] = retry_policy
    if description is not None:
        attributes["description"] = description

    resolved_id = job_id or _slugify(name)
    response = client.create_job(resolved_id, attributes)
    return clean_job(response["data"])


def update_job(
    client: JobsClient,
    *,
    job_id: str,
    name: str | None = None,
    url: str | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout: int | None = None,
    schedule: str | None = None,
    run_at: str | None = None,
    timezone: str | None = None,
    retry_policy: str | None = None,
    description: str | None = None,
    enabled: bool | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """Change a job via GET-mutate-PUT: fetch, apply the partial change, full-replace."""
    current = client.get_job(job_id)["data"]
    attributes = attributes_for_replace(current.get("attributes") or {})

    if name is not None:
        attributes["name"] = name
    if description is not None:
        attributes["description"] = description
    if retry_policy is not None:
        attributes["retry_policy"] = retry_policy

    configuration = dict(attributes.get("configuration") or {})
    if url is not None:
        require_public_target(url)
        configuration["url"] = url
    if method is not None:
        configuration["method"] = method
    if headers is not None:
        configuration["headers"] = headers
    if body is not None:
        configuration["body"] = body
    if timeout is not None:
        configuration["timeout"] = timeout
    attributes["configuration"] = configuration

    if schedule is not None or run_at is not None:
        schedule_value = resolve_schedule(schedule=schedule, run_at=run_at)
        if schedule_value is None:
            attributes.pop("schedule", None)
        else:
            attributes["schedule"] = schedule_value
    if timezone is not None:
        attributes["timezone"] = timezone

    if enabled is not None:
        env = environment or DEFAULT_ENVIRONMENT
        envs = dict(attributes.get("environments") or {})
        entry = dict(envs.get(env) or {})
        entry["enabled"] = enabled
        envs[env] = entry
        attributes["environments"] = envs

    response = client.replace_job(job_id, attributes)
    return clean_job(response["data"])


def delete_job(client: JobsClient, *, job_id: str) -> dict[str, Any]:
    """Delete a job. Its run history is retained."""
    client.delete_job(job_id)
    return {"deleted": True, "id": job_id}


def run_job(
    client: JobsClient,
    *,
    job_id: str,
    environment: str | None = None,
    wait: bool = True,
    timeout_seconds: float = 25.0,
    poll_interval: float = 1.0,
    _sleep: Callable[[float], None] = time.sleep,
    _clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Fire one immediate run and (by default) wait for it to finish.

    The run is enqueued and executed by the worker, so it may return ``PENDING``;
    when ``wait`` is set we poll until a terminal state (or timeout) so the
    captured response is available to the caller.
    """
    response = client.run_job(job_id, environment=environment)
    run = clean_run(response["data"])

    if wait and run["status"] not in TERMINAL_STATUSES:
        deadline = _clock() + timeout_seconds
        while run["status"] not in TERMINAL_STATUSES and _clock() < deadline:
            _sleep(poll_interval)
            run = clean_run(client.get_run(run["id"])["data"])

    return _annotate_failure(run)


# -- runs -------------------------------------------------------------------


def list_runs(
    client: JobsClient,
    *,
    job: str | None = None,
    status: str | list[str] | None = None,
    failed_only: bool = False,
    trigger: str | list[str] | None = None,
    environment: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    last_run_only: bool = False,
    limit: int | None = 50,
) -> dict[str, Any]:
    """List runs by job, status, trigger, environment, and/or a time window."""
    params: dict[str, Any] = {}
    if job:
        params["filter[job]"] = job
    if failed_only and not status:
        status = "FAILED"
    if status:
        params["filter[status]"] = status if isinstance(status, str) else ",".join(status)
    if trigger:
        params["filter[trigger]"] = trigger if isinstance(trigger, str) else ",".join(trigger)
    if environment:
        params["filter[environment]"] = (
            environment if isinstance(environment, str) else ",".join(environment)
        )
    if since or until:
        params["filter[created_at]"] = f"[{since or '*'},{until or '*'})"
    if last_run_only:
        params["last_run_only"] = "true"
    if limit:
        params["page[size]"] = limit

    body = client.list_runs(params=params or None)
    runs = [clean_run(resource) for resource in (body.get("data") or [])]
    return {"runs": runs, "count": len(runs)}


def get_run(client: JobsClient, *, run_id: str) -> dict[str, Any]:
    """Fetch one run, including its captured response and timings."""
    return _annotate_failure(clean_run(client.get_run(run_id)["data"]))
