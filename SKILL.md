---
name: smplkit-jobs
description: Create, run, and monitor scheduled HTTP jobs on smplkit through the mcp.smplkit.com MCP server. Use when the user wants to schedule a recurring or one-off HTTP request (a webhook, cron, ping, cleanup, digest, cache warm, health check), "run X every morning/hour/day", call an endpoint on a schedule, trigger a job on demand and see what it returned, or check whether scheduled jobs have run or failed. Tools: list_jobs, get_job, create_job, update_job, delete_job, run_job, list_runs, get_run.
---

# smplkit Jobs

smplkit Jobs runs scheduled HTTP requests in the cloud — recurring (cron),
one-off (a single future time), or manual (on-demand). Each time a job fires,
smplkit records a **run** capturing the request, the response (status, headers,
body), timing, and outcome. This skill drives those jobs through the eight tools
exposed by the `mcp.smplkit.com` MCP server.

## Connect once

The server is hosted at `mcp.smplkit.com`. The user adds it to their MCP client
with their smplkit API key as a header (see the project README for copy-paste
config). To mint a key: sign up at https://smplkit.com — **prefer Google or
Microsoft SSO**, because an SSO sign-up is email-verified instantly and can
create an API key right away (email/password sign-ups must verify their email
first). Then create an API key in the console and put it in the MCP config.

If a tool returns "Connect your smplkit API key", the key header is missing or
invalid — point the user to the setup above.

## The core flow: create → run → monitor

1. **Create** the job with `create_job`. You describe intent; the kind is
   inferred — never pass a "kind":
   - a cron `schedule` (e.g. `0 7 * * *`) → **recurring**
   - a `run_at` datetime (ISO-8601, or `now`) → **one-off**
   - neither → **manual** (fires only via `run_job`)
2. **Prove it** with `run_job` — fires one immediate run and returns the
   captured response (a real `200` with the body). This is the moment that shows
   the user it works.
3. **Monitor** with `list_runs` / `get_run`. `list_runs(failed_only=true)` (or
   `status=FAILED`) answers "has anything failed?"; `get_run` shows the captured
   response for a specific run.

`update_job` changes a job (pass only the fields to change). `delete_job`
removes it. `list_jobs` shows everything with each job's latest run status.

## The public-URL constraint (important)

smplkit calls the target URL **from the cloud**, so it must be reachable from
the public internet. A `localhost` or private-IP target can never fire, and
`create_job` will refuse it with guidance. When the user wants to schedule a job
against a **local** server, take the fork:

- **Preferred:** point the job at the app's already-deployed/public URL.
- **Tunnel:** expose the local server with a tunnel and use the public URL it
  prints, for example:
  - `cloudflared tunnel --url http://localhost:PORT`
  - `ngrok http PORT`

  Run the tunnel command for the user (it prints a public `https://…` URL), then
  create the job against that URL.

**Secure the endpoint.** Because the URL is now public, set a secret auth header
on the job so only smplkit can call it — pass `headers` to `create_job`, e.g.
`{"Authorization": "Bearer <a-secret-you-generate>"}`, and have the target
verify it. This keeps a tunnelled or public endpoint from answering anyone else.

## Examples

- "POST to https://api.acme.com/cache/warm every morning at 7."
  → `create_job(name="Cache warm", url="https://api.acme.com/cache/warm",
  method="POST", schedule="0 7 * * *", timezone="America/New_York")`, then
  `run_job(job_id=...)` to prove it (show the captured 200), then mention they
  can ask "has anything failed?" anytime.

- "Run my cleanup endpoint once tonight at 2am."
  → `create_job(..., run_at="2026-06-28T02:00:00Z")` (a one-off).

- "Did my nightly digest job fail this week?"
  → `list_runs(job="nightly-digest", failed_only=true,
  since="2026-06-21T00:00:00Z")`, then `get_run` on any failure to show the
  captured error/response.

- "Schedule a ping to my local app at http://localhost:3000/health every 5 min."
  → It's local — explain the constraint, start a tunnel
  (`cloudflared tunnel --url http://localhost:3000`), then
  `create_job(url="https://<tunnel-host>/health", method="GET",
  schedule="*/5 * * * *", headers={"Authorization": "Bearer <secret>"})`.

## Reference

- `create_job(name, url, method?, headers?, body?, timeout?, schedule?, run_at?,
  timezone?, retry_policy?, environment?, description?)`
- `update_job(job_id, …same fields…, enabled?, environment?)` — partial change,
  full-replace under the hood
- `run_job(job_id, environment?, wait?)` — returns the captured run
- `list_runs(job?, status?, failed_only?, trigger?, environment?, since?, until?,
  last_run_only?, limit?)`
- `get_run(run_id)` / `get_job(job_id)` / `list_jobs(name?, kind?, limit?)` /
  `delete_job(job_id)`
