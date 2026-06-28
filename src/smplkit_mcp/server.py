"""FastMCP server exposing the eight smplkit Jobs tools (ADR-057).

Each tool extracts the customer's smplkit API key from the inbound request,
builds a per-request :class:`JobsClient`, and delegates to the plain functions
in :mod:`smplkit_mcp.tools`. The server is stateless: it holds no platform
credential, never caches a key across requests, and never logs the key.

Tool parameter descriptions live in each function's Google-style ``Args``
docstring; FastMCP surfaces them in the tool input schema the model reads.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import oauth, tools
from .config import load_settings
from .errors import JobsApiError, MissingApiKeyError, friendly_message
from .jobs_client import JobsClient
from .urls import NonPublicTargetError

APP_VERSION = os.environ.get("APP_VERSION", "dev")
SETTINGS = load_settings()

# OAuth resource-server provider (ADR-058). None unless an authorization server is
# configured via MCP_OAUTH_* env, in which case FastMCP serves Protected Resource
# Metadata, the 401/WWW-Authenticate challenge, and token validation. Disabled by
# default, so the live server keeps the API-key pass-through unchanged.
AUTH_PROVIDER = oauth.build_auth_provider(oauth.SETTINGS)

INSTRUCTIONS = (
    "The agent gateway to the smplkit platform — it exposes smplkit's capabilities "
    "as tools you can call on the user's behalf. Available now: Smpl Jobs "
    "(scheduled HTTP jobs); more of the platform will be added here over time. "
    "Use create_job to schedule a request (a cron `schedule` makes it recurring; a "
    "`run_at` datetime makes it run once; neither makes it manual/on-demand), "
    "run_job to fire a job immediately and prove it works (it returns the captured "
    "response), and list_runs/get_run to monitor (e.g. status=FAILED answers 'has "
    "anything failed?'). Job target URLs must be reachable from the public internet."
)

mcp: FastMCP = FastMCP(
    name="smplkit MCP Server",
    instructions=INSTRUCTIONS,
    version=APP_VERSION,
    # Mask unexpected internal exceptions; user-facing detail is raised as a
    # ToolError (which bypasses masking). Keeps internals — and the key — unleaked.
    mask_error_details=True,
    # None in the default (API-key-only) deployment; an OAuth resource-server
    # provider when MCP_OAUTH_* is configured (ADR-058).
    auth=AUTH_PROVIDER,
)


# --------------------------------------------------------------------------
# Per-request auth + error mapping
# --------------------------------------------------------------------------


def extract_api_key(headers: dict[str, str]) -> str:
    """Return the customer's smplkit API key from inbound request headers.

    Accepts ``Authorization: Bearer <key>`` (preferred) or a custom
    ``X-Smplkit-Api-Key`` header. Header keys arrive lowercased.
    """
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        token = auth[7:].strip()
        if token:
            return token
    custom = headers.get("x-smplkit-api-key", "").strip()
    if custom:
        return custom
    raise MissingApiKeyError()


def _client() -> JobsClient:
    # include_all=True because FastMCP 3.x strips Authorization from
    # get_http_headers() by default.
    headers = get_http_headers(include_all=True)
    try:
        token = extract_api_key(headers)
    except MissingApiKeyError as exc:
        raise ToolError(str(exc)) from exc
    # In OAuth mode the request has already been authenticated (a valid JWT or a
    # smplkit API key). An API key forwards downstream as today; a validated
    # OAuth token cannot yet be exchanged for a product-API credential, so refuse
    # rather than mis-forward a token the product API would reject (ADR-058).
    if oauth.SETTINGS.enabled and not oauth.looks_like_api_key(token):
        raise ToolError(oauth.OAUTH_EXCHANGE_PENDING_MESSAGE)
    return JobsClient(token, SETTINGS.jobs_base_url)


def _call(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """Run a tool function with a per-request client, mapping errors to ToolError."""
    client = _client()
    try:
        return fn(client, **kwargs)
    except (NonPublicTargetError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except JobsApiError as exc:
        raise ToolError(friendly_message(exc)) from exc


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool
def list_jobs(
    name: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List the account's configured jobs, each enriched with its latest run.

    Use this to see what's scheduled and whether each job's most recent run
    succeeded or failed. Each job includes its schedule, target request, and a
    `last_run` summary (status and captured HTTP status).

    Args:
        name: Filter to jobs whose name contains this text (case-insensitive).
        kind: Filter by kind: 'recurring', 'manual', or 'one_off'.
        limit: Maximum number of jobs to return.
    """
    return _call(tools.list_jobs, name=name, kind=kind, limit=limit)


@mcp.tool
def get_job(job_id: str) -> dict[str, Any]:
    """Get one job's full configuration (schedule, target request, environments).

    Args:
        job_id: The job's id.
    """
    return _call(tools.get_job, job_id=job_id)


@mcp.tool
def create_job(
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
    environment: str = tools.DEFAULT_ENVIRONMENT,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a scheduled HTTP job. The kind is inferred — never set it yourself.

    - A cron `schedule` -> a RECURRING job that fires on that cadence.
    - A `run_at` datetime -> a ONE-OFF job that runs a single time.
    - Neither -> a MANUAL job that runs only when you call run_job.

    The target URL must be publicly reachable. After creating a recurring job,
    call run_job to prove it works with a real captured response.

    Args:
        name: Human-readable name for the job.
        url: Destination URL to call. Must be reachable from the public internet
            (no localhost or private IPs).
        method: HTTP method — GET, POST, PUT, PATCH, or DELETE.
        headers: HTTP headers to send, as a name->value object. Set a secret auth
            header so only smplkit can call your endpoint.
        body: Request body sent verbatim on each run. Pair with a matching
            Content-Type header.
        timeout: Per-run timeout in seconds (default 30).
        schedule: A 5-field cron expression for a RECURRING job (e.g. '0 7 * * *'
            for 7am daily). Omit for a manual or one-time job.
        run_at: An ISO-8601 datetime to run the job ONCE (or 'now' to run once
            immediately). Mutually exclusive with `schedule`.
        timezone: IANA timezone the cron `schedule` runs in (e.g.
            'America/New_York'). Recurring jobs only; defaults to UTC.
        retry_policy: The id of a named retry policy to apply to failed runs.
        environment: Which environment to enable/run the job in (default
            'production').
        description: Optional free-text description.
    """
    return _call(
        tools.create_job,
        name=name,
        url=url,
        method=method,
        headers=headers,
        body=body,
        timeout=timeout,
        schedule=schedule,
        run_at=run_at,
        timezone=timezone,
        retry_policy=retry_policy,
        environment=environment,
        description=description,
    )


@mcp.tool
def update_job(
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
    """Change a job. Only pass the fields you want to change.

    The tool reads the current job, applies your change, and saves the full
    updated job, so a partial change like "move it to 8am" works correctly.

    Args:
        job_id: The id of the job to change.
        name: New name.
        url: New destination URL (must be publicly reachable).
        method: New HTTP method.
        headers: Replace the request headers.
        body: Replace the request body.
        timeout: New per-run timeout in seconds.
        schedule: New cron schedule (makes the job recurring).
        run_at: New one-time run datetime (makes the job one-off).
        timezone: New IANA timezone for the cron schedule.
        retry_policy: New named retry-policy id.
        description: New description.
        enabled: Enable (true) or disable (false) the job in an environment.
        environment: Environment that `enabled` applies to (default 'production').
    """
    return _call(
        tools.update_job,
        job_id=job_id,
        name=name,
        url=url,
        method=method,
        headers=headers,
        body=body,
        timeout=timeout,
        schedule=schedule,
        run_at=run_at,
        timezone=timezone,
        retry_policy=retry_policy,
        description=description,
        enabled=enabled,
        environment=environment,
    )


@mcp.tool
def delete_job(job_id: str) -> dict[str, Any]:
    """Delete a job. Its run history is retained and the id may be reused later.

    Args:
        job_id: The id of the job to delete.
    """
    return _call(tools.delete_job, job_id=job_id)


@mcp.tool
def run_job(
    job_id: str,
    environment: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Fire one immediate run of a job and return the captured result.

    This is the way to prove a job works: it returns the run's status plus the
    captured HTTP response (status, headers, body) once it finishes. The job's
    schedule and enabled state are unchanged.

    Args:
        job_id: The id of the job to run.
        environment: Environment to run in. Optional when the job is enabled in
            exactly one environment; required if it's enabled in several.
        wait: Wait for the run to finish and return the captured response
            (default true).
    """
    return _call(tools.run_job, job_id=job_id, environment=environment, wait=wait)


@mcp.tool
def list_runs(
    job: str | None = None,
    status: str | None = None,
    failed_only: bool = False,
    trigger: str | None = None,
    environment: str | None = None,
    since: str | None = None,
    until: str | None = None,
    last_run_only: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """List runs by job, status, trigger, environment, and/or time window.

    The pull-based monitoring path: `failed_only=true` (or `status=FAILED`)
    answers "has anything failed?"; `job=<id>` shows one job's history.

    Args:
        job: Restrict to one job's run history (the job id).
        status: Restrict to a status, or comma-separated statuses: PENDING,
            RUNNING, SUCCEEDED, FAILED, CANCELED.
        failed_only: Shortcut for status=FAILED — answers "has anything failed?".
        trigger: Restrict to a trigger, or comma-separated: SCHEDULE, MANUAL,
            RERUN, RETRY.
        environment: Restrict to one or more environments (comma-separated).
        since: Only runs created at/after this ISO-8601 time.
        until: Only runs created before this ISO-8601 time.
        last_run_only: Collapse to the last completed run per job-and-environment.
        limit: Maximum number of runs to return (default 50).
    """
    return _call(
        tools.list_runs,
        job=job,
        status=status,
        failed_only=failed_only,
        trigger=trigger,
        environment=environment,
        since=since,
        until=until,
        last_run_only=last_run_only,
        limit=limit,
    )


@mcp.tool
def get_run(run_id: str) -> dict[str, Any]:
    """Get one run: status, timings, failure reason, and the captured HTTP response.

    This is where "what did it return?" lives — the run carries the captured
    `result` (status, headers, body, and whether the body was truncated).

    Args:
        run_id: The run's id (a UUID).
    """
    return _call(tools.get_run, run_id=run_id)


# --------------------------------------------------------------------------
# Health / liveness routes (served at app root, alongside the MCP endpoint)
# --------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> PlainTextResponse:
    """ALB target-group health check."""
    return PlainTextResponse("ok")


@mcp.custom_route("/api/liveness", methods=["GET"])
async def liveness(_request: Request) -> JSONResponse:
    """Version probe (also powers the branded landing page)."""
    return JSONResponse({"service": "mcp", "version": APP_VERSION})
