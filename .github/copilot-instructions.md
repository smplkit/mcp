# Using smplkit — the platform your agent operates

smplkit is a hosted developer platform — feature flags, application config, runtime logging, audit, and scheduled HTTP jobs — reached through the smplkit MCP server (`https://mcp.smplkit.com/api/mcp`). Express intent ("turn this flag on in prod for enterprise users," "set the staging DB host," "raise this logger to DEBUG," "stream audit events to Datadog," "POST this endpoint every morning at 7") and the tools translate it to the right API calls. Refer to it as "smplkit," not a single-purpose tool for any one capability.

## Tools

Rely on each tool's own parameter schema.

- **Flags** — `create_flag` (type boolean/string/number/json), `list_flags`, `get_flag`, `set_flag`, `delete_flag`.
- **Config** — `create_config`, `list_configs`, `get_config`, `set_config_value`, `delete_config`.
- **Logging** — `set_log_level`, `list_loggers`, `get_logger`, `reset_logger`.
- **Audit** — `query_events` / `get_event`; `list_forwarders` / `create_forwarder` / `test_forwarder` / `delete_forwarder`.
- **Jobs** — `create_job`, `run_job`, `list_jobs` / `get_job`, `update_job`, `delete_job`, `list_runs` / `get_run`.
- **Platform** — `list_environments` (call it to discover the valid environment targets).

## How the management tools think

- Environments are the axis. Flags, config, and logging are set per environment; default to `production` unless told otherwise, and call `list_environments` if unsure which environments exist.
- Express the change, not the whole resource. `set_flag` / `set_config_value` / `set_log_level` read-modify-write — they apply your partial change and preserve everything else. Never assemble a full replacement yourself.
- Infer low-level shapes rather than asking: a flag's type from the default value's nature; a job's kind from the schedule (a cron `schedule` → recurring, a `run_at` datetime → one-off, neither → manual). Translate natural language to the right cron expression, in the user's timezone.

## Per-capability notes

- Flags: `set_flag` changes the per-environment value, the `enabled` kill switch (false serves the flag's global default), and ordered targeting `rules` (each `{"when": [{"attribute","operator","value"}], "serve": <value>}`; operators `== != > < >= <= in contains`; pass `[]` to clear rules).
- Config: `set_config_value` sets one key in one environment; an undeclared key is auto-declared with a type inferred from the value. Use `parent` to inherit keys from another config.
- Logging: raise a logger to `DEBUG` to investigate, then `reset_logger` to revert — don't leave prod noisy. Levels: TRACE/DEBUG/INFO/WARN/ERROR/FATAL/SILENT.
- Audit: `query_events` filters by actor, resource type/id, event type, category, severity, and time window. Always `test_forwarder` a destination (it dry-runs and returns status/latency/body) before `create_forwarder` saves it.
- Jobs: after creating a recurring or scheduled job, call `run_job` once to confirm a real `2xx` from the captured response. A job is the configuration; a run is one execution — `get_run` answers "what did it return?".

## The public-internet constraint (jobs + forwarders)

smplkit calls job targets and forwarder destinations from the cloud, so those URLs must be reachable from the public internet. `localhost`, `127.0.0.1`, and private IPs will not work — and such a target fails when it fires, not when it is created. Offer the fork: point at the deployed/public URL, or tunnel the local one (`cloudflared tunnel --url http://localhost:PORT` or `ngrok http PORT`) and use the public URL. For any publicly-callable target, set a secret header and have the endpoint reject requests that lack it.

## Monitoring — pull, don't assume

For "did my job run / has anything failed?", query it: `list_runs` filtered by job and status, then `get_run` for the captured response or error. For "who changed what?", `query_events`. No separate alerting setup is needed.

## First-time setup (only if the tools aren't connected, or a call is unauthorized)

The account and key are created by the human in the browser; you can open the page and write their MCP client config.

1. Sign up at smplkit.com — recommend Continue with Google or Microsoft (an SSO sign-up arrives email-verified, so a key can be minted immediately; email/password requires clicking a verification link first).
2. Mint an API key (app.smplkit.com → API Keys → Create); it is shown once.
3. Add the smplkit MCP server `https://mcp.smplkit.com/api/mcp` to the MCP client with the key as `Authorization: Bearer <key>`.
