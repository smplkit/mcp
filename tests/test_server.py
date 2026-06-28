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


# -- new product tool wrappers (delegate to the product module) -------------


# (server tool, product module, module function name, call kwargs)
_PRODUCT_WRAPPERS = [
    (server.create_flag, "flags", "create_flag", dict(key="f", type="boolean", default=False)),
    (server.list_flags, "flags", "list_flags", {}),
    (server.get_flag, "flags", "get_flag", dict(key="f")),
    (server.set_flag, "flags", "set_flag", dict(key="f", value=True)),
    (server.delete_flag, "flags", "delete_flag", dict(key="f")),
    (server.create_config, "configs", "create_config", dict(name="C")),
    (server.list_configs, "configs", "list_configs", {}),
    (server.get_config, "configs", "get_config", dict(config_id="c")),
    (server.set_config_value, "configs", "set_config_value", dict(config_id="c", key="k", value=1)),
    (server.delete_config, "configs", "delete_config", dict(config_id="c")),
    (server.set_log_level, "loggers", "set_log_level", dict(logger_id="l", level="DEBUG")),
    (server.list_loggers, "loggers", "list_loggers", {}),
    (server.get_logger, "loggers", "get_logger", dict(logger_id="l")),
    (server.reset_logger, "loggers", "reset_logger", dict(logger_id="l")),
    (server.query_events, "audit", "query_events", dict(severity="ERROR")),
    (server.get_event, "audit", "get_event", dict(event_id="e")),
    (server.list_forwarders, "audit", "list_forwarders", {}),
    (server.create_forwarder, "audit", "create_forwarder",
     dict(name="F", url="https://siem.example.com/in")),
    (server.test_forwarder, "audit", "test_forwarder", dict(url="https://siem.example.com/in")),
    (server.delete_forwarder, "audit", "delete_forwarder", dict(forwarder_id="fwd")),
    (server.list_environments, "environments", "list_environments", {}),
]


@pytest.mark.parametrize("tool, module_name, fn_name, kwargs", _PRODUCT_WRAPPERS)
def test_product_wrapper_delegates(monkeypatch, tool, module_name, fn_name, kwargs):
    # A valid key so _api_key() succeeds and the per-request client is built.
    monkeypatch.setattr(server, "get_http_headers", lambda **kw: {"authorization": "Bearer k"})
    module = getattr(server, module_name)
    captured: dict = {}

    def stub(client, **kw):
        captured["client"] = client
        captured["kwargs"] = kw
        return {"ok": True}

    monkeypatch.setattr(module, fn_name, stub)
    result = tool(**kwargs)
    assert result == {"ok": True}
    # the tool forwarded every supplied argument to the product function
    for key, value in kwargs.items():
        assert captured["kwargs"][key] == value


def test_set_flag_wrapper_distinguishes_omitted_from_null(monkeypatch):
    monkeypatch.setattr(server, "get_http_headers", lambda **kw: {"authorization": "Bearer k"})
    captured: dict = {}
    monkeypatch.setattr(server.flags, "set_flag", lambda client, **kw: captured.update(kw) or {})

    server.set_flag(key="f", enabled=True)  # value omitted -> sentinel
    assert captured["value"] is server.flags.UNSET

    server.set_flag(key="f", value=None)  # explicit null -> None (clears env default)
    assert captured["value"] is None


def test_new_tool_missing_key_is_toolerror(monkeypatch):
    monkeypatch.setattr(server, "get_http_headers", lambda **kw: {})
    with pytest.raises(ToolError, match="Connect your smplkit API key"):
        server.list_flags()


def test_new_tool_api_error_is_friendly(monkeypatch):
    monkeypatch.setattr(server, "get_http_headers", lambda **kw: {"authorization": "Bearer k"})

    def boom(client, **kw):
        raise JobsApiError(402, [{"detail": "Free plan allows 5 flags."}])

    monkeypatch.setattr(server.flags, "create_flag", boom)
    with pytest.raises(ToolError) as exc:
        server.create_flag(key="f", type="boolean", default=False)
    assert "Upgrade your plan" in str(exc.value)


def test_new_tool_non_public_target_is_toolerror(monkeypatch):
    # Runs the real audit.create_forwarder, hitting the public-URL guard in _run.
    monkeypatch.setattr(server, "get_http_headers", lambda **kw: {"authorization": "Bearer k"})
    with pytest.raises(ToolError, match="public internet"):
        server.create_forwarder(name="x", url="http://localhost:9000/in")


# -- tool registration ------------------------------------------------------


JOBS_TOOLS = {
    "list_jobs", "get_job", "create_job", "update_job",
    "delete_job", "run_job", "list_runs", "get_run",
}
FLAGS_TOOLS = {"create_flag", "list_flags", "get_flag", "set_flag", "delete_flag"}
CONFIG_TOOLS = {"create_config", "list_configs", "get_config", "set_config_value",
                "delete_config"}
LOGGING_TOOLS = {"set_log_level", "list_loggers", "get_logger", "reset_logger"}
AUDIT_TOOLS = {"query_events", "get_event", "list_forwarders", "create_forwarder",
               "test_forwarder", "delete_forwarder"}
PLATFORM_TOOLS = {"list_environments"}

ALL_TOOLS = (
    JOBS_TOOLS | FLAGS_TOOLS | CONFIG_TOOLS | LOGGING_TOOLS | AUDIT_TOOLS | PLATFORM_TOOLS
)


def test_all_platform_tools_registered():
    registered = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in registered}
    # The original eight Jobs tools are unchanged and still present.
    assert JOBS_TOOLS <= names
    # Plus the full platform surface — 21 new tools, 29 total.
    assert names == ALL_TOOLS
    assert len(ALL_TOOLS) == 29


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
