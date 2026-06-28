"""Environments tool-logic + client tests."""
from __future__ import annotations

import httpx
import pytest

from smplkit_mcp import environments
from smplkit_mcp.environments import EnvironmentsClient
from smplkit_mcp.errors import SmplkitApiError


class FakeEnvironmentsClient:
    """In-memory stand-in for :class:`EnvironmentsClient`."""

    def __init__(self) -> None:
        self.environments: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    def calls_named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]

    def _store(self, key: str, attributes: dict) -> dict:
        stored = dict(attributes)
        stored.setdefault("classification", "STANDARD")
        stored.setdefault("managed", False)
        stored.setdefault("color", None)
        stored["created_at"] = "2026-06-28T00:00:00Z"
        stored["updated_at"] = "2026-06-28T00:00:00Z"
        self.environments[key] = stored
        return stored

    def list_environments(self, params=None):
        self._record("list_environments", params=params)
        data = [
            {"id": k, "type": "environment", "attributes": a}
            for k, a in self.environments.items()
        ]
        return {"data": data, "meta": {"pagination": {"page": 1, "size": len(data)}}}


@pytest.fixture()
def fake() -> FakeEnvironmentsClient:
    return FakeEnvironmentsClient()


# -- list_environments ------------------------------------------------------


def test_list_environments_no_filters_params_none(fake):
    environments.list_environments(fake)
    assert fake.calls_named("list_environments")[-1]["params"] is None


def test_list_environments_all_filters_mapped(fake):
    environments.list_environments(
        fake, classification="AD_HOC", managed=True, search="prev", limit=25
    )
    params = fake.calls_named("list_environments")[-1]["params"]
    assert params == {
        "filter[classification]": "AD_HOC",
        "filter[managed]": True,
        "filter[search]": "prev",
        "page[size]": 25,
    }


def test_list_environments_classification_passthrough(fake):
    environments.list_environments(fake, classification="STANDARD")
    assert fake.calls_named("list_environments")[-1]["params"] == {
        "filter[classification]": "STANDARD"
    }


def test_list_environments_managed_false_included(fake):
    environments.list_environments(fake, managed=False)
    # managed is filtered on `is not None`, so False must still be sent.
    assert fake.calls_named("list_environments")[-1]["params"] == {"filter[managed]": False}


def test_list_environments_search_passthrough(fake):
    environments.list_environments(fake, search="staging")
    assert fake.calls_named("list_environments")[-1]["params"] == {"filter[search]": "staging"}


def test_list_environments_limit_passthrough(fake):
    environments.list_environments(fake, limit=5)
    assert fake.calls_named("list_environments")[-1]["params"] == {"page[size]": 5}


def test_list_environments_clean_shape(fake):
    fake._store(
        "production",
        {"name": "Production", "classification": "STANDARD", "managed": True, "color": "#f00"},
    )
    out = environments.list_environments(fake)
    assert out["count"] == 1
    env = out["environments"][0]
    assert env == {
        "key": "production",  # key comes from id
        "name": "Production",
        "classification": "STANDARD",
        "managed": True,
        "color": "#f00",
        "created_at": "2026-06-28T00:00:00Z",
        "updated_at": "2026-06-28T00:00:00Z",
    }


def test_list_environments_empty_count_zero(fake):
    out = environments.list_environments(fake)
    assert out == {"environments": [], "count": 0}


def test_list_environments_multiple(fake):
    fake._store("production", {"name": "Production"})
    fake._store("staging", {"name": "Staging"})
    out = environments.list_environments(fake)
    assert out["count"] == 2
    assert {e["key"] for e in out["environments"]} == {"production", "staging"}


def test_list_environments_handles_missing_data_key():
    class NoDataClient:
        def list_environments(self, params=None):
            return {"meta": {}}

    out = environments.list_environments(NoDataClient())
    assert out == {"environments": [], "count": 0}


# -- clean_environment ------------------------------------------------------


def test_clean_environment_key_from_id():
    cleaned = environments.clean_environment(
        {"id": "staging", "type": "environment", "attributes": {"name": "Staging"}}
    )
    assert cleaned["key"] == "staging"
    assert cleaned["name"] == "Staging"


def test_clean_environment_missing_attributes():
    cleaned = environments.clean_environment({"id": "preview"})
    assert cleaned == {
        "key": "preview",
        "name": None,
        "classification": None,
        "managed": None,
        "color": None,
        "created_at": None,
        "updated_at": None,
    }


# -- EnvironmentsClient wire shape (MockTransport) --------------------------


def _client(handler) -> EnvironmentsClient:
    return EnvironmentsClient(
        "sk_test", "https://app.example.com", transport=httpx.MockTransport(handler)
    )


def test_list_environments_wire():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["accept"] = request.headers.get("accept")
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "production",
                        "type": "environment",
                        "attributes": {"name": "Production", "classification": "STANDARD"},
                    }
                ],
                "meta": {},
            },
        )

    body = _client(handler).list_environments()
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/environments"
    assert captured["accept"] == "application/vnd.api+json"
    assert captured["auth"] == "Bearer sk_test"
    assert body["data"][0]["id"] == "production"


def test_list_environments_wire_passes_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [], "meta": {}})

    _client(handler).list_environments({"filter[classification]": "AD_HOC", "page[size]": "10"})
    assert captured["query"] == {"filter[classification]": "AD_HOC", "page[size]": "10"}


def test_list_environments_error_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"status": "401", "detail": "bad key"}]})

    with pytest.raises(SmplkitApiError) as exc:
        _client(handler).list_environments()
    assert exc.value.status_code == 401
    assert exc.value.detail() == "bad key"
