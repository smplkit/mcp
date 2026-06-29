"""FastMCP server exposing the smplkit platform as agent tools (ADR-057).

The agent gateway to the whole platform: 29 tools across six capability groups —
Jobs (8), Flags (5), Config (5), Logging (4), Audit (6), and Platform
(list_environments). Each tool extracts the customer's smplkit API key from the
inbound request, builds a per-request product client (``JobsClient`` /
``FlagsClient`` / ``ConfigClient`` / ``LoggerClient`` / ``AuditClient`` /
``EnvironmentsClient``), and delegates to the plain functions in that product's
module (:mod:`smplkit_mcp.tools`, :mod:`~smplkit_mcp.flags`, etc.). The server is
stateless: it holds no platform credential, never caches a key across requests,
and never logs the key.

Tool parameter descriptions live in each function's Google-style ``Args``
docstring; FastMCP surfaces them in the tool input schema the model reads.
"""
from __future__ import annotations

import os
import warnings
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import audit, configs, environments, flags, loggers, oauth, tools
from .audit import AuditClient
from .config import load_settings
from .configs import ConfigClient
from .environments import EnvironmentsClient
from .errors import (
    JobsApiError,
    MissingApiKeyError,
    SmplkitApiError,
    friendly_message,
)
from .flags import FlagsClient
from .jobs_client import JobsClient
from .loggers import LoggerClient
from .urls import NonPublicTargetError

APP_VERSION = os.environ.get("APP_VERSION", "dev")
SETTINGS = load_settings()

# OAuth resource-server provider (ADR-058). None unless an authorization server is
# configured via MCP_OAUTH_* env, in which case FastMCP serves Protected Resource
# Metadata, the 401/WWW-Authenticate challenge, and token validation. Disabled by
# default, so the live server keeps the API-key pass-through unchanged.
AUTH_PROVIDER = oauth.build_auth_provider(oauth.SETTINGS)

# ``set_flag.value`` uses a non-None sentinel default (flags.UNSET) so the model
# can set a flag's per-environment default to JSON ``null`` (distinct from "leave
# unchanged"). Pydantic warns that the sentinel isn't JSON-serializable when
# FastMCP builds the tool schema; the default is correctly excluded, so silence
# that one cosmetic warning.
warnings.filterwarnings(
    "ignore",
    message="Default value .* is not JSON serializable; excluding default from JSON schema",
)

INSTRUCTIONS = (
    "The agent gateway to the smplkit platform — it exposes smplkit's capabilities "
    "as tools you can call on the user's behalf, so you can manage feature flags, "
    "config, logging, audit, and scheduled jobs without leaving the chat. "
    "Capabilities: "
    "Flags — create_flag/list_flags/get_flag/set_flag/delete_flag manage feature "
    "flags with per-environment values and targeting (set_flag changes value, the "
    "kill switch, and rules in one environment). "
    "Config — create_config/list_configs/get_config/set_config_value/delete_config "
    "manage keyed config collections and per-environment values. "
    "Logging — set_log_level/list_loggers/get_logger/reset_logger control runtime "
    "log levels per environment. "
    "Audit — query_events/get_event search the audit log; "
    "list_forwarders/create_forwarder/test_forwarder/delete_forwarder wire SIEM "
    "forwarders (test_forwarder dry-runs a destination before you save it). "
    "Jobs — create_job schedules an HTTP request (a cron `schedule` makes it "
    "recurring, a `run_at` datetime one-off, neither manual), run_job fires one now "
    "and returns the captured response, list_runs/get_run monitor it. "
    "Use list_environments to discover the valid environment targets for every "
    "set_* tool and for jobs. Most set_* tools read-modify-write the full resource, "
    "so you express a partial change and the tool preserves the rest. Job and "
    "forwarder target URLs must be reachable from the public internet. "
    "When you are WRITING application code (not just operating the platform), the "
    "smplkit SDKs — Python, TypeScript, Go, Java, C#, Ruby — resolve flags, read "
    "config, install the dynamic-logging adapter, and emit audit events in-process; "
    "author the SDK call, then provision and verify the resource with these tools. "
    "SDK docs: https://docs.smplkit.com/products/sdks/<language> "
    "(index: https://docs.smplkit.com/llms.txt)."
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


def _resolve_credential() -> str:
    """Resolve the per-request credential to forward to the product APIs.

    Extracts the inbound bearer (surfacing absence as a ToolError). In OAuth mode
    a validated WorkOS token is exchanged for a short-lived app session JWT — the
    credential the product APIs accept — while a smplkit API key forwards
    unchanged (ADR-058 §2.4).
    """
    # include_all=True because FastMCP 3.x strips Authorization from
    # get_http_headers() by default.
    headers = get_http_headers(include_all=True)
    try:
        token = extract_api_key(headers)
    except MissingApiKeyError as exc:
        raise ToolError(str(exc)) from exc
    if oauth.SETTINGS.enabled and not oauth.looks_like_api_key(token):
        try:
            token = oauth.exchange_for_app_token(token)
        except oauth.TokenExchangeError as exc:
            raise ToolError(str(exc)) from exc
    return token


def _client() -> JobsClient:
    return JobsClient(_resolve_credential(), SETTINGS.jobs_base_url)


def _call(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """Run a tool function with a per-request client, mapping errors to ToolError."""
    client = _client()
    try:
        return fn(client, **kwargs)
    except (NonPublicTargetError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except JobsApiError as exc:
        raise ToolError(friendly_message(exc)) from exc


def _api_key() -> str:
    """Per-request credential the product clients forward (see _resolve_credential)."""
    return _resolve_credential()


def _run(client: Any, fn: Callable[..., Any], **kwargs: Any) -> Any:
    """Run a product tool function, mapping product/validation errors to ToolError."""
    try:
        return fn(client, **kwargs)
    except (NonPublicTargetError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except SmplkitApiError as exc:
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
        retry_policy: The id of an existing named retry policy to apply to failed
            runs. Retry policies are managed in the smplkit console — create one
            there, then pass its id.
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
        retry_policy: New named retry-policy id; manage policies in the console.
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
# Flags
# --------------------------------------------------------------------------


@mcp.tool
def create_flag(
    key: str,
    type: str,
    default: Any,
    name: str | None = None,
    description: str | None = None,
    values: list[Any] | None = None,
) -> dict[str, Any]:
    """Create a feature flag with an explicit type, key, and default value.

    After creating, use set_flag to set per-environment values and targeting.

    Args:
        key: Unique key for the flag (its identifier, e.g. 'dark-mode').
        type: Value type — 'boolean', 'string', 'number', or 'json'.
        default: Default value served when no environment rule matches. Must match
            the type (and be one of `values` if you constrain the flag).
        name: Human-readable name (defaults to the key).
        description: Optional free-text description.
        values: Optional allowed-value set to constrain the flag — a list of
            scalars (e.g. ['classic','modern']) or of {name, value} objects.
    """
    return _run(
        FlagsClient(_api_key(), SETTINGS.flags_base_url),
        flags.create_flag,
        key=key, type=type, default=default, name=name,
        description=description, values=values,
    )


@mcp.tool
def list_flags(
    type: str | None = None,
    search: str | None = None,
    managed: bool | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List feature flags with their per-environment state.

    Args:
        type: Filter by type — 'boolean', 'string', 'number', or 'json'.
        search: Case-insensitive substring match on key and name.
        managed: True for API/console-managed flags only, False for
            SDK-auto-discovered only.
        limit: Maximum number of flags to return.
    """
    return _run(
        FlagsClient(_api_key(), SETTINGS.flags_base_url),
        flags.list_flags,
        type=type, search=search, managed=managed, limit=limit,
    )


@mcp.tool
def get_flag(key: str) -> dict[str, Any]:
    """Get one flag's full config: values plus targeting per environment.

    Args:
        key: The flag's key.
    """
    return _run(FlagsClient(_api_key(), SETTINGS.flags_base_url), flags.get_flag, key=key)


@mcp.tool
def set_flag(
    key: str,
    environment: str = flags.DEFAULT_ENVIRONMENT,
    value: Any = flags.UNSET,
    enabled: bool | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Set a flag's value, kill switch, and targeting in one environment.

    Reads the flag, applies your change, and saves the full flag, so other
    environments are preserved. Pass only what you want to change.

    Args:
        key: The flag's key.
        environment: Environment to change (default 'production'). Use
            list_environments to see valid targets.
        value: The value served in this environment when no rule matches (the
            per-environment default). Omit to leave it unchanged; pass null to
            clear it so the environment falls back to the flag's global default.
        enabled: The kill switch. False skips all rules and serves the flag's
            global default; True re-enables targeting.
        rules: **This replaces the environment's entire rule set.** To add a rule
            without dropping the others, call get_flag first and pass the full
            list including the existing ones. Each rule is
            {"when": [{"attribute","operator","value"}, ...], "serve": <value>,
            "description"?: str}; conditions are AND-ed. Operators: ==, !=, >, <,
            >=, <=, in, contains. Pass [] to clear all rules.
    """
    return _run(
        FlagsClient(_api_key(), SETTINGS.flags_base_url),
        flags.set_flag,
        key=key, environment=environment, value=value, enabled=enabled, rules=rules,
    )


@mcp.tool
def delete_flag(key: str) -> dict[str, Any]:
    """Delete a flag.

    Args:
        key: The flag's key.
    """
    return _run(FlagsClient(_api_key(), SETTINGS.flags_base_url), flags.delete_flag, key=key)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@mcp.tool
def create_config(
    name: str,
    config_id: str | None = None,
    description: str | None = None,
    parent: str | None = None,
    items: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a named config — a keyed collection of typed values.

    Args:
        name: Human-readable name for the config.
        config_id: The config's key/identifier (defaults to a slug of the name).
        description: Optional free-text description.
        parent: Key of another config to inherit items from.
        items: Optional initial keys, as {key: value} (type inferred) or
            {key: {"value", "type", "description"}}. Types: STRING, NUMBER,
            BOOLEAN, JSON.
    """
    return _run(
        ConfigClient(_api_key(), SETTINGS.config_base_url),
        configs.create_config,
        name=name, config_id=config_id, description=description,
        parent=parent, items=items,
    )


@mcp.tool
def list_configs(
    parent: str | None = None,
    search: str | None = None,
    managed: bool | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List configs with their keys and per-environment values.

    Args:
        parent: Filter to configs inheriting from this parent key.
        search: Case-insensitive substring match on key and name.
        managed: True for managed configs only, False for SDK-discovered only.
        limit: Maximum number of configs to return.
    """
    return _run(
        ConfigClient(_api_key(), SETTINGS.config_base_url),
        configs.list_configs,
        parent=parent, search=search, managed=managed, limit=limit,
    )


@mcp.tool
def get_config(config_id: str) -> dict[str, Any]:
    """Get one config's full state: items plus per-environment overrides.

    Args:
        config_id: The config's key.
    """
    return _run(ConfigClient(_api_key(), SETTINGS.config_base_url), configs.get_config,
                config_id=config_id)


@mcp.tool
def set_config_value(
    config_id: str,
    key: str,
    value: Any,
    environment: str = configs.DEFAULT_ENVIRONMENT,
) -> dict[str, Any]:
    """Set one config key's value in one environment.

    Reads the config, sets the override, and saves the full config, so other keys
    and environments are preserved. The key is auto-declared (type inferred) if it
    isn't already defined.

    Args:
        config_id: The config's key.
        key: The item key within the config (e.g. 'database.host').
        value: The value to set for this key in this environment.
        environment: Environment to set the value in (default 'production'). Use
            list_environments to see valid targets.
    """
    return _run(
        ConfigClient(_api_key(), SETTINGS.config_base_url),
        configs.set_config_value,
        config_id=config_id, key=key, value=value, environment=environment,
    )


@mcp.tool
def delete_config(config_id: str) -> dict[str, Any]:
    """Delete a config.

    Args:
        config_id: The config's key.
    """
    return _run(ConfigClient(_api_key(), SETTINGS.config_base_url), configs.delete_config,
                config_id=config_id)


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


@mcp.tool
def set_log_level(
    logger_id: str,
    level: str,
    environment: str = loggers.DEFAULT_ENVIRONMENT,
) -> dict[str, Any]:
    """Set a logger's level in one environment (creates the logger if needed).

    Use this to dial up verbosity (e.g. DEBUG) to investigate an issue, then
    reset_logger to revert. Other environments are preserved.

    Args:
        logger_id: The logger's dot-separated key (e.g. 'sqlalchemy.engine').
        level: One of TRACE, DEBUG, INFO, WARN, ERROR, FATAL, SILENT.
        environment: Environment to set the level in (default 'production'). Use
            list_environments to see valid targets.
    """
    return _run(
        LoggerClient(_api_key(), SETTINGS.logging_base_url),
        loggers.set_log_level,
        logger_id=logger_id, level=level, environment=environment,
    )


@mcp.tool
def list_loggers(
    managed: bool | None = None,
    service: str | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List managed loggers and their account-wide and per-environment levels.

    Args:
        managed: True for managed loggers only, False for SDK-observed only.
        service: Restrict to loggers observed from this service.
        search: Case-insensitive substring match on key and name.
        limit: Maximum number of loggers to return.
    """
    return _run(
        LoggerClient(_api_key(), SETTINGS.logging_base_url),
        loggers.list_loggers,
        managed=managed, service=service, search=search, limit=limit,
    )


@mcp.tool
def get_logger(logger_id: str) -> dict[str, Any]:
    """Get one logger's config: account-wide level plus per-environment levels.

    Args:
        logger_id: The logger's dot-separated key.
    """
    return _run(LoggerClient(_api_key(), SETTINGS.logging_base_url), loggers.get_logger,
                logger_id=logger_id)


@mcp.tool
def reset_logger(logger_id: str) -> dict[str, Any]:
    """Stop managing a logger — delete its config so it reverts to the default.

    Args:
        logger_id: The logger's dot-separated key.
    """
    return _run(LoggerClient(_api_key(), SETTINGS.logging_base_url), loggers.reset_logger,
                logger_id=logger_id)


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


@mcp.tool
def query_events(
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
    limit: int = 50,
) -> dict[str, Any]:
    """Search the audit log by actor, resource, category, severity, and/or time.

    Args:
        actor_type: Kind of actor (e.g. 'USER', 'API_KEY', 'SYSTEM').
        actor_id: Identifier of the actor.
        event_type: Exact event type (e.g. 'user.created').
        resource_type: Exact resource kind (e.g. 'user').
        resource_id: Exact resource id (requires resource_type).
        category: Exact category label (e.g. 'auth', 'billing').
        severity: One of TRACE, DEBUG, INFO, WARN, ERROR, FATAL.
        environment: Restrict to an environment (comma-separated for several).
        since: Only events at/after this ISO-8601 time.
        until: Only events before this ISO-8601 time.
        search: Case-insensitive substring on resource_id and description.
        limit: Maximum number of events to return (default 50).
    """
    return _run(
        AuditClient(_api_key(), SETTINGS.audit_base_url),
        audit.query_events,
        actor_type=actor_type, actor_id=actor_id, event_type=event_type,
        resource_type=resource_type, resource_id=resource_id, category=category,
        severity=severity, environment=environment, since=since, until=until,
        search=search, limit=limit,
    )


@mcp.tool
def get_event(event_id: str) -> dict[str, Any]:
    """Get one audit event's full detail.

    Args:
        event_id: The event's id.
    """
    return _run(AuditClient(_api_key(), SETTINGS.audit_base_url), audit.get_event,
                event_id=event_id)


@mcp.tool
def list_forwarders(
    forwarder_type: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List SIEM forwarders and their per-environment enablement.

    Args:
        forwarder_type: Filter by destination type (datadog, elastic, honeycomb,
            http, new_relic, splunk_hec, sumo_logic).
        limit: Maximum number of forwarders to return.
    """
    return _run(
        AuditClient(_api_key(), SETTINGS.audit_base_url),
        audit.list_forwarders,
        forwarder_type=forwarder_type, limit=limit,
    )


@mcp.tool
def create_forwarder(
    name: str,
    url: str,
    forwarder_type: str = "http",
    forwarder_id: str | None = None,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    success_status: str | None = None,
    filter: dict[str, Any] | None = None,
    environment: str = audit.DEFAULT_ENVIRONMENT,
    enabled: bool = True,
    description: str | None = None,
    forward_smplkit_events: bool | None = None,
) -> dict[str, Any]:
    """Wire a forwarder that delivers audit events to a SIEM/HTTP destination.

    Prove the destination first with test_forwarder. The URL must be reachable
    from the public internet.

    Args:
        name: Human-readable name for the forwarder.
        url: Destination URL to deliver events to (publicly reachable).
        forwarder_type: Destination type — datadog, elastic, honeycomb, http,
            new_relic, splunk_hec, sumo_logic (default 'http').
        forwarder_id: The forwarder's key (defaults to a slug of the name).
        method: HTTP method used to deliver (default POST).
        headers: HTTP headers to send, as a name->value object (e.g. auth).
        success_status: Status that counts as success — a code ('200') or class
            ('2xx', default).
        filter: Optional JSON Logic expression; only matching events are delivered.
        environment: Environment to enable the forwarder in (default 'production').
        enabled: Whether the forwarder is enabled in that environment (default true).
        description: Optional free-text description.
        forward_smplkit_events: Also forward smplkit's own platform change events.
    """
    return _run(
        AuditClient(_api_key(), SETTINGS.audit_base_url),
        audit.create_forwarder,
        name=name, url=url, forwarder_type=forwarder_type, forwarder_id=forwarder_id,
        method=method, headers=headers, success_status=success_status, filter=filter,
        environment=environment, enabled=enabled, description=description,
        forward_smplkit_events=forward_smplkit_events,
    )


@mcp.tool
def test_forwarder(
    url: str,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    success_status: str | None = None,
    body: str | None = None,
    timeout_ms: int | None = None,
    tls_verify: bool | None = None,
    ca_cert: str | None = None,
) -> dict[str, Any]:
    """Dry-run a forwarder destination before saving it.

    Sends one sample request to the destination and returns whether it succeeded,
    the response status/headers/body, and the latency — so you can prove a SIEM
    endpoint works before wiring create_forwarder.

    Args:
        url: Destination URL to test (publicly reachable).
        method: HTTP method (default POST).
        headers: HTTP headers to send, as a name->value object.
        success_status: Status that counts as success — a code or class (default '2xx').
        body: Optional request body sent verbatim.
        timeout_ms: Per-request timeout in milliseconds (max 30000).
        tls_verify: Whether to verify the destination's TLS certificate (default true).
        ca_cert: Optional PEM CA certificate to verify a self-signed destination.
    """
    return _run(
        AuditClient(_api_key(), SETTINGS.audit_base_url),
        audit.test_forwarder,
        url=url, method=method, headers=headers, success_status=success_status,
        body=body, timeout_ms=timeout_ms, tls_verify=tls_verify, ca_cert=ca_cert,
    )


@mcp.tool
def delete_forwarder(forwarder_id: str) -> dict[str, Any]:
    """Delete a forwarder. Past forwarded events are unaffected.

    Args:
        forwarder_id: The forwarder's key.
    """
    return _run(AuditClient(_api_key(), SETTINGS.audit_base_url), audit.delete_forwarder,
                forwarder_id=forwarder_id)


# --------------------------------------------------------------------------
# Platform
# --------------------------------------------------------------------------


@mcp.tool
def list_environments(
    classification: str | None = None,
    managed: bool | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List the account's environments — the valid targets for every set_* tool.

    Args:
        classification: Filter by 'STANDARD' (deliberately created) or 'AD_HOC'
            (auto-discovered from SDK traffic).
        managed: True for managed environments only (writable targets).
        search: Case-insensitive substring match on key and name.
        limit: Maximum number of environments to return.
    """
    return _run(
        EnvironmentsClient(_api_key(), SETTINGS.app_base_url),
        environments.list_environments,
        classification=classification, managed=managed, search=search, limit=limit,
    )


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
