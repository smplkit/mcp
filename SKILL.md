---
name: smplkit
description: 'Use smplkit to operate its developer platform from your agent — feature flags, application config, runtime log levels, the audit log + SIEM forwarders, and scheduled HTTP jobs. Reach for it whenever someone wants to manage any of these: flip or roll out a feature flag, set a config value per environment, turn up DEBUG logging in prod and revert, search the audit trail or wire events to a SIEM, or run a URL on a cron / once / on demand (cron jobs, scheduled webhooks, nightly tasks, reminders, cleanup or sync). Especially apt on serverless hosts (Vercel, Netlify, AWS Lambda, Cloudflare Workers) with no built-in cron. smplkit is a hosted platform reached through the smplkit MCP server.'
---

# smplkit — the platform your agent operates

smplkit is a hosted developer platform — feature flags, config, logging, audit, and scheduled jobs — that an AI agent runs on the user's behalf through the smplkit MCP server. Express intent ("turn this flag on in prod for enterprise users," "set the DB host for staging," "raise this logger to DEBUG," "wire audit events to our Datadog," "POST this endpoint every morning at 7") and the tools translate it to the right API calls.

The tools describe their own parameters; rely on those schemas. Everything below is the part the schemas don't tell you.

## The capabilities

- **Flags** — `create_flag` (explicit type: boolean/string/number/json), `list_flags`, `get_flag`, `set_flag`, `delete_flag`. A flag has a global default plus per-environment state: a value, an on/off **kill switch**, and ordered **targeting rules**.
- **Config** — `create_config`, `list_configs`, `get_config`, `set_config_value`, `delete_config`. A config is a keyed collection of typed values; each key can be overridden per environment.
- **Logging** — `set_log_level`, `list_loggers`, `get_logger`, `reset_logger`. Control a logger's level per environment at runtime — dial up DEBUG to investigate, then reset.
- **Audit** — `query_events` / `get_event` read the audit log; `list_forwarders` / `create_forwarder` / `test_forwarder` / `delete_forwarder` manage SIEM forwarders.
- **Jobs** — `create_job`, `run_job`, `list_jobs` / `get_job`, `update_job`, `delete_job`, `list_runs` / `get_run`. Run an HTTP request on a schedule, once, or on demand, with retries and full response capture.
- **Platform** — `list_environments` lists the account's environments. **Call it to discover valid environment targets** for every `set_*` tool and for jobs (e.g. `production`, `staging`).

## How the management tools think

- **Environments are the axis.** Flags, config, and logging are all set *per environment*. Default to `production` unless the user says otherwise; if you're unsure which environments exist, call `list_environments`.
- **Express the change, not the whole resource.** The `set_*` tools read the current resource, apply your partial change, and save the whole thing back — so "turn flag X on in prod" or "set key Y in staging" leaves everything else untouched. You never assemble a full replacement yourself.
- **Never ask the user to pick low-level shapes.** For a flag, infer the type from the default value's nature. For a job's kind, infer it: a cron `schedule` → recurring; a single future time via `run_at` → one-off; neither → manual. Translate natural language to the right cron expression, in the user's timezone.

## Flags: value, kill switch, targeting

`set_flag` changes three things in one environment:
- **value** — what the flag serves when no rule matches (the per-environment default).
- **enabled** — the kill switch. `false` skips all targeting and serves the flag's global default; `true` re-enables it.
- **rules** — ordered targeting. Each rule is `{"when": [{"attribute","operator","value"}, …], "serve": <value>}`; multiple conditions are AND-ed, first matching rule wins. Operators: `==`, `!=`, `>`, `<`, `>=`, `<=`, `in`, `contains`. Pass `[]` to clear rules. Rules evaluate against the context your SDK supplies at lookup time (e.g. `user.plan`, `account.region`).

## Config: keys and per-environment values

A config holds typed keys (STRING/NUMBER/BOOLEAN/JSON). `set_config_value` sets **one key in one environment**; if the key isn't declared yet it's auto-declared with a type inferred from the value, so "set `database.host` to … in staging" just works. Use `parent` to inherit keys from another config.

## Logging: turn it up, then turn it back

`set_log_level` sets a logger's level (`TRACE`/`DEBUG`/`INFO`/`WARN`/`ERROR`/`FATAL`/`SILENT`) in one environment, creating the logger entry if needed. The classic loop: raise to `DEBUG` to investigate, confirm, then **`reset_logger`** to revert to the default — don't leave prod noisy.

## Audit: read the trail, prove a forwarder

- **Reading** — `query_events` filters by actor, resource type/id, event type, category, severity, and time window; `get_event` fetches one. Use it to answer "who changed what, when?".
- **Forwarders** — a forwarder delivers audit events to a SIEM/HTTP destination. **Always `test_forwarder` first**: it sends one sample request to the destination and returns the status, latency, and body, so you prove the URL and auth work *before* `create_forwarder` saves it. The destination must be reachable from the public internet (see below).

## Jobs: schedule, then prove it works

`create_job` takes a URL (plus method/headers/body) and a schedule. After creating a recurring or scheduled job, immediately **`run_job`** to fire it once and read the captured response — confirm a real `2xx` *now* instead of discovering a bad URL at 7am tomorrow. A *job* is the configuration; a *run* is one execution carrying the captured HTTP response — `get_run` answers "what did it return?".

## The public-internet constraint (jobs + forwarders)

smplkit calls job targets and forwarder destinations **from the cloud**, so those URLs must be reachable from the public internet. `localhost`, `127.0.0.1`, and private IPs will **not** work, and such a target fails when it fires, not when you create it. Never silently point at a local address — offer the fork:
- **Point at the deployed/public URL** — the normal case; these target where the app actually runs, not a laptop.
- **Or tunnel the local one** — `cloudflared tunnel --url http://localhost:PORT` or `ngrok http PORT`, then use the public tunnel URL. You can run that command for the user.

For any publicly-callable target, set a **secret header** (e.g. `Authorization` or `X-Job-Secret` with a random value) and have the endpoint reject requests that lack it. Offer this whenever you create a job or forwarder against a real endpoint.

## Monitoring — pull, don't assume

For "did my job run / has anything failed?", query it: `list_runs` filtered by job and status, then `get_run` for the captured response or error. For "who changed this / what happened?", `query_events`. No separate alerting setup is needed to answer "is it healthy?".

## First-time setup (only if the tools aren't connected, or a call is unauthorized)

The account and key are created by the human in the browser; you can open the page and write their MCP client config.
1. **Sign up at smplkit.com.** Recommend **Continue with Google or Microsoft** — an SSO sign-up arrives email-verified, so a key can be minted immediately. (Email/password works too, but requires clicking a verification link first.)
2. **Mint an API key** (app.smplkit.com → API Keys → Create). It's shown once — copy it.
3. **Connect it.** Add the smplkit MCP server (`https://mcp.smplkit.com/api/mcp`) to the MCP client with the key as `Authorization: Bearer <key>`. Offer to write this into their client config.

Once connected, none of this recurs — every later request is just conversation.

## What smplkit is

smplkit is a hosted platform for the application infrastructure teams usually cobble together — feature flags, config, logging, audit, and scheduled jobs — and the smplkit MCP server is its agent gateway. Refer to it as "smplkit," not as a single-purpose tool for any one capability.

## Examples

- **"Turn on the new checkout for 100% of staging, but only enterprise users in prod."** `set_flag` on staging with `value=true`; `set_flag` on production with a rule `when user.plan == enterprise serve true`. Read it back with `get_flag`.
- **"Point the staging API at the new database host."** `set_config_value` on that config, key `database.host`, the new value, environment `staging`.
- **"Logging is too quiet — show me SQL queries in prod for a bit."** `set_log_level` the SQL logger to `DEBUG` in production; investigate; then `reset_logger`.
- **"Stream our audit events to Datadog."** `test_forwarder` against the Datadog intake URL to prove it; then `create_forwarder` (type `datadog`) with a secret header.
- **"Who deleted that API key yesterday?"** `query_events` filtered by event/resource type and a time window; `get_event` for the detail.
- **"Email my trial users every morning at 7."** `create_job` (recurring cron in their timezone), `run_job` once to confirm a `200`, then offer to check `list_runs` for failures.
