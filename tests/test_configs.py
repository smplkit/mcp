"""Config tool-logic + client tests."""
from __future__ import annotations

import json

import httpx
import pytest

from smplkit_mcp import configs
from smplkit_mcp.configs import ConfigClient
from smplkit_mcp.errors import SmplkitApiError


class FakeConfigClient:
    """In-memory stand-in for :class:`ConfigClient`."""

    def __init__(self) -> None:
        self.configs: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    def calls_named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]

    def _store(self, config_id: str, attributes: dict) -> dict:
        stored = dict(attributes)
        stored.setdefault("managed", True)
        stored["created_at"] = "2026-06-28T00:00:00Z"
        stored["updated_at"] = "2026-06-28T00:00:00Z"
        self.configs[config_id] = stored
        return stored

    def list_configs(self, params=None):
        self._record("list_configs", params=params)
        data = [{"id": k, "type": "config", "attributes": a} for k, a in self.configs.items()]
        return {"data": data, "meta": {"pagination": {"page": 1, "size": len(data)}}}

    def get_config(self, config_id):
        self._record("get_config", config_id=config_id)
        if config_id not in self.configs:
            raise SmplkitApiError(404, [{"detail": f"No config '{config_id}'."}])
        return {"data": {"id": config_id, "type": "config", "attributes": self.configs[config_id]}}

    def create_config(self, config_id, attributes):
        self._record("create_config", config_id=config_id, attributes=attributes)
        if config_id in self.configs:
            raise SmplkitApiError(409, [{"detail": f"Config '{config_id}' exists."}])
        stored = self._store(config_id, attributes)
        return {"data": {"id": config_id, "type": "config", "attributes": stored}}

    def replace_config(self, config_id, attributes):
        self._record("replace_config", config_id=config_id, attributes=attributes)
        if config_id not in self.configs:
            raise SmplkitApiError(404, [{"detail": f"No config '{config_id}'."}])
        stored = self._store(config_id, attributes)
        return {"data": {"id": config_id, "type": "config", "attributes": stored}}

    def delete_config(self, config_id):
        self._record("delete_config", config_id=config_id)
        self.configs.pop(config_id, None)


@pytest.fixture()
def fake() -> FakeConfigClient:
    return FakeConfigClient()


# -- create_config ----------------------------------------------------------


class TestCreateConfig:
    def test_minimal(self, fake):
        config = configs.create_config(fake, name="Service Config")
        assert config["id"] == "service-config"  # id slugged from name
        assert config["name"] == "Service Config"
        attrs = fake.calls_named("create_config")[0]["attributes"]
        assert attrs == {"name": "Service Config"}

    def test_explicit_config_id(self, fake):
        config = configs.create_config(fake, name="Service Config", config_id="svc")
        assert config["id"] == "svc"
        assert fake.calls_named("create_config")[0]["config_id"] == "svc"

    def test_description_and_parent(self, fake):
        configs.create_config(
            fake, name="Child", config_id="child",
            description="Inherits from base", parent="base",
        )
        attrs = fake.calls_named("create_config")[0]["attributes"]
        assert attrs["description"] == "Inherits from base"
        assert attrs["parent"] == "base"

    def test_items_bare_values_infer_type(self, fake):
        configs.create_config(
            fake, name="cfg", config_id="cfg",
            items={
                "host": "db-prod.internal",
                "port": 5432,
                "ratio": 1.5,
                "enabled": True,
                "tags": ["a", "b"],
                "nothing": None,
            },
        )
        items = fake.calls_named("create_config")[0]["attributes"]["items"]
        assert items["host"] == {"value": "db-prod.internal", "type": "STRING"}
        assert items["port"] == {"value": 5432, "type": "NUMBER"}
        assert items["ratio"] == {"value": 1.5, "type": "NUMBER"}
        assert items["enabled"] == {"value": True, "type": "BOOLEAN"}
        assert items["tags"] == {"value": ["a", "b"], "type": "JSON"}
        assert items["nothing"] == {"value": None, "type": "JSON"}

    def test_items_dict_form_infer_type(self, fake):
        configs.create_config(
            fake, name="cfg", config_id="cfg",
            items={"host": {"value": "db.internal", "description": "DB host"}},
        )
        items = fake.calls_named("create_config")[0]["attributes"]["items"]
        assert items["host"] == {
            "value": "db.internal", "type": "STRING", "description": "DB host",
        }

    def test_items_dict_form_explicit_type(self, fake):
        configs.create_config(
            fake, name="cfg", config_id="cfg",
            items={"count": {"value": 3, "type": "number"}},
        )
        items = fake.calls_named("create_config")[0]["attributes"]["items"]
        assert items["count"] == {"value": 3, "type": "NUMBER"}

    def test_items_unknown_explicit_type_rejected(self, fake):
        with pytest.raises(ValueError, match="Unknown config item type"):
            configs.create_config(
                fake, name="cfg", config_id="cfg",
                items={"x": {"value": 1, "type": "decimal"}},
            )

    def test_empty_items_not_sent(self, fake):
        configs.create_config(fake, name="cfg", config_id="cfg", items={})
        attrs = fake.calls_named("create_config")[0]["attributes"]
        assert "items" not in attrs


# -- set_config_value (GET-mutate-PUT) -------------------------------------


class TestSetConfigValue:
    def _seed(self, fake, items=None):
        configs.create_config(fake, name="cfg", config_id="cfg", items=items)

    def test_auto_declare_item_with_inferred_type(self, fake):
        self._seed(fake)
        config = configs.set_config_value(
            fake, config_id="cfg", key="port", value=8080,
        )
        # item auto-declared with base value None and inferred type
        assert config["items"]["port"] == {"value": None, "type": "NUMBER", "description": None}
        # GET then PUT
        assert [n for n, _ in fake.calls][-2:] == ["get_config", "replace_config"]

    def test_per_env_override_is_bare_value(self, fake):
        self._seed(fake)
        config = configs.set_config_value(
            fake, config_id="cfg", key="host", value="db-prod.internal",
            environment="production",
        )
        # override is the bare value, NOT wrapped in {"value": ...}
        assert config["environments"]["production"] == {"host": "db-prod.internal"}

    def test_default_environment_is_production(self, fake):
        self._seed(fake)
        config = configs.set_config_value(fake, config_id="cfg", key="host", value="x")
        assert config["environments"]["production"]["host"] == "x"

    def test_base_item_value_preserved(self, fake):
        self._seed(fake, items={"host": {"value": "db-default.internal"}})
        config = configs.set_config_value(
            fake, config_id="cfg", key="host", value="db-prod.internal",
            environment="production",
        )
        # existing item declaration (with its base value/type) is untouched
        assert config["items"]["host"] == {"value": "db-default.internal", "type": "STRING"}
        assert config["environments"]["production"]["host"] == "db-prod.internal"

    def test_other_items_and_envs_preserved_across_two_sets(self, fake):
        self._seed(fake)
        configs.set_config_value(
            fake, config_id="cfg", key="host", value="db-stg.internal",
            environment="staging",
        )
        config = configs.set_config_value(
            fake, config_id="cfg", key="port", value=5432,
            environment="production",
        )
        # both items declared
        assert set(config["items"]) == {"host", "port"}
        # both environments preserved with their own override
        assert config["environments"]["staging"] == {"host": "db-stg.internal"}
        assert config["environments"]["production"] == {"port": 5432}

    def test_readonly_stripped_before_put(self, fake):
        self._seed(fake)
        configs.set_config_value(fake, config_id="cfg", key="host", value="x")
        put_attrs = fake.calls_named("replace_config")[0]["attributes"]
        for field in ("created_at", "updated_at"):
            assert field not in put_attrs

    def test_missing_config_404s(self, fake):
        with pytest.raises(SmplkitApiError) as exc:
            configs.set_config_value(fake, config_id="ghost", key="x", value=1)
        assert exc.value.status_code == 404


# -- list / get / delete ----------------------------------------------------


def test_list_configs_filters(fake):
    configs.list_configs(fake, parent="base", search="db", managed=True, limit=10)
    params = fake.calls_named("list_configs")[-1]["params"]
    assert params == {
        "filter[parent]": "base", "filter[search]": "db",
        "filter[managed]": True, "page[size]": 10,
    }


def test_list_configs_no_filters(fake):
    configs.list_configs(fake)
    assert fake.calls_named("list_configs")[-1]["params"] is None


def test_list_configs_clean_shape(fake):
    configs.create_config(fake, name="a", config_id="a")
    out = configs.list_configs(fake)
    assert out["count"] == 1
    cfg = out["configs"][0]
    assert cfg["id"] == "a"
    assert set(cfg) == {
        "id", "name", "description", "parent", "items",
        "environments", "managed", "created_at", "updated_at",
    }


def test_get_config(fake):
    configs.create_config(fake, name="a", config_id="a")
    assert configs.get_config(fake, config_id="a")["id"] == "a"


def test_delete_config(fake):
    configs.create_config(fake, name="a", config_id="a")
    assert configs.delete_config(fake, config_id="a") == {"deleted": True, "id": "a"}
    assert "a" not in fake.configs


# -- pure transforms --------------------------------------------------------


def test_infer_item_type():
    assert configs._infer_item_type(True) == "BOOLEAN"
    assert configs._infer_item_type(False) == "BOOLEAN"
    assert configs._infer_item_type(3) == "NUMBER"
    assert configs._infer_item_type(2.5) == "NUMBER"
    assert configs._infer_item_type("x") == "STRING"
    assert configs._infer_item_type({"a": 1}) == "JSON"
    assert configs._infer_item_type([1, 2]) == "JSON"
    assert configs._infer_item_type(None) == "JSON"


def test_slugify_fallback():
    assert configs._slugify("!!!") == "config"
    assert configs._slugify("My Config!") == "my-config"


def test_clean_config_handles_missing_attributes():
    assert configs.clean_config({"id": "a"})["name"] is None


# -- ConfigClient wire shape (MockTransport) -------------------------------


def _client(handler) -> ConfigClient:
    return ConfigClient("sk_test", "https://config.example.com",
                        transport=httpx.MockTransport(handler))


def test_create_wraps_envelope():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["ct"] = request.headers.get("content-type")
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "c1", "type": "config", "attributes": {}}})

    _client(handler).create_config("c1", {"name": "C"})
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/configs"
    assert captured["ct"] == "application/vnd.api+json"
    assert captured["body"]["data"] == {
        "id": "c1", "type": "config", "attributes": {"name": "C"},
    }


def test_list_passes_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json={"data": []})

    _client(handler).list_configs({"filter[parent]": "base", "page[size]": 5})
    assert captured["method"] == "GET"
    assert captured["query"] == {"filter[parent]": "base", "page[size]": "5"}


def test_replace_uses_put_with_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "c1", "attributes": {}}})

    _client(handler).replace_config("c1", {"name": "C"})
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/v1/configs/c1"
    assert captured["body"]["data"]["id"] == "c1"
    assert captured["body"]["data"]["type"] == "config"


def test_get_one():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/configs/c1"
        return httpx.Response(200, json={"data": {"id": "c1", "type": "config", "attributes": {}}})

    assert _client(handler).get_config("c1")["data"]["id"] == "c1"


def test_delete_204_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/v1/configs/c1"
        return httpx.Response(204)

    assert _client(handler).delete_config("c1") is None


def test_error_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"errors": [{"status": "402", "detail": "limit"}]})

    with pytest.raises(SmplkitApiError) as exc:
        _client(handler).get_config("c1")
    assert exc.value.status_code == 402
    assert exc.value.detail() == "limit"
