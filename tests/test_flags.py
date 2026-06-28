"""Flags tool-logic + client tests."""
from __future__ import annotations

import json

import httpx
import pytest

from smplkit_mcp import flags
from smplkit_mcp.errors import SmplkitApiError
from smplkit_mcp.flags import FlagsClient


class FakeFlagsClient:
    """In-memory stand-in for :class:`FlagsClient`."""

    def __init__(self) -> None:
        self.flags: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    def calls_named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]

    def _store(self, key: str, attributes: dict) -> dict:
        stored = dict(attributes)
        stored.setdefault("managed", True)
        stored["created_at"] = "2026-06-28T00:00:00Z"
        stored["updated_at"] = "2026-06-28T00:00:00Z"
        stored["sources"] = None
        self.flags[key] = stored
        return stored

    def list_flags(self, params=None):
        self._record("list_flags", params=params)
        data = [{"id": k, "type": "flag", "attributes": a} for k, a in self.flags.items()]
        return {"data": data, "meta": {"pagination": {"page": 1, "size": len(data)}}}

    def get_flag(self, key):
        self._record("get_flag", key=key)
        if key not in self.flags:
            raise SmplkitApiError(404, [{"detail": f"No flag '{key}'."}])
        return {"data": {"id": key, "type": "flag", "attributes": self.flags[key]}}

    def create_flag(self, key, attributes):
        self._record("create_flag", key=key, attributes=attributes)
        if key in self.flags:
            raise SmplkitApiError(409, [{"detail": f"Flag '{key}' exists."}])
        stored = self._store(key, attributes)
        return {"data": {"id": key, "type": "flag", "attributes": stored}}

    def replace_flag(self, key, attributes):
        self._record("replace_flag", key=key, attributes=attributes)
        if key not in self.flags:
            raise SmplkitApiError(404, [{"detail": f"No flag '{key}'."}])
        stored = self._store(key, attributes)
        return {"data": {"id": key, "type": "flag", "attributes": stored}}

    def delete_flag(self, key):
        self._record("delete_flag", key=key)
        self.flags.pop(key, None)


@pytest.fixture()
def fake() -> FakeFlagsClient:
    return FakeFlagsClient()


# -- create_flag ------------------------------------------------------------


class TestCreateFlag:
    def test_boolean_minimal(self, fake):
        flag = flags.create_flag(fake, key="dark-mode", type="boolean", default=False)
        assert flag["id"] == "dark-mode"
        assert flag["type"] == "BOOLEAN"
        assert flag["default"] is False
        assert flag["name"] == "dark-mode"  # name defaults to key

    def test_type_aliases(self, fake):
        assert flags.create_flag(fake, key="a", type="number", default=1)["type"] == "NUMERIC"
        assert flags.create_flag(fake, key="b", type="STRING", default="x")["type"] == "STRING"
        assert flags.create_flag(fake, key="c", type="json", default={})["type"] == "JSON"

    def test_unknown_type_rejected(self, fake):
        with pytest.raises(ValueError, match="Unknown flag type"):
            flags.create_flag(fake, key="x", type="enum", default="a")

    def test_constrained_values_scalars(self, fake):
        flag = flags.create_flag(
            fake, key="theme", type="string", default="classic",
            values=["classic", "modern"], name="Theme", description="UI theme",
        )
        attrs = fake.calls_named("create_flag")[0]["attributes"]
        assert attrs["values"] == [
            {"name": "classic", "value": "classic"},
            {"name": "modern", "value": "modern"},
        ]
        assert attrs["description"] == "UI theme"
        assert flag["name"] == "Theme"

    def test_constrained_values_dicts(self, fake):
        flags.create_flag(
            fake, key="theme", type="string", default="c",
            values=[{"name": "Classic", "value": "c"}, {"value": "m"}],
        )
        attrs = fake.calls_named("create_flag")[0]["attributes"]
        assert attrs["values"] == [
            {"name": "Classic", "value": "c"},
            {"name": "m", "value": "m"},
        ]


# -- set_flag (GET-mutate-PUT) ---------------------------------------------


class TestSetFlag:
    def _seed(self, fake):
        flags.create_flag(fake, key="dark", type="boolean", default=False)

    def test_enable_in_environment(self, fake):
        self._seed(fake)
        flag = flags.set_flag(fake, key="dark", environment="production", value=True, enabled=True)
        env = flag["environments"]["production"]
        assert env["enabled"] is True
        assert env["default"] is True
        # GET then PUT
        assert [n for n, _ in fake.calls][-2:] == ["get_flag", "replace_flag"]

    def test_readonly_stripped_before_put(self, fake):
        self._seed(fake)
        flags.set_flag(fake, key="dark", value=True)
        put_attrs = fake.calls_named("replace_flag")[0]["attributes"]
        for field in ("sources", "created_at", "updated_at"):
            assert field not in put_attrs

    def test_other_environments_preserved(self, fake):
        self._seed(fake)
        flags.set_flag(fake, key="dark", environment="staging", value=True)
        flag = flags.set_flag(fake, key="dark", environment="production", value=False)
        assert flag["environments"]["staging"]["default"] is True
        assert flag["environments"]["production"]["default"] is False

    def test_disable_kill_switch(self, fake):
        self._seed(fake)
        flag = flags.set_flag(fake, key="dark", enabled=False)
        assert flag["environments"]["production"]["enabled"] is False

    def test_targeting_rule_single_condition(self, fake):
        self._seed(fake)
        flag = flags.set_flag(
            fake, key="dark", environment="staging",
            rules=[{"when": [{"attribute": "user.plan", "operator": "==", "value": "enterprise"}],
                    "serve": True, "description": "Enterprise"}],
        )
        rule = flag["environments"]["staging"]["rules"][0]
        assert rule["logic"] == {"==": [{"var": "user.plan"}, "enterprise"]}
        assert rule["value"] is True
        assert rule["description"] == "Enterprise"

    def test_targeting_rule_multi_condition_anded(self, fake):
        self._seed(fake)
        flag = flags.set_flag(
            fake, key="dark",
            rules=[{"when": [
                {"attribute": "user.plan", "operator": "==", "value": "enterprise"},
                {"attribute": "account.region", "operator": "in", "value": ["us", "ca"]},
            ], "serve": True}],
        )
        logic = flag["environments"]["production"]["rules"][0]["logic"]
        assert logic == {"and": [
            {"==": [{"var": "user.plan"}, "enterprise"]},
            {"in": [{"var": "account.region"}, ["us", "ca"]]},
        ]}

    def test_contains_reverses_operands(self, fake):
        self._seed(fake)
        flag = flags.set_flag(
            fake, key="dark",
            rules=[{"when": [{"attribute": "user.tags", "operator": "contains", "value": "vip"}],
                    "serve": True}],
        )
        logic = flag["environments"]["production"]["rules"][0]["logic"]
        assert logic == {"in": ["vip", {"var": "user.tags"}]}

    def test_rule_missing_serve_rejected(self, fake):
        self._seed(fake)
        with pytest.raises(ValueError, match="serve"):
            flags.set_flag(fake, key="dark", rules=[{"when": [
                {"attribute": "a", "operator": "==", "value": 1}]}])

    def test_rule_bad_operator_rejected(self, fake):
        self._seed(fake)
        with pytest.raises(ValueError, match="operator"):
            flags.set_flag(fake, key="dark", rules=[{"when": [
                {"attribute": "a", "operator": "~=", "value": 1}], "serve": True}])

    def test_rule_no_conditions_rejected(self, fake):
        self._seed(fake)
        with pytest.raises(ValueError, match="when"):
            flags.set_flag(fake, key="dark", rules=[{"when": [], "serve": True}])

    def test_rule_malformed_condition_rejected(self, fake):
        self._seed(fake)
        with pytest.raises(ValueError, match="attribute"):
            flags.set_flag(fake, key="dark", rules=[{"when": [{"operator": "=="}], "serve": True}])

    def test_clear_rules_with_empty_list(self, fake):
        self._seed(fake)
        flags.set_flag(fake, key="dark", rules=[{"when": [
            {"attribute": "a", "operator": "==", "value": 1}], "serve": True}])
        flag = flags.set_flag(fake, key="dark", rules=[])
        assert flag["environments"]["production"]["rules"] == []

    def test_value_omitted_leaves_default_unchanged(self, fake):
        self._seed(fake)
        flags.set_flag(fake, key="dark", environment="production", value=True)
        # A later set that only flips the kill switch must not touch the default.
        flag = flags.set_flag(fake, key="dark", environment="production", enabled=False)
        assert flag["environments"]["production"]["default"] is True
        assert flag["environments"]["production"]["enabled"] is False

    def test_value_none_clears_default_to_null(self, fake):
        self._seed(fake)
        flags.set_flag(fake, key="dark", environment="production", value=True)
        # Explicit None is distinct from "unchanged": it sets the env default to null.
        flag = flags.set_flag(fake, key="dark", environment="production", value=None)
        assert flag["environments"]["production"]["default"] is None
        put_attrs = fake.calls_named("replace_flag")[-1]["attributes"]
        assert put_attrs["environments"]["production"]["default"] is None

    def test_missing_flag_404s(self, fake):
        with pytest.raises(SmplkitApiError) as exc:
            flags.set_flag(fake, key="ghost", value=True)
        assert exc.value.status_code == 404


# -- list / get / delete ----------------------------------------------------


def test_list_flags_filters(fake):
    flags.list_flags(fake, type="boolean", search="dark", managed=True, limit=10)
    params = fake.calls_named("list_flags")[-1]["params"]
    assert params == {
        "filter[type]": "BOOLEAN", "filter[search]": "dark",
        "filter[managed]": True, "page[size]": 10,
    }


def test_list_flags_clean_shape(fake):
    flags.create_flag(fake, key="a", type="boolean", default=False)
    out = flags.list_flags(fake)
    assert out["count"] == 1
    assert out["flags"][0]["id"] == "a"
    assert "sources" not in out["flags"][0]


def test_get_flag(fake):
    flags.create_flag(fake, key="a", type="boolean", default=True)
    assert flags.get_flag(fake, key="a")["id"] == "a"


def test_delete_flag(fake):
    flags.create_flag(fake, key="a", type="boolean", default=True)
    assert flags.delete_flag(fake, key="a") == {"deleted": True, "id": "a"}
    assert "a" not in fake.flags


# -- FlagsClient wire shape (MockTransport) --------------------------------


def _client(handler) -> FlagsClient:
    return FlagsClient("sk_test", "https://flags.example.com",
                       transport=httpx.MockTransport(handler))


def test_create_wraps_envelope():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["ct"] = request.headers.get("content-type")
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "f1", "type": "flag", "attributes": {}}})

    _client(handler).create_flag("f1", {"name": "F", "type": "BOOLEAN", "default": False})
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/flags"
    assert captured["ct"] == "application/vnd.api+json"
    assert captured["body"]["data"] == {
        "id": "f1", "type": "flag",
        "attributes": {"name": "F", "type": "BOOLEAN", "default": False},
    }


def test_replace_uses_put_with_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "f1", "attributes": {}}})

    _client(handler).replace_flag("f1", {"name": "F"})
    assert captured["method"] == "PUT"
    assert captured["body"]["data"]["id"] == "f1"
    assert captured["body"]["data"]["type"] == "flag"


def test_delete_204_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    assert _client(handler).delete_flag("f1") is None


def test_error_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"errors": [{"status": "402", "detail": "limit"}]})

    with pytest.raises(SmplkitApiError) as exc:
        _client(handler).get_flag("f1")
    assert exc.value.status_code == 402
    assert exc.value.detail() == "limit"
