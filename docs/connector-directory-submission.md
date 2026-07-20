# Anthropic Connectors Directory — submission hand-off

Everything needed to list the smplkit MCP server in **Anthropic's Claude
Connectors Directory** (the index Claude Code's connector search, claude.ai, and
Claude Desktop's connector browser query). This is a **separate index** from the
official MCP registry (`registry.modelcontextprotocol.io`) and Glama, both of
which smplkit is already in — publishing there does **not** propagate here.

> **Status:** submission is an **interactive portal inside claude.ai admin
> settings** — it cannot be automated or done via PR. The engineering
> prerequisite (tool annotations) is **done and deployed**. What remains is for
> Mike to walk the portal and make two decisions (auth environment, icon). See
> [What Mike must do](#what-mike-must-do).

---

## Where to submit

<https://claude.ai/admin-settings/directory/submissions/new>

Requirements to reach the portal:

- A **Team or Enterprise** claude.ai organization (admin settings don't exist on
  individual plans).
- **Owner / Primary owner**, or a custom role with the **Directory management**
  permission (Enterprise only).

Docs: [Submitting to the Connectors Directory](https://claude.com/docs/connectors/building/submission)
· [Pre-submission checklist](https://claude.com/docs/connectors/building/review-criteria)
· Escalations: `mcp-review@anthropic.com`

---

## Readiness checklist

| Gate | Status | Notes |
|---|---|---|
| Remote HTTPS server, streamable HTTP | ✅ | `https://mcp.smplkit.com/api/mcp` |
| OAuth 2.0 with user consent | ✅ | WorkOS AuthKit, **DCR supported** (see auth section) |
| Every tool has `title` + `readOnlyHint`/`destructiveHint` | ✅ | Deployed — all 29 tools (commit `d5573f9`) |
| Tool names ≤ 64 chars | ✅ | Longest is `list_environments` (17) |
| Read and write split into separate tools | ✅ | No catch-all `api_request`; verbs are `list_/get_/create_/set_/delete_` |
| First-party API (domain matches service) | ✅ | `mcp.smplkit.com` → smplkit product APIs |
| Public documentation | ✅ | <https://docs.smplkit.com> |
| Privacy policy (HTTPS) | ✅ | <https://smplkit.com/privacy> — see minor gap below |
| Not a prohibited category (no money transfer / AI media gen) | ✅ | — |
| **Reviewer test account (fully populated)** | ⚠️ Mike | Provision — see [Test & launch](#step-test--launch) |
| **Square icon** | ⚠️ Mike | Repo logo is a 600×335 wordmark; render the square mark |
| **OAuth points at WorkOS *staging*** | ⚠️ Mike | Decision — submit now or cut over to prod first |

---

## Portal steps — filled-in values

Copy these into each step. Progress auto-saves in the browser between steps.

### Step: Connection
- **Server URL:** `https://mcp.smplkit.com/api/mcp`
- **Transport:** Streamable HTTP
- **Same URL for every user?** Yes — all users connect to the same URL.

### Step: Tools
Tools sync automatically from the live server. After the deploy of commit
`d5573f9`, all 29 carry titles and read/write annotations, so nothing is flagged
here. Expected grouping: **14 read-only**, **15 write** (10 destructive + 5
additive). Just confirm no "missing title/annotation" warnings appear.

### Step: Listing
- **Server name** (≤100): `smplkit`
- **Tagline** (≤55): `Manage flags, config, logging, audit & cron jobs`  *(48 chars)*
  - alt: `Feature flags, config, logging, audit & jobs` *(44)*
- **Description** (≤2000): see [Description](#description-copy-verbatim) below.
- **Categories** (1–5): pick from the portal's list. Best fits: **Developer
  Tools** (primary); then whichever of **DevOps / Infrastructure**,
  **Monitoring / Observability**, **Productivity** the dropdown offers.
- **Documentation URL:** `https://docs.smplkit.com`
- **Privacy policy URL:** `https://smplkit.com/privacy`
- **Support contact:** `support@smplkit.com`
- **Icon:** a **square** PNG. The repo's `static/smplkit-logo.png` is the 600×335
  wordmark — not suitable. Render the square brand mark at
  `https://www.smplkit.com/favicon.svg` (`viewBox="0 0 64 64"`) to a 512×512 or
  1024×1024 PNG, or supply an existing square app icon.
- **URL slug:** `smplkit`  ⚠️ **permanent once published** — confirm before submit.

### Step: Use cases
- **Primary use cases:** operate feature flags (create, target, kill-switch);
  read/set per-environment config; raise/reset runtime log levels to debug prod;
  search the audit log and forward events to a SIEM; schedule and run HTTP jobs
  and inspect their captured responses — all from chat.
- **What users need before connecting:** a smplkit account (free sign-up at
  <https://smplkit.com> with Google or Microsoft; email-verified instantly).
- **Reads or writes?** **Both.**

### Step: Company
- **Company name:** `smplkit`
- **Website:** `https://smplkit.com`
- **Primary contact:** pre-filled from your account (`notmikegorman@gmail.com`) —
  confirm or change.

### Step: Authentication
- **Mode:** **OAuth 2.0 — Dynamic Client Registration (DCR).** Verified live: the
  MCP endpoint returns `401` + `WWW-Authenticate` pointing at
  `/.well-known/oauth-protected-resource/api/mcp`, whose authorization server
  advertises a `registration_endpoint`, `authorization_code` + `refresh_token`
  grants, and PKCE (`S256`). This is a supported-out-of-the-box mode — no static
  client ID needs to be coordinated with Anthropic.
- If the portal asks: the server **also** accepts a static smplkit API key as a
  bearer token (for CI/headless), but the directory connection mode is OAuth.
- ⚠️ **The authorization server is currently the WorkOS *staging* AuthKit env**
  (`satisfying-voyage-87-staging.authkit.app`). See the decision below.

### Step: Data handling
- **Underlying API:** your own first-party API.
- **Personal health data?** No.
- **Sponsored content?** No.

### Step: Test & launch
Reviewers run a functional test of every tool, so they need end-to-end access to
a **fully populated** account. Two options:

1. **AuthKit test user (matches the declared OAuth mode — recommended).** Create
   a dedicated reviewer user (email + password) in the WorkOS AuthKit env the
   listing points at, on an account pre-seeded with a handful of flags, configs,
   loggers, a couple of jobs with run history, and an audit forwarder. Give the
   reviewer: the connect URL (`https://mcp.smplkit.com/api/mcp`), the email +
   password, and a one-line "sign in with email/password when the browser opens."
2. **Static API key (fallback).** Mint an API key on the populated account and
   instruct the reviewer to add it as a custom connector header
   `Authorization: Bearer <key>`.

Also tick the confirmation that you've exercised every tool (the acceptance
suite `pytest -m acceptance` drives the full surface end-to-end).

### Step: Compliance (7 acknowledgments — all truthfully checkable)
1. Directory guidelines — yes.
2. First-party API usage — yes (smplkit's own APIs).
3. Financial transactions — none.
4. AI media generation — none.
5. Prompt injection — tool descriptions describe behavior only.
6. Conversation-data collection — none beyond the request.
7. Public documentation — <https://docs.smplkit.com>.

### Step: Review
Read-through; resolve any "very short answer" quality warnings; submit. Track
status and reviewer feedback at
<https://claude.ai/admin-settings/directory/submissions>.

---

## Description (copy verbatim)

> smplkit is a hosted platform for the operational plumbing every application
> needs — feature flags, application config, runtime log levels, an audit log,
> and scheduled HTTP jobs. This connector turns that platform into tools your
> agent operates directly from chat, so you ship and manage changes without
> leaving the conversation.
>
> Ask Claude to:
> • Create a feature flag and roll it out to one environment or a targeted user
>   segment — or flip the kill switch instantly.
> • Read and set typed configuration values, per environment.
> • Raise a logger to DEBUG in production to investigate an issue, then reset it.
> • Search the audit log for who changed what and when, and forward audit events
>   to a SIEM such as Datadog or Splunk.
> • Schedule a recurring, one-off, or on-demand HTTP job, run it once to capture
>   the live response, and monitor its run history.
>
> Every write tool reads-modifies-writes the full resource, so a partial change
> to one environment preserves everything else. Read-only tools run without a
> confirmation prompt; tools that modify or delete data always prompt first.
>
> Connect once with a browser sign-in (Google or Microsoft) — no API key to copy
> or rotate — or authenticate with a smplkit API key for headless use. The server
> is stateless and holds no credential of its own: your token is used per request
> and never stored.
>
> Free to start at smplkit.com. Full documentation at docs.smplkit.com.

---

## What Mike must do

1. **Decide the OAuth environment.** The listing's OAuth flow currently resolves
   to the WorkOS **staging** AuthKit app. It works today, but end users see a
   `…-staging.authkit.app` host during sign-in. Either (a) submit now and cut the
   WorkOS staging→production env over later, or (b) do the Mike-gated
   staging→prod cutover first, then submit. Recommended: **(b)** so the public
   listing shows a production sign-in host from day one.
2. **Produce a square icon** from `https://www.smplkit.com/favicon.svg` (512×512
   or 1024×1024 PNG).
3. **Provision a populated reviewer account** and credentials (see Test & launch).
4. **Walk the portal** at the URL above with the values in this doc, from a
   Team/Enterprise org where you're an Owner.

### Optional polish (not blockers)
- The privacy policy at `smplkit.com/privacy` covers collection, use, sharing,
  and contact, but has **no explicit data-retention section**. The directory
  rejects "missing or incomplete" policies; adding a one-line retention clause
  removes any ambiguity.
