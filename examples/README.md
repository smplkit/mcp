# Examples

**Schedule any HTTP endpoint from your AI agent — uniformly across all your services, with retries, run history, and on-demand runs — without per-platform cron config or lock-in.**

These are small, real projects that show the idiomatic flow: you deploy an ordinary HTTP endpoint, connect the **smplkit MCP server** once, and then your agent creates, runs, and monitors the schedule for you in plain language. The endpoint is just the workload — smplkit calls it on the schedule your agent sets up. **The endpoints do not use a smplkit SDK; smplkit calls them, they don't call smplkit.**

The same flow works for anything you can reach over HTTP — a Vercel function, a Python service, a Go binary, an existing internal API — so you schedule everything the same way instead of wiring up a different cron mechanism per platform.

| Example | Stack | Schedule | Shows |
|---|---|---|---|
| [daily-digest](./daily-digest/) | TypeScript (Vercel function) | recurring (cron) | the full create → run-to-prove → monitor flow + the shared-secret auth pattern |
| [nightly-cleanup](./nightly-cleanup/) | Python (Flask) | recurring (cron) | any language works (smplkit just calls a URL) + the failure/monitoring angle |

## Before you start

smplkit MCP Server is published to the official [MCP Registry](https://registry.modelcontextprotocol.io) as **`com.smplkit/mcp`** (also browsable at [github.com/mcp](https://github.com/mcp)).

1. **Connect the server once** — follow [Connect once](../README.md#connect-once) in the root README: sign up at [smplkit.com](https://smplkit.com), mint an API key, and add `https://mcp.smplkit.com/api/mcp` to your MCP client.
2. Then open an example and follow its README — each one gives you the exact prompt to hand your agent.

> These are reference examples. Deploy your *own* copy (Vercel/your host) and point a job at it — there's nothing to deploy from this repo.
