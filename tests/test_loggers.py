"""Logging tool-logic + client tests."""
from __future__ import annotations

import json

import httpx
import pytest

from smplkit_mcp import loggers
from smplkit_mcp.errors import SmplkitApiError
from smplkit_mcp.loggers import LoggerClient


class FakeLoggerClient:
    """In-memory stand-in for :class:`LoggerClient`."""

    def __init__(self) -> None:
        self.loggers: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    def calls_named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]

    def _store(self, logger_id: str, attributes: dict) -> dict:
        stored = dict(attributes)
        stored.setdefault("managed", True)
        stored["created_at"] = "2026-06-28T00:00:00Z"
        stored["updated_at"] = "2026-06-28T00:00:00Z"
        stored["sources"] = ["sqlalchemy"]
        stored["effective_levels"] = {"production": stored.get("level") or "INFO"}
        self.loggers[logger_id] = stored
        return stored

    def list_loggers(self, params=None):
        self._record("list_loggers", params=params)
        data = [
            {"id": k, "type": "logger", "attributes": a} for k, a in self.loggers.items()
        ]
        return {"data": data, "meta": {"pagination": {"page": 1, "size": len(data)}}}

    def get_logger(self, logger_id):
        self._record("get_logger", logger_id=logger_id)
        if logger_id not in self.loggers:
            raise SmplkitApiError(404, [{"detail": f"No logger '{logger_id}'."}])
        return {"data": {"id": logger_id, "type": "logger", "attributes": self.loggers[logger_id]}}

    def replace_logger(self, logger_id, attributes):
        self._record("replace_logger", logger_id=logger_id, attributes=attributes)
        # PUT upserts: creates if absent, replaces if present.
        stored = self._store(logger_id, attributes)
        return {"data": {"id": logger_id, "type": "logger", "attributes": stored}}

    def delete_logger(self, logger_id):
        self._record("delete_logger", logger_id=logger_id)
        self.loggers.pop(logger_id, None)


@pytest.fixture()
def fake() -> FakeLoggerClient:
    return FakeLoggerClient()


# -- set_log_level on an existing logger (GET-mutate-PUT) -------------------


class TestSetLogLevelExisting:
    def _seed(self, fake):
        # PUT-upsert the logger into existence first.
        loggers.set_log_level(fake, logger_id="sqlalchemy.engine", level="INFO")
        fake.calls.clear()

    def test_sets_environment_level_as_object(self, fake):
        self._seed(fake)
        logger = loggers.set_log_level(
            fake, logger_id="sqlalchemy.engine", level="DEBUG", environment="production"
        )
        # The per-env value is an OBJECT {"level": ...}, not a bare string.
        assert logger["environments"]["production"] == {"level": "DEBUG"}

    def test_get_then_put_order(self, fake):
        self._seed(fake)
        loggers.set_log_level(fake, logger_id="sqlalchemy.engine", level="DEBUG")
        assert [n for n, _ in fake.calls][-2:] == ["get_logger", "replace_logger"]

    def test_other_environments_preserved(self, fake):
        self._seed(fake)
        loggers.set_log_level(
            fake, logger_id="sqlalchemy.engine", level="DEBUG", environment="staging"
        )
        logger = loggers.set_log_level(
            fake, logger_id="sqlalchemy.engine", level="ERROR", environment="production"
        )
        assert logger["environments"]["staging"] == {"level": "DEBUG"}
        assert logger["environments"]["production"] == {"level": "ERROR"}

    def test_readonly_stripped_before_put(self, fake):
        self._seed(fake)
        loggers.set_log_level(fake, logger_id="sqlalchemy.engine", level="WARN")
        put_attrs = fake.calls_named("replace_logger")[0]["attributes"]
        for field in ("sources", "effective_levels", "created_at", "updated_at"):
            assert field not in put_attrs

    def test_invalid_level_rejected(self, fake):
        with pytest.raises(ValueError, match="Unknown log level"):
            loggers.set_log_level(fake, logger_id="x", level="WARNING")

    def test_level_case_insensitive(self, fake):
        self._seed(fake)
        logger = loggers.set_log_level(fake, logger_id="sqlalchemy.engine", level="debug")
        assert logger["environments"]["production"] == {"level": "DEBUG"}


# -- set_log_level UPSERT (logger does not exist) --------------------------


class TestSetLogLevelUpsert:
    def test_creates_when_absent(self, fake):
        logger = loggers.set_log_level(
            fake, logger_id="brand.new", level="trace", environment="staging"
        )
        assert logger["id"] == "brand.new"
        assert logger["name"] == "brand.new"
        assert logger["environments"]["staging"] == {"level": "TRACE"}

    def test_one_get_then_replace(self, fake):
        loggers.set_log_level(fake, logger_id="brand.new", level="INFO")
        # Exactly one GET (which 404s) followed by the upserting PUT.
        assert [n for n, _ in fake.calls] == ["get_logger", "replace_logger"]
        assert len(fake.calls_named("get_logger")) == 1

    def test_replace_body_has_name_and_level(self, fake):
        loggers.set_log_level(fake, logger_id="brand.new", level="INFO", environment="dev")
        attrs = fake.calls_named("replace_logger")[0]["attributes"]
        assert attrs["name"] == "brand.new"
        assert attrs["environments"] == {"dev": {"level": "INFO"}}

    def test_non_404_error_reraised(self, fake):
        def boom(logger_id):
            raise SmplkitApiError(403, [{"detail": "forbidden"}])

        fake.get_logger = boom
        with pytest.raises(SmplkitApiError) as exc:
            loggers.set_log_level(fake, logger_id="nope", level="INFO")
        assert exc.value.status_code == 403


# -- list / get / reset -----------------------------------------------------


def test_list_loggers_filters(fake):
    loggers.list_loggers(fake, managed=True, service="config", search="sql", limit=10)
    params = fake.calls_named("list_loggers")[-1]["params"]
    assert params == {
        "filter[managed]": True,
        "filter[service]": "config",
        "filter[search]": "sql",
        "page[size]": 10,
    }


def test_list_loggers_no_filters_passes_none(fake):
    loggers.list_loggers(fake)
    assert fake.calls_named("list_loggers")[-1]["params"] is None


def test_list_loggers_clean_shape(fake):
    loggers.set_log_level(fake, logger_id="a", level="INFO")
    out = loggers.list_loggers(fake)
    assert out["count"] == 1
    logger = out["loggers"][0]
    assert logger["id"] == "a"
    assert "sources" not in logger
    assert "effective_levels" in logger


def test_get_logger(fake):
    loggers.set_log_level(fake, logger_id="a", level="INFO")
    assert loggers.get_logger(fake, logger_id="a")["id"] == "a"


def test_reset_logger(fake):
    loggers.set_log_level(fake, logger_id="a", level="INFO")
    assert loggers.reset_logger(fake, logger_id="a") == {"deleted": True, "id": "a"}
    assert "a" not in fake.loggers
    assert fake.calls_named("delete_logger") == [{"logger_id": "a"}]


# -- LoggerClient wire shape (MockTransport) -------------------------------


def _client(handler) -> LoggerClient:
    return LoggerClient(
        "sk_test", "https://logging.example.com", transport=httpx.MockTransport(handler)
    )


def test_replace_uses_put_with_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["ct"] = request.headers.get("content-type")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "sqlalchemy.engine", "attributes": {}}})

    _client(handler).replace_logger(
        "sqlalchemy.engine", {"name": "sqlalchemy.engine", "environments": {}}
    )
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/v1/loggers/sqlalchemy.engine"
    assert captured["ct"] == "application/vnd.api+json"
    assert captured["body"]["data"]["id"] == "sqlalchemy.engine"
    assert captured["body"]["data"]["type"] == "logger"


def test_list_uses_get():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(200, json={"data": []})

    _client(handler).list_loggers({"filter[managed]": True})
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/loggers"


def test_delete_204_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/v1/loggers/sqlalchemy.engine"
        return httpx.Response(204)

    assert _client(handler).delete_logger("sqlalchemy.engine") is None


def test_error_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"status": "404", "detail": "no logger"}]})

    with pytest.raises(SmplkitApiError) as exc:
        _client(handler).get_logger("ghost")
    assert exc.value.status_code == 404
    assert exc.value.detail() == "no logger"
