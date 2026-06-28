# ADR-058: MCP One-Tap OAuth Onboarding (Jobs v2 auth)

| Field      | Value                                                                                          |
|------------|------------------------------------------------------------------------------------------------|
| Status     | **Proposed** — resource-server scaffolding implemented; **§2.3 authz-server decided 2026-06-28: WorkOS AuthKit**; credential bridge (§2.4) + wiring pending |
| Date       | 2026-06-28                                                                                      |
| Author     | Mike (draft prepared by Claude Code)                                                            |
| Applies To | smplkit/mcp (resource server, this repo); smplkit/app (authorization server + credential bridge) |

> **Location note.** ADRs canonically live in `app/docs/adrs/`. This draft was authored in the `smplkit/mcp` worktree alongside the scaffolding it describes, and is **Proposed** precisely because §2.3 needs Mike's sign-off before anything with vendor cost is adopted. Copy to `app/docs/adrs/ADR-058-…` on acceptance.

## 1. Context

ADR-057 §2.4 shipped v1 auth — the customer's own API key, forwarded per request — and explicitly deferred **v2: one-tap OAuth onboarding** to "a future, separate ADR." This is that ADR.

**The wall today.** A developer whose agent reaches for smplkit must: open the console, sign up, mint an API key, copy it, and paste it into their MCP client config. SSO sign-up arrives `email_verified=True` (ADR-036) so the gate is one step, but the manual key-mint-and-paste is friction in front of exactly the "connect once" moment the wedge depends on.

**The v2 magic moment.** The MCP client (Claude, Cursor, …) gets a `401`, opens a browser, the human does **one** SSO sign-in, and the client walks away holding a working credential — no console visit, no copy-paste. Account ownership and the ADR-036 anti-abuse posture are preserved because a real human SSO sign-in still happens once.

**What the MCP authorization spec actually requires (verified against the current stable revision, 2025-11-25).** The spec splits two roles that our stack currently fuses:

- The **MCP server is an OAuth 2.1 _resource server_**: it validates access tokens and advertises *where its tokens come from* via Protected Resource Metadata (RFC 9728). It does **not** mint tokens. Hard requirements (stable across the 2025-06-18 and 2025-11-25 revisions): serve RFC 9728 PRM whose document names ≥1 `authorization_servers`; return `401` with `WWW-Authenticate: Bearer …, resource_metadata="…"`; validate that every token's audience is *this* server (RFC 8707) and never pass a client token through to an upstream API.
- A separate **authorization server (AS)** runs `/authorize` + `/token`, advertises RFC 8414 metadata, handles client registration, and drives the human sign-in. **PKCE (S256)** and the **`resource` indicator** (RFC 8707) are *client* obligations, not ours.

**Our stack is not an AS today.** The app is an OIDC *consumer*: Google + Microsoft social login plus per-account custom OIDC/SAML (ADR-054), and it mints its own **HS256** JWTs. It has **no** `/authorize`, `/token`, `/.well-known/oauth-authorization-server`, JWKS, Dynamic Client Registration, or PKCE of its own (`app/backend/src/app/api/routes/auth.py`, `core/auth.py`). API keys (`sk_api_*`) are minted at `POST /api/v1/api-keys`, gated on `email_verified` (ADR-036). So **standing up an OAuth 2.1 AS with client registration is net-new capability** — and, as the task flagged, the genuinely hard part.

## 2. Decision

### 2.1 The MCP server becomes an OAuth 2.1 resource server — built now, vendor-independent

The resource-server half of the spec is **identical for every build-vs-buy outcome in §2.3**, so it ships now, behind a feature flag, with no vendor commitment and no new dependency. FastMCP 3.x (already our dependency) provides the machinery; we configure it.

Implemented in this repo (`src/smplkit_mcp/oauth.py`, wired in `server.py`):

- **Protected Resource Metadata** (RFC 9728) at `/.well-known/oauth-protected-resource/api/mcp`, advertising `resource = https://mcp.smplkit.com/api/mcp`, the configured `authorization_servers`, and `scopes_supported`. Built by FastMCP's `RemoteAuthProvider`.
- **The 401 challenge.** An unauthenticated MCP request returns `401` with `WWW-Authenticate: Bearer …, resource_metadata="https://mcp.smplkit.com/.well-known/oauth-protected-resource/api/mcp"` — the trigger that starts client discovery.
- **The token-validation hook.** Bearer JWTs are validated locally (signature via JWKS, issuer, **audience bound to this resource** per RFC 8707, expiry) by FastMCP's `JWTVerifier`.
- **Discovery wiring** mounted at the app root by `mcp.http_app(...)`.

Two design choices worth recording:

- **Disabled by default.** With no `MCP_OAUTH_*` env configured, the provider is `None` and the live server behaves exactly as it does today (the ADR-057 §2.4 API-key pass-through). The OAuth role turns on only once an AS is configured — which cannot happen until §2.3 is decided. This is why the scaffolding is safe to ship before the decision.
- **The API-key path keeps working alongside OAuth.** A `MultiAuth` composition validates OAuth JWTs first, then falls back to accepting a smplkit API key (`sk_*`) via a pass-through verifier. The MCP server has never validated API keys itself — the product API is the authority (ADR-057 §2.4) — so the pass-through accepts a well-formed key and forwards it downstream unchanged. **Scopes are advertised, not gated** (a hard scope requirement would 403 the API-key path, which carries no OAuth scopes); per-scope authorization is deferred to the credential model in §2.4.

### 2.2 Client-registration reality: CIMD is the future, DCR is still needed for reach today

The hardest part of "buy or build an AS" is **client registration**, and the spec moved under our feet — so this section pins down what we actually have to support.

- **DCR was downgraded.** Dynamic Client Registration (RFC 7591) was `SHOULD` in 2025-06-18 and is now only `MAY` — explicitly "deprecated and retained for backwards compatibility" — in the current 2025-11-25 spec. Self-operating an *open, unauthenticated* `/register` endpoint is a real liability (unbounded client growth, anonymous registration abuse, redirect-URI laxity).
- **CIMD is the new `SHOULD`.** Client ID Metadata Documents (`draft-ietf-oauth-client-id-metadata-document`, at -01 as of March 2026; the MCP spec references -00) make the `client_id` an HTTPS URL the AS fetches — no registration round-trip, no per-connection client records. It is the preferred path going forward.
- **But client support lags the spec (June 2026 reality):** **Claude Code** ships CIMD (changelog v2.1.81). **Cursor and VS Code still drive DCR** — a CIMD-only AS locks Cursor out today. Therefore, to onboard from *all* major clients, the AS must offer **both CIMD and DCR**. This is the single biggest reason to prefer a managed AS that already implements both correctly over hand-rolling either.

A related interop hazard for whichever AS we pick: redirect-URI policy. Claude.ai web uses `https://claude.ai/api/mcp/auth_callback`; **Claude Code uses RFC 8252 loopback on ephemeral ports** (needs port-agnostic matching); **Cursor desktop uses a custom scheme** `cursor://…/oauth/callback` (neither HTTPS nor loopback — a strict AS rejects it). A managed AS absorbs most of this; a hand-rolled one inherits all of it.

### 2.3 Build-vs-buy for the authorization server — **the decision for Mike**

There are three shapes, not two. FastMCP's `OAuthProxy`/`OIDCProxy` adds a genuine "build" option by presenting DCR+PKCE+consent to MCP clients while delegating to a *fixed* upstream OAuth app.

| Shape | What we operate | Our build cost | Recurring $ | Notes |
|---|---|---|---|---|
| **A. Buy turnkey AS** | resource-server only (done in §2.1) | low | vendor pricing | Vendor fronts our SSO and owns DCR+CIMD+PKCE+consent. |
| **B. Build (app-as-AS + FastMCP OAuthProxy)** | a new OAuth2 AS on the app + the proxy | **high** | $0 | We add `/authorize`+`/token`+consent to the app; FastMCP shims DCR/PKCE to clients. Security-critical, ours forever. |
| **C. Self-host (Keycloak / Ory Hydra)** | a self-run AS | medium | infra only | No SaaS fee, but we operate a security-critical service; Keycloak lacks RFC 8707 (audience-mapper workaround). |

**Vendor comparison (primary-source verified, June 2026):**

| Vendor | DCR | CIMD | Fronts our Google/MS SSO w/o migration | Free tier | Cost at our scale | Lock-in / notes |
|---|---|---|---|---|---|---|
| **WorkOS AuthKit** | yes (off by default) | yes (off by default) | **yes — "Standalone Connect", no user migration** | **1,000,000 MAU** | **$0/mo** | +$2,500 per additional 1M MAU; social free; **first-class FastMCP integration**; standard JWTs (portable). |
| **Stytch** (Twilio-owned since Nov 2025) | yes (open, off by default) | partial / TAT | yes — "Connected Apps" via Trusted Auth Token | 10,000 MAU | $0/mo now | Beyond-free MAU pricing **unpublished**; FastMCP `BearerAuthProvider`; acquisition-era uncertainty. |
| **Auth0** (Okta) | yes (disabled by default; **prod-safe hardening = Enterprise**) | yes (recommends CIMD for prod) | yes (Token Vault, RFC 8693 OBO) | 25,000 MAU | $0 now, but **prod DCR ⇒ Enterprise (contact-sales)** | Most lock-in (proprietary Actions/Rules). B2C Essentials $35/mo. |
| **AWS Cognito** | **no native DCR** (needs Lambda/API-GW shim) | no | yes (social + SAML/OIDC) | 10,000 MAU | $0 + **build a discovery/DCR proxy** | AWS-native, but **omits `code_challenge_methods_supported`** ⇒ spec-strict clients refuse; two metadata blockers negate the "less to operate" win. API-GW JWT authorizer is fine for RS validation. |
| **Descope** | yes (configurable) | yes | yes | 7,500 MAU | $0 now; Pro $249/mo (10k MAU) | "Agentic Identity Hub" markets MCP; PKCE/RFC 8707 unconfirmed in primary docs. |
| **Keycloak / Ory** (self-host) | yes | no (Keycloak) | yes | n/a | infra only | We run a security-critical AS; Keycloak: RFC 8707 "Not supported." |

**Build (Shape B) effort, for calibration.** The resource-server side is cheap and done. A from-scratch AS is not: Authlib's authorization-server integration is **Flask/Django-only** (no async FastAPI AS), so we'd implement the MCP Python SDK's `OAuthAuthorizationServerProvider` (10 methods) + consent UI + auth-code storage + a move from HS256 to asymmetric signing/JWKS + DCR *and* CIMD endpoints (incl. SSRF-safe CIMD fetch). Rough order of magnitude **4–8 engineering weeks**, plus the permanent burden of operating a security-critical auth endpoint. FastMCP's `OAuthProxy` removes the *client-facing* DCR/PKCE/consent work but still requires the app to expose a standard OAuth2 authorization-code endpoint it does not have today.

**Recommendation: Shape A — buy WorkOS AuthKit in "Standalone Connect" mode.**

- **Cost: $0/month at current and foreseeable scale** (1M MAU free; we are pre-revenue and orders of magnitude below that). The only future cost is $2,500 per additional 1M MAU — a problem we would love to have.
- **Preserves ADR-036.** Standalone Connect fronts our *existing* Google/Microsoft SSO with **no user migration** — the human still does exactly one real SSO sign-in, and AuthKit issues a WorkOS-signed JWT (audience bound to our MCP resource) rather than a raw Google token.
- **Broadest client reach.** Supports both CIMD (Claude Code) and DCR (Cursor/VS Code), plus PKCE and RFC 8707, correctly — the §2.2 problem we do not want to own.
- **Minimal build on our side.** FastMCP ships a native AuthKit integration; the §2.1 scaffolding (a `RemoteAuthProvider` pointed at an issuer) is already the right shape.
- **Trade-off to weigh:** a third-party vendor sits in the critical auth path, and there is lock-in (mitigated by standard JWTs, the no-migration standalone mode, and the resource-server code being portable). Stytch is the viable runner-up (same fronting model, but a 10k-MAU free tier, unpublished overage pricing, and fresh Twilio ownership). Auth0 and Cognito are not recommended on cost/operational grounds above.

**Why this still needs your sign-off even at $0:** WorkOS becomes a dependency in the login path (availability, data residency, lock-in) and carries a real price above 1M MAU. Per our "stop before adopting any vendor / anything with cost" rule, I am **proposing, not adopting**.

> **Decision — 2026-06-28: accepted, WorkOS AuthKit (Standalone Connect).** Next actions: (1) Mike provisions a WorkOS account and configures the dashboard — Standalone Connect pointed at our existing SSO, enable CIMD + DCR, set the MCP server's resource URL as a Resource Indicator (this is the external dependency only Mike can do). (2) Wire the resource server: set `MCP_OAUTH_AUTHORIZATION_SERVERS`/`MCP_OAUTH_JWKS_URI` at the AuthKit issuer (the `oauth.py` scaffolding already accepts these; FastMCP's `AuthKitProvider` is an optional convenience). (3) Design the §2.4 credential bridge.

### 2.4 The credential bridge — new architecture, coupled to §2.3

After the resource server validates the AS token, it must still obtain a credential the **product APIs** (Jobs) accept — and Jobs accepts smplkit API keys today, not WorkOS JWTs. Closing that gap is the genuinely new architecture, and its shape depends on the §2.3 decision. Options:

- **(a) Map-and-mint in the MCP server.** On a valid AS token, map the subject/email → smplkit account and look up or mint an API key to forward. Requires the MCP server to hold a platform credential — breaking ADR-057's "stateless, no platform credential" posture — or a new internal provisioning endpoint on the app.
- **(b) Product APIs accept the AS token directly.** Jobs (and siblings) become resource servers too. Cleanest long-term, but a multi-service change and **an API contract change → requires separate approval** under the org API rules.
- **(c) Exchange at sign-in.** The in-flow callback resolves the account and mints an API credential bound to it, handing the client a credential the existing path already accepts. Most compatible with today's design.

This pass deliberately does **not** build the bridge: a validated OAuth token reaching the tool layer raises a clear "exchange not yet implemented" error rather than silently forwarding a token Jobs would reject. Choosing among (a)/(b)/(c) is the first follow-up after §2.3, and (b) in particular would need its own API-change review.

## 3. Consequences

**Ships in this pass (committed, CI-green, no vendor):**
- Resource-server scaffolding (`oauth.py`, `server.py` wiring, 32 tests): PRM endpoint, 401/`WWW-Authenticate` challenge, JWT validation hook, discovery wiring — all gated behind `MCP_OAUTH_*`, **off in production**.
- Zero behavior change to the live API-key path; no new dependency (FastMCP's auth deps were already present); coverage maintained (98%+).

**Blocked on the §2.3 decision (not built):**
- Selecting/configuring the authorization server, and turning the resource-server flag on.
- The §2.4 credential bridge (and, if option (b), a separate API-change review).
- Skill/README/`server.json`/`llms.txt` updates teaching the OAuth connect flow (kept untouched while OAuth is dark, to avoid advertising a path that 401s).

**Risks/notes:**
- The spec is young and moving (a 2026-07-28 release candidate exists). Targeting 2025-11-25 and leaning on FastMCP's maintained implementation contains the churn.
- CIMD-vs-DCR client support will keep shifting; "buy" insulates us from re-implementing registration as it does.

## 4. The decision I need from you

**Pick the authorization-server strategy (§2.3):**

1. **Adopt WorkOS AuthKit (recommended).** $0/mo now; a vendor in the auth path; $2,500/1M-MAU later. I proceed to wire AuthKit + design the §2.4 bridge.
2. **Build it ourselves** (app-as-AS + FastMCP OAuthProxy). No vendor, ~4–8 weeks, security-critical and ours to operate forever.
3. **Stytch** (alternative buy) or **another vendor** you prefer.
4. **Hold** — ship only the dark resource-server scaffolding for now.

Once you choose, the §2.4 credential-bridge option (a/b/c) is the immediate next design — and if it's (b), I'll bring you the API-change proposal separately before touching any spec.

## Non-goals (this pass)
- Turning OAuth on in production (no AS configured).
- The token→credential exchange / account provisioning bridge (§2.4).
- Any change to the live API-key onboarding, the skill, README, or `server.json`.
- Migrating the app's own first-party login to OAuth (the app remains an OIDC consumer; this is about the MCP edge only).

## References
- ADR-057 (MCP server; §2.4 defers this), ADR-036 (email verification / anti-abuse; SSO arrives verified), ADR-054 (custom OIDC/SAML SSO), ADR-053 (CLI; console-minted keys), ADR-010 (provisioning / unified API keys), ADR-014 (JSON:API, full-replace PUT)
- MCP authorization spec (current stable **2025-11-25**): `https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization`
- RFC 9728 (Protected Resource Metadata), RFC 8414 (AS Metadata), RFC 7591 (DCR), RFC 7636 (PKCE), RFC 8707 (Resource Indicators); `draft-ietf-oauth-client-id-metadata-document` (CIMD)
- FastMCP auth: `RemoteAuthProvider`, `JWTVerifier`, `MultiAuth`, `OAuthProxy`/`OIDCProxy`, AuthKit integration (`gofastmcp.com/integrations/authkit`)
- Vendor pricing (verified June 2026): `workos.com/pricing`, `stytch.com/pricing`, `auth0.com/pricing`, `aws.amazon.com/cognito/pricing`, `descope.com/pricing`
