"""FastMCP server wiring: key extraction, error mapping, tools, routes."""
from __future__ import annotations

import asyncio

import pytest
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from smplkit_mcp import server, tools
from smplkit_mcp.errors import JobsApiError, MissingApiKeyError
from smplkit_mcp.urls import NonPublicTargetError

# -- extract_api_key --------------------------------------------------------


class TestExtractApiKey:
    def test_bearer(self):
        assert server.extract_api_key({"authorization": "Bearer sk_api_x"}) == "sk_api_x"

    def test_bearer_case_insensitive(self):
        assert server.extract_api_key({"authorization": "bearer sk_api_y"}) == "sk_api_y"

    def test_custom_header(self):
        assert server.extract_api_key({"x-smplkit-api-key": "sk_api_z"}) == "sk_api_z"

    def test_bearer_preferred_over_custom(self):
        headers = {"authorization": "Bearer first", "x-smplkit-api-key": "second"}
        assert server.extract_api_key(headers) == "first"

    def test_empty_bearer_falls_back_to_custom(self):
        headers = {"authorization": "Bearer    ", "x-smplkit-api-key": "fallback"}
        assert server.extract_api_key(headers) == "fallback"

    def test_missing_raises(self):
        with pytest.raises(MissingApiKeyError):
            server.extract_api_key({})


# -- _call error mapping ----------------------------------------------------


class TestCallMapping:
    def test_jobs_api_error_becomes_friendly_toolerror(self, monkeypatch, fake_client):
        monkeypatch.setattr(server, "_client", lambda: fake_client)

        def fn(_client):
            raise JobsApiError(401, [{"status": "401", "title": "Unauthorized"}])

        with pytest.raises(ToolError) as exc:
            server._call(fn)
        assert "API key" in str(exc.value)

    def test_value_error_becomes_toolerror(self, monkeypatch, fake_client):
        monkeypatch.setattr(server, "_client", lambda: fake_client)

        def fn(_client):
            raise ValueError("bad input")

        with pytest.raises(ToolError, match="bad input"):
            server._call(fn)

    def test_non_public_target_becomes_toolerror(self, monkeypatch, fake_client):
        monkeypatch.setattr(server, "_client", lambda: fake_client)

        def fn(_client):
            raise NonPublicTargetError()

        with pytest.raises(ToolError, match="tunnel"):
            server._call(fn)

    def test_missing_key_surfaces_as_toolerror(self, monkeypatch):
        monkeypatch.setattr(server, "get_http_headers", lambda **kw: {})
        with pytest.raises(ToolError, match="Connect your smplkit API key"):
            server.list_jobs()


# -- tool wrappers (delegate to tools.* with the per-request client) --------


class TestToolWrappers:
    @pytest.fixture(autouse=True)
    def _patch_client(self, monkeypatch, fake_client):
        self.client = fake_client
        monkeypatch.setattr(server, "_client", lambda: fake_client)

    def test_create_get_run_list_update_delete_flow(self):
        created = server.create_job(name="Flow Job", url="https://api.example.com/x",
                                    schedule="0 7 * * *")
        assert created["kind"] == "recurring"
        job_id = created["id"]

        assert server.get_job(job_id=job_id)["id"] == job_id

        listed = server.list_jobs()
        assert any(j["id"] == job_id for j in listed["jobs"])

        run = server.run_job(job_id=job_id, wait=False)
        assert run["trigger"] == "MANUAL"

        runs = server.list_runs(job=job_id)
        assert runs["count"] >= 1
        assert server.get_run(run_id=run["id"])["id"] == run["id"]

        updated = server.update_job(job_id=job_id, name="Renamed")
        assert updated["name"] == "Renamed"

        assert server.delete_job(job_id=job_id)["deleted"] is True


# -- tool registration ------------------------------------------------------


def test_eight_tools_registered():
    registered = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in registered}
    assert names == {
        "list_jobs", "get_job", "create_job", "update_job",
        "delete_job", "run_job", "list_runs", "get_run",
    }


def test_default_environment_constant():
    assert tools.DEFAULT_ENVIRONMENT == "production"


def test_server_identity_is_platform_level():
    # Platform identity, not Jobs-specific (the name clients show on initialize).
    assert server.mcp.name == "smplkit MCP Server"
    assert "smplkit platform" in server.INSTRUCTIONS


# -- custom routes ----------------------------------------------------------


def test_health_and_liveness_routes():
    from smplkit_mcp.app import app

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.text == "ok"

        liveness = client.get("/api/liveness")
        assert liveness.status_code == 200
        assert liveness.json()["service"] == "mcp"


# -- __main__ ---------------------------------------------------------------


def test_main_runs_http_transport(monkeypatch):
    import smplkit_mcp.__main__ as main_module

    captured = {}
    monkeypatch.setattr(main_module.mcp, "run", lambda **kw: captured.update(kw))
    main_module.main()
    assert captured["transport"] == "http"
    assert captured["path"] == "/api/mcp"
    assert captured["stateless_http"] is True
    assert captured["json_response"] is True
