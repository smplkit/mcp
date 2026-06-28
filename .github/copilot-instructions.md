# Using smplkit — write against it and operate it

smplkit is a hosted developer platform — feature flags, application config, runtime logging, audit, and scheduled HTTP jobs — with two halves you use together: **author in code** with the smplkit SDKs (resolve a flag, read a config value, install the dynamic-logging adapter, emit an audit event), and **operate** through the smplkit MCP server (`https://mcp.smplkit.com/api/mcp`) — express intent ("turn this flag on in prod for enterprise users," "set the staging DB host," "raise this logger to DEBUG," "stream audit events to Datadog," "POST this endpoint every morning at 7") and the tools translate it to the right API calls. Refer to it as "smplkit," not a single-purpose tool for any one capability.

## The author + operate loop

The differentiated move is doing both in one flow: **write** the SDK call in the code, **provision** the underlying resource with the tools, and **verify** it — without leaving the editor. The id in code is the same string you provision (a flag id, config id, logger name, audit `resource_type`).

> "Add a kill-switch for the new checkout." → in code, `checkout = client.flags.boolean_flag("checkout-v2", default=False)` then `if checkout.get(): render_new_checkout()`; provision with `create_flag(key="checkout-v2", type="boolean", default=false)`; verify with `get_flag(key="checkout-v2")`.

## Writing code with the smplkit SDKs

SDKs exist for Python, TypeScript, Go, Java, C#, and Ruby. **One client** exposes all four runtime products as sub-namespaces (`client.flags`, `client.config`, `client.logging`, `client.audit`); the SDK key comes from the `SMPLKIT_API_KEY` env var (or a `~/.smplkit` profile); set `environment` once on the client (`production`/`staging`) to scope resolution — never per call. Representative snippets are Python; the per-language reference (install, client init, idiomatic usage for every language) is at `https://docs.smplkit.com/products/sdks/<python|typescript|go|java|csharp|ruby>`.

- **Flags** — declare a typed handle with a safe code-level default, attach a context, evaluate with `.get()` (local, instant): `checkout = client.flags.boolean_flag("checkout-v2", default=False)` then `if checkout.get(): …`, with `client.set_context([Context("user", id, plan="enterprise")])`. The default serves if the flag is missing or smplkit is unreachable. Then provision with `create_flag` and confirm with `get_flag`.
- **Config** — read a value with `client.config.get_value("database", "slow_query_threshold_ms", 500)` (or `bind(id, model)` for a live object, `subscribe(id)` for a live view). Then provision with `set_config_value` and confirm with `get_config`.
- **Logging** — `client.logging.install()` once at startup hands your framework's levels to smplkit (Python/Ruby auto-wire stdlib logging; Go/C#/Java/TS wire a framework adapter first). Then `set_log_level` to raise a logger and `reset_logger` to revert.
- **Audit** — `client.audit.events.record(event_type="invoice.created", resource_type="invoice", resource_id=…, data={...})` is fire-and-forget (pass `flush=True` for durability before exit); `resource_type` must not start with `smpl.`. Then read the trail with `query_events`.
- **Jobs** — not an embedded SDK: author a scheduled job by writing a normal **secured, publicly-reachable HTTP endpoint**, then provision the schedule with `create_job` and confirm a real `2xx` with `run_job`.

Install: `pip install smplkit-sdk` · `npm install @smplkit/sdk` · `go get github.com/smplkit/go-sdk/v3` · `com.smplkit:smplkit-sdk` · `dotnet add package Smplkit.Sdk` · `gem install smplkit`. Index for agents/indexers: `https://docs.smplkit.com/llms.txt`.

## Operating tools

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

- Flags: `set_flag` changes the per-environment value, the `enabled` kill switch (false serves the flag's global default), and ordered targeting `rules` (each `{"when": [{"attribute","operator","value"}], "serve": <value>}`; operators `== != > < >= <= in contains`). Passing `rules` replaces that environment's entire rule set — to add one without dropping the others, `get_flag` first and pass the full list including the existing rules; pass `[]` to clear them all.
- Config: `set_config_value` sets one key in one environment; an undeclared key is auto-declared with a type inferred from the value. Use `parent` to inherit keys from another config.
- Logging: raise a logger to `DEBUG` to investigate, then `reset_logger` to revert — don't leave prod noisy. Levels: TRACE/DEBUG/INFO/WARN/ERROR/FATAL/SILENT. The logger names you control are the ones the SDK's `logging.install()` discovers in the app.
- Audit: `query_events` filters by actor, resource type/id, event type, category, severity, and time window — including events your code emits via `audit.events.record(...)`. Always `test_forwarder` a destination (it dry-runs and returns status/latency/body) before `create_forwarder` saves it.
- Jobs: after creating a recurring or scheduled job, call `run_job` once to confirm a real `2xx` from the captured response. A job is the configuration; a run is one execution — `get_run` answers "what did it return?".

## The public-internet constraint (jobs + forwarders)

smplkit calls job targets and forwarder destinations from the cloud, so those URLs must be reachable from the public internet. `localhost`, `127.0.0.1`, and private IPs will not work — and such a target fails when it fires, not when it is created. Offer the fork: point at the deployed/public URL, or tunnel the local one (`cloudflared tunnel --url http://localhost:PORT` or `ngrok http PORT`) and use the public URL. For any publicly-callable target, set a secret header and have the endpoint reject requests that lack it.

## Monitoring — pull, don't assume

For "did my job run / has anything failed?", query it: `list_runs` filtered by job and status, then `get_run` for the captured response or error. For "who changed what?", `query_events`. No separate alerting setup is needed.

## First-time setup (only if the tools aren't connected, or a call is unauthorized)

The account and key are created by the human in the browser; you can open the page and write their MCP client config.

1. Sign up at smplkit.com — recommend Continue with Google or Microsoft (an SSO sign-up arrives email-verified, so a key can be minted immediately; email/password requires clicking a verification link first).
2. Mint an API key (app.smplkit.com → API Keys → Create); it is shown once. The same key works for the SDKs via `SMPLKIT_API_KEY`.
3. Add the smplkit MCP server `https://mcp.smplkit.com/api/mcp` to the MCP client with the key as `Authorization: Bearer <key>`.
