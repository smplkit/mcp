# smplkit MCP Server

A hosted, agent-native [Model Context Protocol](https://modelcontextprotocol.io)
server — the gateway that lets an AI agent (Claude Code, Claude Desktop, Cursor,
…) operate the [smplkit](https://smplkit.com) platform on your behalf, without
you ever leaving the chat. Implements ADR-057.

**Available now: Smpl Jobs** — create, run, and monitor scheduled HTTP jobs. More
of the smplkit platform will be exposed here over time.

The magic moment: connect once, then say *"POST my endpoint every morning at
7."* The agent creates a recurring job, fires one run on the spot to prove it (a
real `200` with the captured response body), and can answer *"has anything
failed?"* from run history.

It is a thin, **stateless** HTTP client to the smplkit Jobs API. Your smplkit
API key is forwarded per request and never stored.

## Connect once

### 1. Get a smplkit API key

Sign up at **https://smplkit.com**. **Use Google or Microsoft (SSO)** — an SSO
sign-up is email-verified instantly, so you can mint a key right away. Then
create an **API key** in the console.

### 2. Add the server to your MCP client

The server lives at **`https://mcp.smplkit.com/api/mcp`**. Authenticate with your
key via the `Authorization: Bearer <key>` header (a custom `X-Smplkit-Api-Key`
header is also accepted).

**Claude Code** (CLI):

```bash
claude mcp add --transport http smplkit-jobs https://mcp.smplkit.com/api/mcp \
  --header "Authorization: Bearer YOUR_SMPLKIT_API_KEY"
```

…or in `.mcp.json`:

```json
{
  "mcpServers": {
    "smplkit-jobs": {
      "type": "http",
      "url": "https://mcp.smplkit.com/api/mcp",
      "headers": { "Authorization": "Bearer ${SMPLKIT_API_KEY}" }
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json` or project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "smplkit-jobs": {
      "url": "https://mcp.smplkit.com/api/mcp",
      "headers": { "Authorization": "Bearer ${env:SMPLKIT_API_KEY}" }
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`) — Desktop bridges remote
servers through `mcp-remote`. Note the header value has **no space after the
colon** in `args` (a known mcp-remote quirk); the spaced value goes in `env`:

```json
{
  "mcpServers": {
    "smplkit-jobs": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "https://mcp.smplkit.com/api/mcp",
        "--header", "Authorization:${AUTH_HEADER}"
      ],
      "env": { "AUTH_HEADER": "Bearer YOUR_SMPLKIT_API_KEY" }
    }
  }
}
```

### 3. Ask your agent

> "Create a job that POSTs to https://api.example.com/cache/warm every morning
> at 7am New York time, then run it now to prove it works."

The agent calls `create_job` (recurring) then `run_job`, and shows you the real
`200` and captured body. Follow up with:

> "List my jobs." · "Has anything failed in the last day?" · "Move the cache
> warm to 8am." · "Run the digest job now and show me what it returned."

## Tools

| Tool | What it does |
|---|---|
| `list_jobs` | List jobs, each with its latest run status |
| `get_job` | One job's configuration |
| `create_job` | Create a job (kind inferred from the schedule) |
| `update_job` | Change a job (partial change, full-replace under the hood) |
| `delete_job` | Remove a job (run history is retained) |
| `run_job` | Fire one run now and return the captured response |
| `list_runs` | Runs by job / status / time window (the "did it fail?" path) |
| `get_run` | One run: status, timing, and the captured HTTP response |

`create_job` infers the kind: a cron `schedule` → **recurring**, a `run_at`
datetime → **one-off**, neither → **manual**. You never set a kind.

## The public-URL constraint

smplkit calls your target URL from the cloud, so it must be reachable from the
public internet — `localhost`/private addresses won't fire. To schedule against
a local server, point at its deployed URL or expose it with a tunnel
(`cloudflared tunnel --url http://localhost:PORT` or `ngrok http PORT`) and set a
secret auth header on the job. The bundled [SKILL.md](SKILL.md) teaches an agent
this flow.

## Development

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt          # add: --index-url https://pypi.org/simple/
pytest                                          # unit tests (acceptance deselected)
ruff check src tests
```

Run the server locally:

```bash
PYTHONPATH=src uvicorn smplkit_mcp.app:app --host 0.0.0.0 --port 8000
# MCP endpoint:  http://localhost:8000/api/mcp
# Health check:  http://localhost:8000/health
```

Configuration (env vars):

- `JOBS_BASE_DOMAIN` — Jobs API host (default `jobs.smplkit.com`).
- `JOBS_SCHEME` — `https` (default) or `http`.
- `JOBS_BASE_URL` — full base-URL override (e.g. `http://localhost:8005`); wins
  over the two above. Used for the local platform and tests.

### Acceptance tests

`tests/test_acceptance.py` provisions an ephemeral verified account, drives all
eight tools against a real Jobs service (the ADR-057 magic moment), and cleans
up. It self-skips unless a smplkit **admin** key is available (`ADMIN_API_KEY`
env or the `[admin]` profile in `~/.smplkit`). Run it explicitly:

```bash
pytest -m acceptance
# point at a non-prod Jobs service with JOBS_BASE_URL=...
```

## Architecture

- **Stateless** Python + [FastMCP](https://gofastmcp.com) service on Fargate
  behind the ALB, deployed via Pulumi (`infra/`) reusing `ProductServiceStack`
  from `smplkit-infra` — no worker, no database.
- The MCP endpoint is served under `/api/mcp` so it routes through the standard
  CloudFront → ALB pattern; the transport is stateless with JSON responses (no
  long-lived SSE) so it behaves through proxies with short idle timeouts.
- Auth is **per request**: the customer's API key arrives as a header, is
  forwarded as the `Bearer` token to the Jobs API, and is never cached or logged.
- CI builds the image (tagged by commit SHA) and `pulumi up`s on push to `main`.
