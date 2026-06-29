<!-- mcp-name: com.smplkit/mcp -->

# smplkit MCP Server

A hosted, agent-native [Model Context Protocol](https://modelcontextprotocol.io)
server — the gateway that lets an AI agent (Claude Code, Claude Desktop, Cursor,
…) **operate the whole [smplkit](https://smplkit.com) platform** on your behalf,
without you ever leaving the chat.

One server, the whole platform:

- **Flags** — feature flags with per-environment values and targeting.
- **Config** — keyed, typed config with per-environment overrides.
- **Logging** — change runtime log levels per environment.
- **Audit** — search the audit log and wire SIEM forwarders.
- **Jobs** — scheduled HTTP jobs (cron, one-off, or on-demand) with response capture.

Once connected, tell your agent *"turn on the new checkout for enterprise users in
prod,"* *"set the staging database host,"* *"raise the SQL logger to DEBUG in
prod,"* *"stream audit events to Datadog,"* or *"POST my endpoint every morning at
7."* It makes the change, proves it works, and answers follow-up questions
straight from the live state.

It is a thin, **stateless** HTTP client to the smplkit product APIs — it holds no
credential of its own.

## Connect once

### 1. Add the server and sign in

The server lives at **`https://mcp.smplkit.com/api/mcp`**. Point your MCP client
at that URL — the first time it connects, the client opens your browser for a
**one-time sign-in** (**Continue with Google or Microsoft**, standard OAuth).
After that it reconnects and refreshes access on its own; there's no key to mint,
copy, or rotate.

**Claude Code** (CLI):

```bash
claude mcp add --transport http smplkit https://mcp.smplkit.com/api/mcp
```

…or in `.mcp.json`:

```json
{
  "mcpServers": {
    "smplkit": {
      "type": "http",
      "url": "https://mcp.smplkit.com/api/mcp"
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json` or project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "smplkit": {
      "url": "https://mcp.smplkit.com/api/mcp"
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`) — Desktop bridges remote
servers through `mcp-remote`, which opens a browser for the one-time sign-in and
caches the connection:

```json
{
  "mcpServers": {
    "smplkit": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.smplkit.com/api/mcp"]
    }
  }
}
```

#### Prefer a static key?

For non-interactive use — CI, scripts, headless clients, or writing code with the
[smplkit SDKs](https://docs.smplkit.com/products/sdks/python) — skip the browser
and authenticate with an API key as a bearer token. Sign up at
**https://smplkit.com** (Google or Microsoft SSO, email-verified instantly),
create an **API key** in the console, and send it as
`Authorization: Bearer YOUR_SMPLKIT_API_KEY` (a custom `X-Smplkit-Api-Key` header
is also accepted). The SDKs read the same key from `SMPLKIT_API_KEY`. For example,
add a `headers` block to the config above:

```json
{
  "mcpServers": {
    "smplkit": {
      "type": "http",
      "url": "https://mcp.smplkit.com/api/mcp",
      "headers": { "Authorization": "Bearer ${SMPLKIT_API_KEY}" }
    }
  }
}
```

### 2. Ask your agent

> "Create a boolean flag `new-checkout`, off by default, then turn it on in prod
> only for enterprise users."

> "List my environments, then set `database.host` to `db-staging.internal` for
> staging."

> "Raise the `sqlalchemy.engine` logger to DEBUG in production while I debug,
> then reset it."

> "Test whether `https://http-intake.logs.datadoghq.com/...` accepts a sample,
> then create a Datadog forwarder for our audit events."

> "POST `https://api.example.com/cache/warm` every morning at 7am NY time, then
> run it now to prove it works."

## Tools

All tools share intent-named verbs — `list_*`, `get_*`, `create_*`, `set_*`,
`delete_*` — and hide the JSON:API envelopes, per-environment nesting, and
full-replace PUTs behind partial-intent calls.

| Capability | Tools |
|---|---|
| **Flags** | `create_flag`, `list_flags`, `get_flag`, `set_flag`, `delete_flag` |
| **Config** | `create_config`, `list_configs`, `get_config`, `set_config_value`, `delete_config` |
| **Logging** | `set_log_level`, `list_loggers`, `get_logger`, `reset_logger` |
| **Audit** | `query_events`, `get_event`, `list_forwarders`, `create_forwarder`, `test_forwarder`, `delete_forwarder` |
| **Jobs** | `create_job`, `list_jobs`, `get_job`, `update_job`, `delete_job`, `run_job`, `list_runs`, `get_run` |
| **Platform** | `list_environments` |

A few load-bearing behaviors:

- **`set_flag` / `set_config_value` / `set_log_level` are read-modify-write.** You
  express a partial change in one environment and the tool preserves the rest.
- **`list_environments`** tells you the valid environment targets (`production`,
  `staging`, …) for every `set_*` tool and for jobs.
- **Prove before you trust.** `run_job` fires a job once and returns the captured
  response; `test_forwarder` dry-runs a SIEM destination before you save it.
- **`create_job` infers the kind:** a cron `schedule` → recurring, a `run_at`
  datetime → one-off, neither → manual. You never set a kind.

The bundled [SKILL.md](SKILL.md) teaches an agent the whole surface.

## The public-internet constraint

smplkit calls **job targets** and **forwarder destinations** from the cloud, so
those URLs must be reachable from the public internet — `localhost`/private
addresses won't fire. To target a local server, point at its deployed URL or
expose it with a tunnel (`cloudflared tunnel --url http://localhost:PORT` or
`ngrok http PORT`) and set a secret auth header.

## Development

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
pytest                                          # unit tests (acceptance deselected)
ruff check src tests
```

Run the server locally:

```bash
PYTHONPATH=src uvicorn smplkit_mcp.app:app --host 0.0.0.0 --port 8000
# MCP endpoint:  http://localhost:8000/api/mcp
# Health check:  http://localhost:8000/health
```

Configuration (env vars) — each product's base host is independently
configurable, mirroring the SDK's `base_domain` pattern:

- `JOBS_BASE_DOMAIN` / `FLAGS_BASE_DOMAIN` / `CONFIG_BASE_DOMAIN` /
  `LOGGING_BASE_DOMAIN` / `AUDIT_BASE_DOMAIN` / `APP_BASE_DOMAIN` — the host for
  each product API (defaults `<product>.smplkit.com`; `APP_*` backs
  `list_environments`).
- `*_SCHEME` — `https` (default) or `http`.
- `*_BASE_URL` — full base-URL override (e.g. `http://localhost:8002`); wins over
  the two above. Used for the local platform and tests.

### Acceptance tests

`tests/test_acceptance.py` provisions an ephemeral verified account and drives
the full tool surface against the real product APIs end-to-end — create/set/get/
delete a flag, set and read a config value, set and list a log level, query
events, create/test/delete a forwarder, list environments, and the eight Jobs
tools — then cleans up. These tests require smplkit-internal **admin**
credentials, so they self-skip unless an admin key is available (`ADMIN_API_KEY`
env or the `[admin]` profile in `~/.smplkit`) — external contributors can ignore
them; the unit suite needs no credentials. Run them explicitly:

```bash
pytest -m acceptance
# point at a non-prod platform with <PRODUCT>_BASE_URL=...
```

## Architecture

- **Stateless** Python service built on [FastMCP](https://gofastmcp.com): it holds
  no database and no platform credential of its own.
- Auth is **per request** — whether the caller signs in with OAuth or sends an API
  key, the credential is validated and used per request to reach each product API,
  and is never cached or logged.
- One thin JSON:API HTTP client per product (flags, config, logging, audit,
  environments), each pointed at its own configurable base host.
- The MCP endpoint is served under `/api/mcp` with a stateless, JSON-response
  transport (no long-lived SSE), so it behaves correctly through proxies and load
  balancers with short idle timeouts.

## License

[MIT](LICENSE).
