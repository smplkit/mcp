"""OAuth 2.1 resource-server scaffolding (ADR-058).

Covers the settings model, the API-key pass-through verifier, JWT validation,
and the wired behaviors (Protected Resource Metadata, the 401/WWW-Authenticate
challenge, and that the API-key path keeps working) via a FastMCP app built with
a test provider. The live default (no MCP_OAUTH_* env) stays API-key-only.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from smplkit_mcp import oauth, server

RESOURCE_BASE = "https://mcp.test.example"
MCP_PATH = "/api/mcp"
AS_URL = "https://as.test.example"
PRM_PATH = "/.well-known/oauth-protected-resource/api/mcp"

# Shared keypair so the JWKS/JWT tests sign and verify against the same key.
_KEYPAIR = RSAKeyPair.generate()


def _enabled_settings(**overrides) -> oauth.OAuthSettings:
    base = dict(
        authorization_servers=(AS_URL,),
        resource_base_url=RESOURCE_BASE,
        mcp_path=MCP_PATH,
        public_key=_KEYPAIR.public_key,
        issuer=AS_URL,
        scopes_supported=("jobs:write",),
    )
    base.update(overrides)
    return oauth.OAuthSettings(**base)


def _build_app(settings: oauth.OAuthSettings):
    """A minimal FastMCP app wired with the OAuth provider, mirroring app.py."""
    provider = oauth.build_auth_provider(settings)
    mcp = FastMCP(name="smplkit MCP Server", auth=provider)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp.http_app(
        path=MCP_PATH, transport="http", stateless_http=True, json_response=True
    )


# -- _csv --------------------------------------------------------------------


class TestCsv:
    def test_none_and_empty(self):
        assert oauth._csv(None) == ()
        assert oauth._csv("") == ()
        assert oauth._csv("   ") == ()

    def test_splits_and_strips(self):
        assert oauth._csv("a, b ,,c") == ("a", "b", "c")


# -- OAuthSettings -----------------------------------------------------------


class TestOAuthSettings:
    def test_disabled_by_default(self):
        assert oauth.OAuthSettings().enabled is False

    def test_needs_both_as_and_a_key(self):
        assert oauth.OAuthSettings(authorization_servers=(AS_URL,)).enabled is False
        assert oauth.OAuthSettings(jwks_uri="https://x/jwks").enabled is False
        assert oauth.OAuthSettings(
            authorization_servers=(AS_URL,), jwks_uri="https://x/jwks"
        ).enabled is True
        assert oauth.OAuthSettings(
            authorization_servers=(AS_URL,), public_key="-----BEGIN-----"
        ).enabled is True

    def test_resource_url_joins_base_and_path(self):
        s = oauth.OAuthSettings(resource_base_url="https://m.example/", mcp_path="/api/mcp")
        assert s.resource_url == "https://m.example/api/mcp"

    def test_token_audience_defaults_to_resource_url(self):
        assert _enabled_settings().token_audience == f"{RESOURCE_BASE}{MCP_PATH}"

    def test_explicit_audience_wins(self):
        s = _enabled_settings(audience="urn:smplkit:mcp")
        assert s.token_audience == "urn:smplkit:mcp"


class TestLoadOAuthSettings:
    def test_empty_env_is_disabled(self):
        assert oauth.load_oauth_settings({}).enabled is False

    def test_full_env_parse(self):
        env = {
            "MCP_OAUTH_AUTHORIZATION_SERVERS": f"{AS_URL}, https://as2.example",
            "MCP_OAUTH_RESOURCE_BASE_URL": "https://mcp.example",
            "MCP_OAUTH_MCP_PATH": "/api/mcp",
            "MCP_OAUTH_JWKS_URI": "https://as.example/jwks",
            "MCP_OAUTH_ISSUER": AS_URL,
            "MCP_OAUTH_AUDIENCE": "urn:aud",
            "MCP_OAUTH_ALGORITHM": "RS256",
            "MCP_OAUTH_SCOPES_SUPPORTED": "jobs:read, jobs:write",
            "MCP_OAUTH_API_KEY_PREFIXES": "sk_, smpl_",
        }
        s = oauth.load_oauth_settings(env)
        assert s.enabled is True
        assert s.authorization_servers == (AS_URL, "https://as2.example")
        assert s.jwks_uri == "https://as.example/jwks"
        assert s.audience == "urn:aud"
        assert s.algorithm == "RS256"
        assert s.scopes_supported == ("jobs:read", "jobs:write")
        assert s.api_key_prefixes == ("sk_", "smpl_")

    def test_api_key_prefixes_default(self):
        assert oauth.load_oauth_settings({}).api_key_prefixes == oauth.DEFAULT_API_KEY_PREFIXES


# -- looks_like_api_key ------------------------------------------------------


class TestLooksLikeApiKey:
    def test_default_prefix(self):
        assert oauth.looks_like_api_key("sk_api_abc") is True
        assert oauth.looks_like_api_key("eyJhbGci.payload.sig") is False
        assert oauth.looks_like_api_key("") is False

    def test_custom_prefix_via_settings(self):
        s = oauth.OAuthSettings(api_key_prefixes=("smpl_",))
        assert oauth.looks_like_api_key("smpl_x", s) is True
        assert oauth.looks_like_api_key("sk_api_x", s) is False


# -- ApiKeyPassthroughVerifier ----------------------------------------------


class TestApiKeyPassthroughVerifier:
    def test_accepts_api_key(self):
        v = oauth.ApiKeyPassthroughVerifier()
        token = asyncio.run(v.verify_token("sk_api_demo"))
        assert token is not None
        assert token.client_id == "smplkit-api-key"
        assert token.token == "sk_api_demo"

    def test_rejects_non_key(self):
        v = oauth.ApiKeyPassthroughVerifier()
        assert asyncio.run(v.verify_token("eyJ.a.b")) is None
        assert asyncio.run(v.verify_token("")) is None

    def test_custom_prefixes(self):
        v = oauth.ApiKeyPassthroughVerifier(prefixes=("smpl_",))
        assert asyncio.run(v.verify_token("smpl_x")) is not None
        assert asyncio.run(v.verify_token("sk_api_x")) is None


# -- build_auth_provider -----------------------------------------------------


class TestBuildAuthProvider:
    def test_none_when_disabled(self):
        assert oauth.build_auth_provider(oauth.OAuthSettings()) is None

    def test_multiauth_when_enabled(self):
        from fastmcp.server.auth import MultiAuth

        provider = oauth.build_auth_provider(_enabled_settings())
        assert isinstance(provider, MultiAuth)

    def test_uses_module_settings_by_default(self, monkeypatch):
        monkeypatch.setattr(oauth, "SETTINGS", oauth.OAuthSettings())
        assert oauth.build_auth_provider() is None


# -- token verification ------------------------------------------------------


class TestTokenVerification:
    def _provider(self):
        return oauth.build_auth_provider(_enabled_settings())

    def test_valid_token_accepted(self):
        provider = self._provider()
        token = _KEYPAIR.create_token(
            subject="user-1", issuer=AS_URL, audience=f"{RESOURCE_BASE}{MCP_PATH}"
        )
        access = asyncio.run(provider.verify_token(token))
        assert access is not None
        assert access.client_id == "user-1"

    def test_wrong_audience_rejected(self):
        provider = self._provider()
        token = _KEYPAIR.create_token(
            subject="user-1", issuer=AS_URL, audience="https://elsewhere.example"
        )
        assert asyncio.run(provider.verify_token(token)) is None

    def test_expired_token_rejected(self):
        provider = self._provider()
        token = _KEYPAIR.create_token(
            subject="user-1",
            issuer=AS_URL,
            audience=f"{RESOURCE_BASE}{MCP_PATH}",
            expires_in_seconds=-10,
        )
        assert asyncio.run(provider.verify_token(token)) is None

    def test_api_key_accepted_alongside_oauth(self):
        # MultiAuth falls back to the pass-through verifier for sk_ keys.
        provider = self._provider()
        access = asyncio.run(provider.verify_token("sk_api_demo"))
        assert access is not None
        assert access.client_id == "smplkit-api-key"


# -- wired HTTP behaviors ----------------------------------------------------


class TestWiredBehaviors:
    @pytest.fixture()
    def client(self):
        with TestClient(_build_app(_enabled_settings())) as c:
            yield c

    def test_protected_resource_metadata(self, client):
        resp = client.get(PRM_PATH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        doc = resp.json()
        assert doc["resource"] == f"{RESOURCE_BASE}{MCP_PATH}"
        assert doc["authorization_servers"] == [f"{AS_URL}/"]
        assert doc["scopes_supported"] == ["jobs:write"]
        assert doc["resource_name"] == "smplkit MCP Server"

    def test_unauthenticated_request_gets_challenge(self, client):
        resp = client.post(
            MCP_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream",
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        www = resp.headers["www-authenticate"]
        assert www.startswith("Bearer ")
        assert f'resource_metadata="{RESOURCE_BASE}{PRM_PATH}"' in www

    def test_api_key_passes_the_gate(self, client):
        resp = client.post(
            MCP_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream",
                     "Content-Type": "application/json",
                     "Authorization": "Bearer sk_api_demo"},
        )
        # Past the 401/403 auth gate (tools/list needs no downstream call).
        assert resp.status_code == 200

    def test_health_is_not_gated(self, client):
        assert client.get("/health").status_code == 200


# -- server wiring -----------------------------------------------------------


class TestServerWiring:
    def test_auth_disabled_in_default_env(self):
        # The live server ships API-key-only; OAuth is opt-in via env.
        assert server.AUTH_PROVIDER is None

    def test_client_exchanges_oauth_token_when_enabled(self, monkeypatch):
        monkeypatch.setattr(
            oauth, "SETTINGS", _enabled_settings(app_internal_url="http://app.internal")
        )
        monkeypatch.setattr(
            oauth, "exchange_for_app_token", lambda token, settings=None: "app-jwt-xyz"
        )
        monkeypatch.setattr(
            server, "get_http_headers", lambda **kw: {"authorization": "Bearer eyJ.a.b"}
        )
        # The OAuth token is swapped for the app session JWT, which is forwarded.
        client = server._client()
        assert client._api_key == "app-jwt-xyz"

    def test_client_surfaces_exchange_failure(self, monkeypatch):
        monkeypatch.setattr(
            oauth, "SETTINGS", _enabled_settings(app_internal_url="http://app.internal")
        )

        def _boom(token, settings=None):
            raise oauth.TokenExchangeError("exchange unavailable")

        monkeypatch.setattr(oauth, "exchange_for_app_token", _boom)
        monkeypatch.setattr(
            server, "get_http_headers", lambda **kw: {"authorization": "Bearer eyJ.a.b"}
        )
        with pytest.raises(ToolError, match="exchange unavailable"):
            server._client()

    def test_client_forwards_api_key_when_enabled(self, monkeypatch):
        monkeypatch.setattr(oauth, "SETTINGS", _enabled_settings())
        monkeypatch.setattr(
            server, "get_http_headers", lambda **kw: {"authorization": "Bearer sk_api_x"}
        )
        client = server._client()
        assert client._api_key == "sk_api_x"

    def test_client_unaffected_when_disabled(self, monkeypatch):
        monkeypatch.setattr(oauth, "SETTINGS", oauth.OAuthSettings())
        monkeypatch.setattr(
            server, "get_http_headers", lambda **kw: {"authorization": "Bearer eyJ.a.b"}
        )
        # No OAuth gate when disabled: any bearer is forwarded as today.
        client = server._client()
        assert client._api_key == "eyJ.a.b"


class TestExchangeForAppToken:
    def setup_method(self):
        oauth._exchange_cache.clear()

    def test_unconfigured_app_url_raises(self):
        with pytest.raises(oauth.TokenExchangeError, match="APP_INTERNAL_URL"):
            oauth.exchange_for_app_token("wtok", _enabled_settings(app_internal_url=""))

    def test_success_and_caches(self, monkeypatch):
        calls = []

        class _Resp:
            status_code = 200

            def json(self):
                return {"access_token": "app-jwt", "expires_in": 300}

        def _post(url, json=None, timeout=None):
            calls.append(url)
            return _Resp()

        monkeypatch.setattr(oauth.httpx, "post", _post)
        s = _enabled_settings(app_internal_url="http://app.internal/")
        first = oauth.exchange_for_app_token("wtok", s)
        second = oauth.exchange_for_app_token("wtok", s)  # served from cache
        assert first == second == "app-jwt"
        assert calls == ["http://app.internal/internal/v1/mcp/token"]  # one POST only

    def test_rejected_token_maps_to_reconnect(self, monkeypatch):
        class _Resp:
            status_code = 403

            def json(self):
                return {}

        monkeypatch.setattr(oauth.httpx, "post", lambda *a, **k: _Resp())
        with pytest.raises(oauth.TokenExchangeError, match="verified"):
            oauth.exchange_for_app_token("wtok", _enabled_settings(app_internal_url="http://a"))

    def test_server_error_maps_to_rejected(self, monkeypatch):
        class _Resp:
            status_code = 500

            def json(self):
                return {}

        monkeypatch.setattr(oauth.httpx, "post", lambda *a, **k: _Resp())
        with pytest.raises(oauth.TokenExchangeError, match="rejected"):
            oauth.exchange_for_app_token("wtok", _enabled_settings(app_internal_url="http://a"))

    def test_network_error_maps_to_retry(self, monkeypatch):
        def _post(*a, **k):
            raise oauth.httpx.ConnectError("down")

        monkeypatch.setattr(oauth.httpx, "post", _post)
        with pytest.raises(oauth.TokenExchangeError, match="reach"):
            oauth.exchange_for_app_token("wtok", _enabled_settings(app_internal_url="http://a"))

    def test_no_token_in_response(self, monkeypatch):
        class _Resp:
            status_code = 200

            def json(self):
                return {"expires_in": 300}

        monkeypatch.setattr(oauth.httpx, "post", lambda *a, **k: _Resp())
        with pytest.raises(oauth.TokenExchangeError, match="no token"):
            oauth.exchange_for_app_token("wtok", _enabled_settings(app_internal_url="http://a"))

    def test_prunes_expired_entries_on_miss(self, monkeypatch):
        class _Resp:
            status_code = 200

            def json(self):
                return {"access_token": "fresh", "expires_in": 300}

        monkeypatch.setattr(oauth.httpx, "post", lambda *a, **k: _Resp())
        oauth._exchange_cache["stale-key"] = ("old", 0.0)  # expired at epoch
        oauth.exchange_for_app_token("wtok", _enabled_settings(app_internal_url="http://a"))
        assert "stale-key" not in oauth._exchange_cache  # pruned on the miss
