"""OAuth 2.1 resource-server scaffolding for the smplkit MCP server (ADR-058).

The MCP authorization spec models the MCP server as an OAuth 2.1 **resource
server**: it validates access tokens minted by a separate **authorization
server** and advertises where those tokens come from via Protected Resource
Metadata (RFC 9728). The authorization server is **WorkOS AuthKit**, fronting our
existing SSO in Standalone mode (ADR-058 §2.3). This module validates AuthKit's
tokens and, when a validated OAuth token arrives, exchanges it for a short-lived
app session JWT via the app's VPC-only endpoint (``exchange_for_app_token``) —
the credential the product APIs already accept. No API key is ever minted.

**Disabled by default.** With no authorization server configured,
:func:`build_auth_provider` returns ``None`` and the server behaves exactly as it
does today: the per-request API-key pass-through of ADR-057 §2.4. Set
``MCP_OAUTH_AUTHORIZATION_SERVERS`` (plus a ``MCP_OAUTH_JWKS_URI`` or
``MCP_OAUTH_PUBLIC_KEY`` to validate signatures) and the resource-server role
turns on:

* ``GET /.well-known/oauth-protected-resource/api/mcp`` advertises the resource
  and its authorization server(s) (RFC 9728);
* an unauthenticated MCP request gets ``401`` with
  ``WWW-Authenticate: Bearer ..., resource_metadata="<prm-url>"`` so the client
  can discover the AS and run the OAuth 2.1 + PKCE flow;
* bearer JWTs are validated locally (signature via JWKS, issuer, audience bound
  to this resource per RFC 8707, expiry) before any tool runs.

Scopes are *advertised* in the metadata (``scopes_supported``) but not yet
*gated* at the auth layer: a hard scope requirement would reject the API-key
pass-through (an API key carries no OAuth scopes), so per-scope authorization is
deferred to the credential model that lands with the authorization-server
decision.

**The API-key path keeps working alongside OAuth.** A :class:`MultiAuth`
composition tries OAuth JWT validation first, then falls back to accepting a
smplkit API key (``sk_*``) via :class:`ApiKeyPassthroughVerifier`. The MCP server
has never validated API keys itself — the product API is the authority
(ADR-057 §2.4) — so the pass-through accepts a well-formed key and lets the tool
forward it downstream exactly as today.

**The token→credential bridge.** When a validated OAuth (WorkOS) token reaches
the tool layer, :func:`exchange_for_app_token` swaps it — via the app's VPC-only
``/internal/v1/mcp/token`` endpoint (``MCP_OAUTH_APP_INTERNAL_URL``) — for a
short-lived app session JWT, cached in-process until it nears expiry. That JWT is
what gets forwarded to the product APIs; the WorkOS token never leaves this
boundary (no token passthrough) and no API key is created.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass

import httpx

# Base auth classes are lightweight. The JWT verifier pulls heavy crypto deps
# (authlib/joserfc/cryptography), so it is imported lazily in
# build_token_verifier() to keep the common, OAuth-disabled path cheap.
from fastmcp.server.auth import (
    AccessToken,
    AuthProvider,
    MultiAuth,
    RemoteAuthProvider,
    TokenVerifier,
)
from pydantic import AnyHttpUrl

DEFAULT_RESOURCE_BASE_URL = "https://mcp.smplkit.com"
DEFAULT_MCP_PATH = "/api/mcp"
# smplkit API keys are prefixed ``sk_`` (e.g. ``sk_api_…``). Used to tell an API
# key apart from an OAuth JWT on the same ``Authorization: Bearer`` header.
DEFAULT_API_KEY_PREFIXES = ("sk_",)
RESOURCE_NAME = "smplkit MCP Server"

# In-flow credential exchange: the MCP server forwards a validated WorkOS token
# to the app's VPC-only exchange endpoint and receives a short-lived app session
# JWT to forward to the product APIs — no API key is minted (ADR-058 §2.4).
_EXCHANGE_PATH = "/internal/v1/mcp/token"
_EXCHANGE_BUFFER_SECONDS = 30


def _csv(value: str | None) -> tuple[str, ...]:
    """Parse a comma-separated env value into a tuple, dropping blanks."""
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class OAuthSettings:
    """Resolved OAuth resource-server settings (see module docstring for env)."""

    authorization_servers: tuple[str, ...] = ()
    resource_base_url: str = DEFAULT_RESOURCE_BASE_URL
    mcp_path: str = DEFAULT_MCP_PATH
    jwks_uri: str | None = None
    public_key: str | None = None
    issuer: str | None = None
    audience: str | None = None
    algorithm: str | None = None
    scopes_supported: tuple[str, ...] = ()
    api_key_prefixes: tuple[str, ...] = DEFAULT_API_KEY_PREFIXES
    # VPC-only base URL of the app's token-exchange endpoint (ADR-058 §2.4).
    app_internal_url: str = ""

    @property
    def enabled(self) -> bool:
        """OAuth is active only once an AS *and* a way to verify signatures exist."""
        return bool(self.authorization_servers) and bool(self.jwks_uri or self.public_key)

    @property
    def resource_url(self) -> str:
        """Canonical resource identifier (RFC 9728 ``resource``)."""
        return f"{self.resource_base_url.rstrip('/')}/{self.mcp_path.lstrip('/')}"

    @property
    def token_audience(self) -> str:
        """Audience tokens must carry — defaults to the resource URL (RFC 8707)."""
        return self.audience or self.resource_url


def load_oauth_settings(env: dict[str, str] | None = None) -> OAuthSettings:
    """Build :class:`OAuthSettings` from the environment.

    ``MCP_OAUTH_AUTHORIZATION_SERVERS`` (comma-separated issuer URLs) plus one of
    ``MCP_OAUTH_JWKS_URI`` / ``MCP_OAUTH_PUBLIC_KEY`` turn the resource-server
    role on; everything else has a safe default. When unset, the server runs the
    API-key-only path it ships with today.
    """
    env = os.environ if env is None else env
    return OAuthSettings(
        authorization_servers=_csv(env.get("MCP_OAUTH_AUTHORIZATION_SERVERS")),
        resource_base_url=env.get("MCP_OAUTH_RESOURCE_BASE_URL", DEFAULT_RESOURCE_BASE_URL),
        mcp_path=env.get("MCP_OAUTH_MCP_PATH", DEFAULT_MCP_PATH),
        jwks_uri=env.get("MCP_OAUTH_JWKS_URI") or None,
        public_key=env.get("MCP_OAUTH_PUBLIC_KEY") or None,
        issuer=env.get("MCP_OAUTH_ISSUER") or None,
        audience=env.get("MCP_OAUTH_AUDIENCE") or None,
        algorithm=env.get("MCP_OAUTH_ALGORITHM") or None,
        scopes_supported=_csv(env.get("MCP_OAUTH_SCOPES_SUPPORTED")),
        api_key_prefixes=_csv(env.get("MCP_OAUTH_API_KEY_PREFIXES")) or DEFAULT_API_KEY_PREFIXES,
        app_internal_url=env.get("MCP_OAUTH_APP_INTERNAL_URL") or "",
    )


# Module-level settings, resolved once. server.py reads this to decide whether to
# attach the auth provider and how to treat the inbound credential.
SETTINGS = load_oauth_settings()


def looks_like_api_key(token: str, settings: OAuthSettings | None = None) -> bool:
    """True if ``token`` is a smplkit API key (vs. an OAuth JWT)."""
    settings = settings or SETTINGS
    return bool(token) and token.startswith(settings.api_key_prefixes)


class ApiKeyPassthroughVerifier(TokenVerifier):
    """Accept smplkit API keys so the per-request pass-through keeps working.

    The MCP server does not validate API keys itself — the product API is the
    authority (ADR-057 §2.4). This verifier therefore *accepts* any token shaped
    like a smplkit API key so the request proceeds to the tool, which forwards
    the key downstream exactly as today. Tokens that are not API keys (e.g. an
    OAuth JWT) return ``None`` here, so an invalid/expired OAuth token falls
    through to a 401 + ``WWW-Authenticate`` challenge instead of being
    mis-forwarded as if it were a key.
    """

    def __init__(self, prefixes: tuple[str, ...] = DEFAULT_API_KEY_PREFIXES) -> None:
        super().__init__()
        self._prefixes = tuple(prefixes)

    async def verify_token(self, token: str) -> AccessToken | None:
        if token and token.startswith(self._prefixes):
            return AccessToken(token=token, client_id="smplkit-api-key", scopes=[])
        return None


def build_token_verifier(settings: OAuthSettings) -> TokenVerifier:
    """Build the JWT resource-server verifier for ``settings``.

    Validates signature (JWKS or static public key), issuer, audience (bound to
    this resource per RFC 8707), and expiry. Scopes are advertised, not gated
    (see module docstring), so ``required_scopes`` is intentionally left unset.
    """
    # Lazy import: JWTVerifier drags in authlib/cryptography (~150ms). Only paid
    # for when OAuth is actually configured.
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    return JWTVerifier(
        public_key=settings.public_key,
        jwks_uri=settings.jwks_uri,
        issuer=settings.issuer,
        audience=settings.token_audience,
        algorithm=settings.algorithm,
    )


def build_auth_provider(settings: OAuthSettings | None = None) -> AuthProvider | None:
    """Return the FastMCP auth provider, or ``None`` when OAuth is not configured.

    When configured, returns a :class:`MultiAuth` that serves Protected Resource
    Metadata + the 401 challenge and validates OAuth JWTs, while still accepting
    smplkit API keys via :class:`ApiKeyPassthroughVerifier`.
    """
    settings = settings or SETTINGS
    if not settings.enabled:
        return None

    remote = RemoteAuthProvider(
        token_verifier=build_token_verifier(settings),
        authorization_servers=[AnyHttpUrl(url) for url in settings.authorization_servers],
        base_url=settings.resource_base_url,
        resource_base_url=settings.resource_base_url,
        resource_name=RESOURCE_NAME,
        scopes_supported=list(settings.scopes_supported) or None,
    )
    return MultiAuth(
        server=remote,
        verifiers=[ApiKeyPassthroughVerifier(settings.api_key_prefixes)],
        base_url=settings.resource_base_url,
    )


# --------------------------------------------------------------------------
# Token→credential bridge (ADR-058 §2.4)
# --------------------------------------------------------------------------


class TokenExchangeError(Exception):
    """Raised when exchanging a WorkOS token for an app session JWT fails."""


# Cache of exchanged app JWTs, keyed by a hash of the WorkOS token, so repeated
# tool calls in one session don't re-hit the app. Bounded by dropping expired
# entries on each miss. Guarded by a lock (tools may run across threads).
_exchange_cache: dict[str, tuple[str, float]] = {}
_exchange_lock = threading.Lock()


def exchange_for_app_token(workos_token: str, settings: OAuthSettings | None = None) -> str:
    """Exchange a validated WorkOS access token for a short-lived app session JWT.

    Calls the app's VPC-only ``/internal/v1/mcp/token`` endpoint and caches the
    returned JWT in-process until shortly before it expires. The WorkOS token is
    never forwarded downstream (no token passthrough) and no API key is minted.

    Raises :class:`TokenExchangeError` if OAuth is misconfigured, the app is
    unreachable, or the token is rejected.
    """
    settings = settings or SETTINGS
    if not settings.app_internal_url:
        raise TokenExchangeError(
            "OAuth is enabled but MCP_OAUTH_APP_INTERNAL_URL is not configured."
        )

    key = hashlib.sha256(workos_token.encode()).hexdigest()
    now = time.time()
    with _exchange_lock:
        cached = _exchange_cache.get(key)
        if cached is not None and cached[1] - _EXCHANGE_BUFFER_SECONDS > now:
            return cached[0]

    url = f"{settings.app_internal_url.rstrip('/')}{_EXCHANGE_PATH}"
    try:
        response = httpx.post(url, json={"workos_access_token": workos_token}, timeout=10.0)
    except httpx.HTTPError as exc:
        raise TokenExchangeError(
            "Could not reach the smplkit sign-in service. Please try again."
        ) from exc

    if response.status_code in (401, 403):
        raise TokenExchangeError(
            "Your smplkit sign-in could not be verified. Reconnect to sign in again."
        )
    if response.status_code >= 400:
        raise TokenExchangeError("The smplkit sign-in service rejected the request.")

    data = response.json()
    app_token = data.get("access_token")
    if not app_token:
        raise TokenExchangeError("The smplkit sign-in service returned no token.")
    expires_in = data.get("expires_in") or 300

    with _exchange_lock:
        for stale in [k for k, (_, exp) in _exchange_cache.items() if exp <= now]:
            _exchange_cache.pop(stale, None)
        _exchange_cache[key] = (app_token, now + float(expires_in))
    return app_token
