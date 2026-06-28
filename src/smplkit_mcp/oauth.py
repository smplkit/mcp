"""OAuth 2.1 resource-server scaffolding for the smplkit MCP server (ADR-058).

The MCP authorization spec models the MCP server as an OAuth 2.1 **resource
server**: it validates access tokens minted by a separate **authorization
server** and advertises where those tokens come from via Protected Resource
Metadata (RFC 9728). This module wires that resource-server role onto the
FastMCP app — *without* committing to which authorization server mints the
tokens. That build-vs-buy choice (front the app with WorkOS/Auth0/Stytch, or
turn the app into an OAuth AS behind FastMCP's ``OAuthProxy``) is still pending
Mike's sign-off (ADR-058); the resource-server half below is identical for every
one of those options, so it can ship now.

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

**Not yet wired: the token→credential exchange.** Once a real OAuth access token
is validated, the server still has to obtain a credential it can forward to the
product APIs. That bridge depends on the authorization-server decision (e.g. the
AS mints/maps an smplkit API credential during the in-flow sign-in) and is
therefore deferred. Until it exists, a validated OAuth token reaching the tool
layer raises :data:`OAUTH_EXCHANGE_PENDING_MESSAGE` rather than silently
forwarding a token the product API would reject.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

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

OAUTH_EXCHANGE_PENDING_MESSAGE = (
    "This OAuth access token was validated, but exchanging it for a product-API "
    "credential is not implemented yet (pending the ADR-058 authorization-server "
    "decision). For now, connect with a smplkit API key instead."
)


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
