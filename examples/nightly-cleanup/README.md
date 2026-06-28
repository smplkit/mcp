# Example: nightly cleanup (recurring)

A minimal **Python (Flask)** endpoint that purges expired/stale records,
scheduled by your AI agent through the [smplkit MCP server](../../README.md).

This example is in Python on purpose: **any language or stack works, because
smplkit just calls a URL.** Your endpoint doesn't import a smplkit SDK — smplkit
calls *it*, on the schedule your agent sets up, with retries, and records every
run. Whether it's a Vercel function, this Flask app, a Go binary, or an existing
internal API, you schedule them all the same way.

Like the other example, it **verifies a shared secret header (`X-Job-Secret`)
and returns 401 without it**. The work is **idempotent** — safe to re-run any
time, which matters because you (or your agent) can fire it on demand with
`run_job`.

- [`app.py`](./app.py) — the endpoint (route: `POST /cleanup`). The DB delete is stubbed so it runs as-is.

## The flow

### 1. Deploy the endpoint

```bash
cp .env.example .env          # set JOB_SECRET to a long random string
pip install -r requirements.txt
JOB_SECRET=<secret> python app.py    # serves http://localhost:8000/cleanup
```

Deploy it wherever you run Python (your host, a container, a PaaS), with
`JOB_SECRET` set in the environment. Verify the auth pattern locally:

```bash
curl -i -X POST localhost:8000/cleanup                              # → 401 unauthorized
curl -i -X POST localhost:8000/cleanup -H "X-Job-Secret: <secret>"  # → 200 {"ok":true,"deleted":0}
```

### 2. Connect smplkit (once)

Add the smplkit MCP server to your agent — see [Connect once](../../README.md#connect-once).

### 3. Tell your agent to schedule it

> **Create a job that POSTs `https://your-app.com/cleanup` every night at 2am, with header `X-Job-Secret: <your-secret>`. Then run it once to prove it works.**

The agent calls **`create_job`** (recurring, from the cron `0 2 * * *`) with your
secret header, then **`run_job`** to fire it immediately and show you the real
`200` and captured body — so you confirm it works before the first scheduled run.

### 4. Monitor failures — just ask

> **Has the nightly cleanup failed in the last week?**

The agent calls **`list_runs`** filtered to this job and **failed** status. For
any failure, **`get_run`** returns the **captured error and response** (status,
body) for that execution — so you see *why* it failed, not just *that* it did.
Because the job is idempotent, once you've fixed the cause you can re-run it on
the spot ("run the cleanup now") without waiting for tomorrow.

## How this works

Your agent provisions the job through the smplkit MCP server; smplkit calls your
endpoint on schedule (with retries) and records every run. See the
[root README](../../README.md) and [smplkit.com](https://smplkit.com).
