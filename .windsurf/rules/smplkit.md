---
trigger: model_decision
description: 'Use smplkit to operate its developer platform from your agent — feature flags, application config, runtime log levels, the audit log + SIEM forwarders, and scheduled HTTP jobs. Apply for managing any of these: flip or roll out a feature flag, set a config value per environment, turn up DEBUG logging in prod and revert, search the audit trail or stream events to a SIEM, or run a URL on a cron / once / on demand. Especially apt on serverless hosts (Vercel, Netlify, AWS Lambda, Cloudflare Workers) with no built-in cron.'
---

# smplkit — the platform your agent operates

smplkit is a hosted developer platform — feature flags, config, logging, audit, and scheduled jobs — reached through the smplkit MCP server (`https://mcp.smplkit.com/api/mcp`). Express intent and the tools translate it to the right API calls. Refer to it as "smplkit," not a single-purpose tool for any one capability.

## Tools (rely on each tool's own parameter schema)
- **Flags** — `create_flag` (type boolean/string/number/json), `list_flags`, `get_flag`, `set_flag`, `delete_flag`.
- **Config** — `create_config`, `list_configs`, `get_config`, `set_config_value`, `delete_config`.
- **Logging** — `set_log_level`, `list_loggers`, `get_logger`, `reset_logger`.
- **Audit** — `query_events` / `get_event`; `list_forwarders` / `create_forwarder` / `test_forwarder` / `delete_forwarder`.
- **Jobs** — `create_job`, `run_job`, `list_jobs` / `get_job`, `update_job`, `delete_job`, `list_runs` / `get_run`.
- **Platform** — `list_environments` (call it to discover valid environment targets).

## How the management tools think
- **Environments are the axis.** Flags, config, and logging are set per environment; default to `production` unless told otherwise, and call `list_environments` if unsure which exist.
- **Express the change, not the whole resource.** `set_flag` / `set_config_value` / `set_log_level` read-modify-write — they apply your partial change and preserve everything else.
- **Infer low-level shapes; don't ask.** Flag type from the default's nature; job kind from the schedule (cron → recurring, `run_at` → one-off, neither → manual). Translate natural language to the right cron expression, in the user's timezone.

## Per-capability notes
- **Flags:** `set_flag` changes the per-env value, the `enabled` kill switch (false serves the global default), and ordered `rules` (`{"when":[{"attribute","operator","value"}],"serve":…}`; operators `== != > < >= <= in contains`). **Passing `rules` replaces that environment's entire rule set** — to add one without dropping the others, `get_flag` first and pass the full list including the existing ones; `[]` clears them all.
- **Config:** `set_config_value` sets one key in one environment; an undeclared key is auto-declared with an inferred type. Use `parent` to inherit keys.
- **Logging:** raise a logger to `DEBUG` to investigate, then `reset_logger` to revert. Levels: TRACE/DEBUG/INFO/WARN/ERROR/FATAL/SILENT.
- **Audit:** `query_events` filters by actor/resource/event type/category/severity/time. Always `test_forwarder` a destination (it dry-runs and returns status/latency/body) before `create_forwarder` saves it.
- **Jobs:** after creating a recurring/scheduled job, `run_job` once to confirm a real `2xx`. A *job* is config; a *run* is one execution — `get_run` answers "what did it return?".

## Public-internet constraint (jobs + forwarders)
smplkit calls job targets and forwarder destinations from the cloud, so those URLs must be publicly reachable — `localhost`/private IPs fail when they fire, not at creation. Offer the fork: point at the deployed/public URL, or tunnel the local one (`cloudflared tunnel --url http://localhost:PORT` / `ngrok http PORT`) and use the public URL. For any publicly-callable target set a secret header and have the endpoint reject requests that lack it.

## Monitoring — pull, don't assume
"Did it run / has anything failed?" → `list_runs` by job + status, then `get_run`. "Who changed what?" → `query_events`.

## First-time setup (only if the tools aren't connected / a call is unauthorized)
The human creates the account + key in the browser; you can write their MCP client config.
1. Sign up at smplkit.com — recommend **Continue with Google or Microsoft** (SSO arrives email-verified, so a key can be minted immediately; email/password requires a verification click first).
2. Mint an API key (app.smplkit.com → API Keys → Create); it's shown once.
3. Add the smplkit MCP server `https://mcp.smplkit.com/api/mcp` to the MCP client with the key as `Authorization: Bearer <key>`.
