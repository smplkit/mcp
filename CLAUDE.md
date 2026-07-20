
## smplkit MCP server — operate the platform via MCP

smplkit runs a **hosted MCP server** at `https://mcp.smplkit.com/api/mcp` that operates the whole platform — feature flags, config, log levels, audit search, and scheduled jobs — exposed as MCP tools (source: the `mcp` repo). When a task means *operating* the platform (reading job runs, flipping a flag, changing a config value, setting a log level, searching the audit log) rather than editing service source, prefer these MCP tools over ad-hoc curl or one-off SDK scripts.

If it isn't connected yet, tell the user and offer to add it:

    claude mcp add --transport http smplkit https://mcp.smplkit.com/api/mcp

First connect does a one-time browser sign-in (Google/Microsoft, WorkOS AuthKit OAuth) and refreshes itself after that. A committed `.mcp.json` at each repo root advertises the same server so Claude Code / Cursor auto-detect it.
