# Design for sign-off: MCP OAuth — Standalone sign-in handler + credential bridge

| Field   | Value                                                                 |
|---------|-----------------------------------------------------------------------|
| Status  | **Draft for Mike's sign-off** — elaborates ADR-058 §2.4               |
| Date    | 2026-06-28                                                            |
| Author  | Claude Code                                                          |
| Repos   | `smplkit/app` (Part A handler), `smplkit/mcp` (Part B bridge client) |

This note designs the two remaining pieces after WorkOS AuthKit provisioning (now done + verified in Staging): **(A)** the app-side "External Sign-in" handler that lets WorkOS delegate the actual login to our existing SSO, and **(B)** the credential bridge that turns a validated WorkOS token into something the Jobs API accepts. Both are presented for approval before implementation; Part B is an architecture decision (one variant is a public API change).

All WorkOS mechanics below are verified against current WorkOS docs (June 2026).

---

## Where we are

- Resource-server scaffolding shipped (ADR-058 §2.1), behind `MCP_OAUTH_*`, CI green.
- WorkOS AuthKit **Staging** provisioned: DCR + CIMD + PKCE on; resource indicator `https://mcp.smplkit.com/api/mcp`; AuthKit domain `https://satisfying-voyage-87-staging.authkit.app`. Verified our server's PRM/401/JWKS validation against the live AS.
- "External Sign-in URI" left **empty** in the dashboard, pending Part A.

---

## Part A — Standalone "External Sign-in" handler (app-side)

**Goal:** WorkOS runs the OAuth protocol with the MCP client, but the *human login* happens in **our** app via our existing Google/Microsoft SSO — no users migrate into WorkOS, our account model stays the source of truth (ADR-036 intact).

### Verified WorkOS flow

1. MCP client → AuthKit `/oauth2/authorize` (PKCE + `resource=https://mcp.smplkit.com/api/mcp`).
2. AuthKit redirects the browser to our **External Sign-in URI** with `?external_auth_id=<temp id>`.
3. **Our app authenticates the user** (existing SSO).
4. Our backend calls **`POST https://api.workos.com/authkit/oauth2/complete`** (`Authorization: Bearer ${WORKOS_API_KEY}`):
   ```json
   {
     "external_auth_id": "<from step 2>",
     "user": {
       "id":    "<our UserModel.id>",   // becomes the WorkOS external_id
       "email": "<user email>",          // must be unique; creates-or-updates the WorkOS user
       "first_name": "...", "last_name": "...",
       "metadata": { "smplkit_account_id": "<account uuid>", "smplkit_user_id": "<user uuid>" }
     }
   }
   ```
   → returns `{ "redirect_uri": "..." }`.
5. We redirect the browser to `redirect_uri`; AuthKit finishes consent and issues the access token (aud = our MCP resource) to the client.

### What we build in `smplkit/app`

A new auth router (e.g. `routes/mcp_auth.py`, `APIRouter(prefix="/auth/mcp")`, registered in `main.py` like the other auth routers):

- **`GET /api/v1/auth/mcp/external-signin`** — the URL we register as the WorkOS External Sign-in URI. Stashes `external_auth_id` + a CSRF nonce in the session and kicks off the existing OIDC flow (`begin_oidc_login`), tagging `session["oidc"]["entry_point"] = "mcp_workos"`.
- **Completion branch in the existing callback.** `handle_oidc_callback` (`routes/auth.py:464`) already resolves/creates the `UserModel` + `AccountUserModel` and computes `account_id`. When `entry_point == "mcp_workos"`, instead of issuing our own JWT + redirecting to the frontend, call the WorkOS `complete` endpoint (above) with the resolved identity and `metadata`, then redirect to the returned `redirect_uri`.

Reuse, don't reinvent: the OIDC machinery, `_create_user_account_and_env`, and account resolution are all already there. SSO users arrive `email_verified=True`, so the ADR-036 gate is satisfied exactly as today.

### Account selection

`account_id` resolution mirrors login: the user's claim-selected or earliest-created account (`account_context.py`). For multi-account users, a clean **future** enhancement is WorkOS `user_consent_options` on the `complete` call — it renders an account picker at consent and writes the choice into a claim. v1: default account; note the upgrade path.

### Surfacing the account in the token (JWT Template)

In the WorkOS Dashboard (one-time, per environment), add a **JWT Template** mapping our metadata into claims:
```
"urn:smplkit:account_id": "{{ user.metadata.smplkit_account_id }}"
"urn:smplkit:user_id":    "{{ user.metadata.smplkit_user_id }}"
```
Now the access token the MCP server validates **carries the smplkit account** — which is the key that makes Part B clean. (I'll hand you the exact template to paste.)

### Dependencies / config

- **No new Python dependency** — the single WorkOS call uses `httpx` (already a backend dep). (The `workos` SDK is optional sugar; not worth a new dep for one endpoint.)
- New settings in `core/config.py`: `workos_api_key` (Secrets Manager), `workos_client_id`; same injection pattern as `google_client_secret` / `sso_secret_encryption_key`.

---

## Part B — The credential bridge (decision)

**The problem.** After the MCP server validates the WorkOS token (and reads `urn:smplkit:account_id`), it still has to call the **Jobs REST API**, which authenticates with smplkit API keys (`sk_api_*`). And the MCP spec **forbids token passthrough** (a token minted for the MCP resource must not be forwarded to a different-audience API — confused-deputy). So we can't just forward the WorkOS token to Jobs. Three options:

### B1 — App-mediated exchange  ★ recommended (interim)

The MCP server, on a validated WorkOS token, calls a **new internal app endpoint** (e.g. `POST /api/v1/internal/mcp/credential`), relaying the user's WorkOS token. The app validates the WorkOS JWT (WorkOS JWKS), reads `urn:smplkit:account_id`, mints a **short-lived, account-scoped `sk_api_` key** (reusing the existing admin/service mint path; keys already support `expires_at`), and returns it. The MCP server forwards *that key* to Jobs and caches it in-process until it nears expiry.

- **Pros:** Jobs and the **entire public API contract stay unchanged** (no `api-change-checklist`, no SDK regen — the new endpoint is internal-only, excluded from the published OpenAPI). The MCP server holds **no static platform credential** — it only ever relays the *user's own* token and receives a short-lived key. No token passthrough (the WorkOS token never reaches Jobs; Jobs gets a proper smplkit key). Account-in-token makes the lookup trivial.
- **Cons:** one extra app hop per (cache-miss) request — mitigated by caching the short-lived key for its lifetime; and a new internal endpoint to build + secure (must be excluded from the customer spec and rate-limited).

### B2 — Product APIs become OAuth resource servers  (north star, deferred)

Jobs (and every product API) validate WorkOS JWTs directly and resolve the account from a claim; the MCP server performs an RFC 8693 **token exchange** (MCP-audience → Jobs-audience) per downstream call.

- **Pros:** spec-pure, no API keys in the loop, uniform auth across the platform.
- **Cons:** **a public API contract change on every product service** (new auth requirement) → full `api-change-checklist` + regen across **6 SDKs** + showcase impact; depends on token-exchange support (WorkOS OBO is unconfirmed) or our own exchange service. Largest blast radius. **Right long-term, wrong first step.**

### B3 — Static service credential in the MCP server  (rejected)

The MCP server holds a long-lived `sk_admin_`-class credential and mints account-scoped keys itself.

- **Why not:** smallest code, but it puts a platform-wide credential inside the stateless edge server — a compromise of the MCP server becomes admin access. This is exactly the posture ADR-057 §2.4 avoided ("no platform credential"). Don't.

### Recommendation

**Ship B1 now; document B2 as the migration target.** B1 keeps the public contract and the 6 SDKs untouched, keeps the MCP server credential-light, and is fully enabled by the account-in-token from Part A. Revisit B2 once the OAuth path is proven and there's a concrete reason to make the product APIs natively OAuth (e.g. non-MCP first-party clients).

---

## Decisions I need from you

1. **Approve Part A** (the Standalone handler in `smplkit/app`, using `httpx`, adding `WORKOS_API_KEY`, plus the JWT Template). — yes/adjust
2. **Pick the bridge:** **B1 (recommended)** / B2 / B3.
   - If **B1**: I confirm the internal endpoint is excluded from the published OpenAPI (no SDK regen). No public API change.
   - If **B2**: I stop and bring you a full API-change proposal (per the org rule) before touching any service or spec.
3. **Build order:** once approved, I'd implement in `smplkit/app` (separate session/checkout) in this order: (a) the External Sign-in handler + `complete()` call, (b) the JWT Template (you paste it in the dashboard), (c) the B1 internal credential endpoint, (d) wire the MCP server's exchange client + flip the staging flag on, (e) end-to-end test from a real MCP client. Then production cutover (WorkOS Prod env + card).

## References
- ADR-058 (§2.1 resource server; §2.3 WorkOS decision; §2.4 — this elaborates it)
- WorkOS: Standalone Connect `complete` (`POST /authkit/oauth2/complete`), JWT Templates / External ID / Custom Metadata, AuthKit MCP docs
- App integration points: `routes/auth.py` (OIDC), `core/auth.py` (`issue_token`), `core/admin_key_auth.py` (service mint), `models/api_key.py` (`expires_at`), `core/config.py` (settings)
