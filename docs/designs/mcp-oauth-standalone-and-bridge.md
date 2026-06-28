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

## Part B — The credential bridge

**The problem.** After the MCP server validates the WorkOS token (aud = our MCP resource), it must call the product APIs (jobs, config, flags, logging, audit) on the user's behalf. Two hard constraints: (1) the MCP spec **forbids token passthrough** — the WorkOS token (aud = MCP resource) must not be forwarded to a different-audience API (confused-deputy); (2) we don't want to manufacture **persisted API keys** as plumbing — expired keys linger as customer-visible "junk" rows.

**The enabling finding.** Every product service already accepts **ephemeral JWTs** via smplkit-core's `create_auth_dependency` (`python-core/src/smplcore/auth.py:446`): a **user JWT** (app-issued HS256, `iss=app_url`, `aud=smplkit-app`, carries `account_id`/`user_id`) and an **internal on-behalf-of JWT** (`mint_internal_jwt`, 60s, `originating_actor_type=USER`). Neither is persisted — they leave **zero database residue**. API keys are the *only* credential that creates a stored row. So the right bridge never mints a key.

### The right design: token exchange → ephemeral app JWT  ★

The **app** (the platform's existing credential authority — it already owns `issue_token`, `APP_AUTH_SECRET`, and `INTERNAL_JWT_SECRET`) performs an RFC 8693-style exchange; the MCP edge holds no platform secret.

1. MCP server validates the inbound WorkOS token (resource-server hook, already built).
2. MCP server calls a **VPC-only app endpoint** (e.g. `POST {app_internal_url}/internal/v1/mcp/token`) relaying the user's WorkOS token as the subject token.
3. The app validates that WorkOS token (WorkOS JWKS), confirms its audience is our MCP resource, resolves the smplkit user/account (from the WorkOS `external_id` we set in Part A, or the `urn:smplkit:account_id` claim), and mints a **short-lived user JWT** via the existing `issue_token` (short TTL, e.g. 5 min) — **no API key**.
4. MCP server forwards that ephemeral JWT to the product APIs, which already accept it, and caches it in-process for its brief TTL (one exchange serves all product calls until it expires).

**Properties:**
- **No API keys, no junk** — the forwarded credential is a stateless JWT; nothing is persisted or shown in the customer's key list. (No "auto-delete keys" feature needed.)
- **No token passthrough** — the WorkOS token is the *subject* of an exchange, consumed only by the MCP server (correct audience) and the app (the exchange authority); products receive a *different*, platform-issued token. Spec-compliant.
- **MCP edge holds no static platform secret** — it relays the user's own WorkOS token and receives a short-lived token; the app stays the sole authority. (Preserves the ADR-057 §2.4 posture — unlike giving the edge `INTERNAL_JWT_SECRET` or an admin key.)
- **No public API change, no SDK regen** — the exchange endpoint is an internal VPC-only route (same pattern as the existing `/internal/v1/accounts` key-introspection endpoint), not a customer resource.
- **Reuses what exists** — `issue_token` on the app side; "products accept user JWTs" on the receiving side. One small new internal endpoint.

**Variant considered — app mints a per-call internal JWT** (`mint_internal_jwt`, `originating_actor=USER`, 60s, aud-scoped per service): gives richer audit provenance ("user X via MCP") and per-service audience scoping, but requires one mint per target service (chattier) and the user-JWT path already records `actor_type=USER` correctly. Keep the user-JWT exchange as the default; the internal-JWT variant is a clean future refinement if we want "via MCP" provenance in audit.

### Rejected alternatives

- **Mint short-lived API keys** (the original B1) — creates the customer-visible "expired junk" rows; rejected on your call. The platform doesn't need it.
- **MCP server mints internal JWTs itself** — would require shipping `INTERNAL_JWT_SECRET` to the edge; a single powerful secret at the edge can impersonate any user to any service. Same smell as a static admin key. Keep minting at the app.
- **Products become WorkOS resource servers** (validate WorkOS JWTs directly) — a public API contract change across all services + 6 SDK regens, for no benefit the ephemeral-JWT exchange doesn't already deliver. Only revisit if non-MCP external clients ever need it.

---

## Decisions (approved 2026-06-28)

- **Part A** — Standalone handler in `smplkit/app` (`httpx`, `WORKOS_API_KEY`, JWT Template): **approved**.
- **Part B** — token exchange → ephemeral **short-TTL user JWT** via a VPC-only app endpoint; no API keys; no public API change: **approved** (user-JWT variant chosen over the per-call internal-JWT provenance variant).

## Implementation contract (turnkey for the app-side session)

**New, internal/VPC-only app endpoint** (excluded from the customer OpenAPI — model it on the existing `/internal/v1/accounts` route):
```
POST {app_internal_url}/internal/v1/mcp/token
Body:    { "workos_access_token": "<the user's WorkOS JWT>" }
Action:  validate the WorkOS JWT (WorkOS JWKS for the env) → assert aud == our MCP resource
         → resolve smplkit user/account (WorkOS external_id we set in Part A, or the
           urn:smplkit:account_id claim) → issue_token(..., ttl_seconds≈300)
Returns: { "access_token": "<app user JWT>", "expires_in": 300 }
```

**App-side tasks (`smplkit/app`):**
1. `routes/mcp_auth.py` — `GET /api/v1/auth/mcp/external-signin?external_auth_id=…`: stash `external_auth_id`+nonce in session, start existing OIDC (`begin_oidc_login`, `entry_point="mcp_workos"`).
2. Completion branch in `handle_oidc_callback`: when `entry_point=="mcp_workos"`, `POST https://api.workos.com/authkit/oauth2/complete` (Bearer `WORKOS_API_KEY`) with `{external_auth_id, user:{id:user.id, email, first_name, last_name, metadata:{smplkit_account_id, smplkit_user_id}}}` → redirect to returned `redirect_uri`.
3. The `/internal/v1/mcp/token` exchange endpoint (above).
4. `core/config.py`: add `workos_api_key`, `workos_client_id` (Secrets Manager); keep the WorkOS call on `httpx` (no new dep).
5. Tests (≥90% coverage), and confirm both new routes are excluded from the published OpenAPI (no SDK regen).

**Dashboard task (Mike):** add the JWT Template (`urn:smplkit:account_id ← {{user.metadata.smplkit_account_id}}`, `urn:smplkit:user_id ← {{user.metadata.smplkit_user_id}}`); set the External Sign-in URI to the route from task 1.

**MCP-side tasks (`smplkit/mcp`, this repo):** replace `OAUTH_EXCHANGE_PENDING_MESSAGE` in `_client()` with: on a validated OAuth token, call the exchange endpoint, cache the returned user JWT in-process by token id until ~TTL, and build the product client with that JWT. Add `APP_INTERNAL_URL` config. Then flip the staging `MCP_OAUTH_*` flag on and run an end-to-end test from a real MCP client.

**Then:** production cutover (WorkOS Production env + billing card; replicate the staging config).

**Pre-merge:** adversarial security review of the new auth code (confused-deputy, audience checks, WorkOS-JWT validation, replay, the VPC-only endpoint's threat model, edge-held short-TTL JWT exposure).

## References
- ADR-058 (§2.1 resource server; §2.3 WorkOS decision; §2.4 — this elaborates it)
- WorkOS: Standalone Connect `complete` (`POST /authkit/oauth2/complete`), JWT Templates / External ID / Custom Metadata, AuthKit MCP docs
- App integration points: `routes/auth.py` (OIDC), `core/auth.py` (`issue_token`), `core/admin_key_auth.py` (service mint), `models/api_key.py` (`expires_at`), `core/config.py` (settings)
