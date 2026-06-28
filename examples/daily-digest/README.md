# Example: daily digest (recurring)

A minimal **Vercel function** that sends a daily digest email, scheduled by your
AI agent through the [smplkit MCP server](../../README.md). The endpoint is an
ordinary HTTP handler — it doesn't import any smplkit SDK; smplkit just calls it
on the schedule your agent sets up, with retries, and records every run.

The one thing worth copying: it **verifies a shared secret header
(`X-Job-Secret`) and returns 401 without it**. Because the URL is public,
that's what keeps anyone but smplkit from triggering it.

- [`api/digest.ts`](./api/digest.ts) — the endpoint (route: `POST /api/digest`). The real work is stubbed (assemble a summary → send an email) so it runs as-is.

## The flow

### 1. Deploy the endpoint

```bash
cp .env.example .env.local   # set JOB_SECRET to a long random string
npm install
npx vercel deploy --prod     # → https://your-app.vercel.app
```

Set the same `JOB_SECRET` in your Vercel project's environment variables. Run it
locally first if you like: `npm run dev` (serves `http://localhost:3000/api/digest`), then:

```bash
curl -i -X POST localhost:3000/api/digest                              # → 401 unauthorized
curl -i -X POST localhost:3000/api/digest -H "X-Job-Secret: <secret>"  # → 200 {"ok":true,...}
```

### 2. Connect smplkit (once)

Add the smplkit MCP server to your agent — see [Connect once](../../README.md#connect-once). You only do this once, for all your jobs.

### 3. Tell your agent to schedule it

> **Create a job that POSTs `https://your-app.vercel.app/api/digest` every morning at 8am New York time, with header `X-Job-Secret: <your-secret>`. Then run it once to prove it works.**

What the agent does, via the MCP server:

1. **`create_job`** — a recurring job (it infers *recurring* from the cron schedule `0 8 * * *`, in `America/New_York`), with your secret header attached.
2. **`run_job`** — fires one run immediately and shows you the real **`200`** and the **captured response body** (`{"ok":true,"subject":"…","recipients":…}`). This catches a wrong URL, a missing header, or a 500 *now* — not at 8am tomorrow.

### 4. Monitor it — just ask

> **Has the daily digest failed this week?**

The agent calls **`list_runs`** filtered to this job and failed status; **`get_run`**
shows the captured response or error for any single run. No alerting to set up —
the run history is the monitor.

You can also change it in plain language ("move the digest to 7am") or fire it
on demand any time ("run the digest now").

## How this works

Your agent provisions the job through the smplkit MCP server; smplkit calls your
endpoint on schedule (with retries) and records every run. See the
[root README](../../README.md) and [smplkit.com](https://smplkit.com).
