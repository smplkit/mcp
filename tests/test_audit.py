"""Audit tool-logic + client tests."""
from __future__ import annotations

import json

import httpx
import pytest

from smplkit_mcp import audit
from smplkit_mcp.audit import AuditClient
from smplkit_mcp.errors import SmplkitApiError
from smplkit_mcp.urls import NonPublicTargetError


class FakeAuditClient:
    """In-memory stand-in for :class:`AuditClient`."""

    def __init__(self) -> None:
        self.events: dict[str, dict] = {}
        self.forwarders: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        # Canned result returned by ``test_forwarder``; tests may override.
        self.test_result: dict = {
            "succeeded": True,
            "response_status": 200,
            "latency_ms": 42,
        }

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    def calls_named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]

    # -- seeding helpers ----------------------------------------------------

    def seed_event(self, event_id: str, attributes: dict) -> None:
        self.events[event_id] = dict(attributes)

    # -- events -------------------------------------------------------------

    def list_events(self, params=None):
        self._record("list_events", params=params)
        data = [
            {"id": k, "type": "event", "attributes": a}
            for k, a in self.events.items()
        ]
        return {"data": data, "meta": {"pagination": {"page": 1, "size": len(data)}}}

    def get_event(self, event_id):
        self._record("get_event", event_id=event_id)
        if event_id not in self.events:
            raise SmplkitApiError(404, [{"detail": f"No event '{event_id}'."}])
        return {"data": {"id": event_id, "type": "event", "attributes": self.events[event_id]}}

    # -- forwarders ---------------------------------------------------------

    def list_forwarders(self, params=None):
        self._record("list_forwarders", params=params)
        data = [
            {"id": k, "type": "forwarder", "attributes": a}
            for k, a in self.forwarders.items()
        ]
        return {"data": data, "meta": {"pagination": {"page": 1, "size": len(data)}}}

    def create_forwarder(self, forwarder_id, attributes):
        self._record("create_forwarder", forwarder_id=forwarder_id, attributes=attributes)
        if forwarder_id in self.forwarders:
            raise SmplkitApiError(409, [{"detail": f"Forwarder '{forwarder_id}' exists."}])
        stored = dict(attributes)
        stored["version"] = 1
        stored["created_at"] = "2026-06-28T00:00:00Z"
        stored["updated_at"] = "2026-06-28T00:00:00Z"
        self.forwarders[forwarder_id] = stored
        return {"data": {"id": forwarder_id, "type": "forwarder", "attributes": stored}}

    def delete_forwarder(self, forwarder_id):
        self._record("delete_forwarder", forwarder_id=forwarder_id)
        self.forwarders.pop(forwarder_id, None)

    def test_forwarder(self, body):
        self._record("test_forwarder", body=body)
        return dict(self.test_result)


@pytest.fixture()
def fake() -> FakeAuditClient:
    return FakeAuditClient()


# -- query_events -----------------------------------------------------------


class TestQueryEvents:
    def test_all_filters_mapped(self, fake):
        audit.query_events(
            fake,
            actor_type="user",
            actor_id="u-1",
            event_type="login",
            resource_type="account",
            resource_id="acct-1",
            category="auth",
            severity="warn",
            environment="production",
            search="failed",
            limit=10,
        )
        params = fake.calls_named("list_events")[-1]["params"]
        assert params == {
            "filter[actor_type]": "user",
            "filter[actor_id]": "u-1",
            "filter[event_type]": "login",
            "filter[resource_type]": "account",
            "filter[resource_id]": "acct-1",
            "filter[category]": "auth",
            "filter[severity]": "WARN",
            "filter[environment]": "production",
            "filter[search]": "failed",
            "page[size]": 10,
        }

    def test_time_range_closed(self, fake):
        audit.query_events(fake, since="2026-05-01T00:00:00Z", until="2026-06-01T00:00:00Z")
        params = fake.calls_named("list_events")[-1]["params"]
        assert params["filter[occurred_at]"] == "[2026-05-01T00:00:00Z,2026-06-01T00:00:00Z)"

    def test_time_range_open_ended_since(self, fake):
        audit.query_events(fake, since="2026-05-01T00:00:00Z")
        params = fake.calls_named("list_events")[-1]["params"]
        assert params["filter[occurred_at]"] == "[2026-05-01T00:00:00Z,*)"

    def test_time_range_open_ended_until(self, fake):
        audit.query_events(fake, until="2026-06-01T00:00:00Z")
        params = fake.calls_named("list_events")[-1]["params"]
        assert params["filter[occurred_at]"] == "[*,2026-06-01T00:00:00Z)"

    def test_severity_normalized(self, fake):
        audit.query_events(fake, severity="error")
        params = fake.calls_named("list_events")[-1]["params"]
        assert params["filter[severity]"] == "ERROR"

    def test_invalid_severity_rejected(self, fake):
        with pytest.raises(ValueError, match="Unknown severity"):
            audit.query_events(fake, severity="critical")

    def test_no_filters_passes_none(self, fake):
        audit.query_events(fake, limit=None)
        assert fake.calls_named("list_events")[-1]["params"] is None

    def test_clean_shape_and_count(self, fake):
        fake.seed_event(
            "ev-1",
            {
                "event_type": "login",
                "resource_type": "account",
                "resource_id": "acct-1",
                "description": "User logged in",
                "severity": "INFO",
                "category": "auth",
                "occurred_at": "2026-06-01T00:00:00Z",
                "actor_type": "user",
                "actor_id": "u-1",
                "actor_label": "Alice",
                "environment": "production",
                "data": {"ip": "1.2.3.4"},
                "created_at": "2026-06-01T00:00:01Z",
                "idempotency_key": "k-1",
                "do_not_forward": False,
            },
        )
        out = audit.query_events(fake)
        assert out["count"] == 1
        event = out["events"][0]
        assert event["id"] == "ev-1"
        assert event["event_type"] == "login"
        assert event["severity"] == "INFO"
        assert event["actor_label"] == "Alice"
        assert event["data"] == {"ip": "1.2.3.4"}
        # Internal-only attributes are not surfaced.
        assert "idempotency_key" not in event
        assert "do_not_forward" not in event


# -- get_event --------------------------------------------------------------


def test_get_event_clean(fake):
    fake.seed_event("ev-1", {"event_type": "login", "severity": "INFO"})
    event = audit.get_event(fake, event_id="ev-1")
    assert event["id"] == "ev-1"
    assert event["event_type"] == "login"


def test_get_event_404(fake):
    with pytest.raises(SmplkitApiError) as exc:
        audit.get_event(fake, event_id="ghost")
    assert exc.value.status_code == 404


# -- list_forwarders --------------------------------------------------------


def test_list_forwarders_filters(fake):
    audit.list_forwarders(fake, forwarder_type="DATADOG", limit=5)
    params = fake.calls_named("list_forwarders")[-1]["params"]
    assert params == {"filter[forwarder_type]": "datadog", "page[size]": 5}


def test_list_forwarders_no_filters(fake):
    audit.list_forwarders(fake)
    assert fake.calls_named("list_forwarders")[-1]["params"] is None


def test_list_forwarders_clean_shape(fake):
    audit.create_forwarder(fake, name="My HTTP sink", url="https://hooks.example.com/audit")
    out = audit.list_forwarders(fake)
    assert out["count"] == 1
    fwd = out["forwarders"][0]
    assert fwd["forwarder_type"] == "http"
    assert fwd["configuration"]["url"] == "https://hooks.example.com/audit"
    assert fwd["version"] == 1


def test_list_forwarders_invalid_type_rejected(fake):
    with pytest.raises(ValueError, match="Unknown forwarder type"):
        audit.list_forwarders(fake, forwarder_type="bogus")


# -- create_forwarder -------------------------------------------------------


class TestCreateForwarder:
    def test_default_type_and_configuration(self, fake):
        fwd = audit.create_forwarder(
            fake,
            name="My HTTP sink",
            url="https://hooks.example.com/audit",
            method="PUT",
            headers={"Authorization": "Bearer x"},
            success_status="200",
        )
        attrs = fake.calls_named("create_forwarder")[0]["attributes"]
        assert attrs["forwarder_type"] == "http"
        assert attrs["configuration"] == {
            "url": "https://hooks.example.com/audit",
            "method": "PUT",
            "headers": {"Authorization": "Bearer x"},
            "success_status": "200",
        }
        assert attrs["environments"] == {"production": {"enabled": True}}
        # Optional attributes not provided are omitted entirely.
        assert "filter" not in attrs
        assert "description" not in attrs
        assert "forward_smplkit_events" not in attrs
        assert fwd["name"] == "My HTTP sink"

    def test_minimal_configuration(self, fake):
        audit.create_forwarder(fake, name="Sink", url="https://hooks.example.com/a")
        attrs = fake.calls_named("create_forwarder")[0]["attributes"]
        assert attrs["configuration"] == {
            "url": "https://hooks.example.com/a",
            "method": "POST",
        }

    def test_environment_and_enabled(self, fake):
        audit.create_forwarder(
            fake,
            name="Sink",
            url="https://hooks.example.com/a",
            environment="staging",
            enabled=False,
        )
        attrs = fake.calls_named("create_forwarder")[0]["attributes"]
        assert attrs["environments"] == {"staging": {"enabled": False}}

    def test_filter_description_forward_passthrough(self, fake):
        audit.create_forwarder(
            fake,
            name="Sink",
            url="https://hooks.example.com/a",
            filter={"==": [{"var": "severity"}, "ERROR"]},
            description="Only errors",
            forward_smplkit_events=True,
        )
        attrs = fake.calls_named("create_forwarder")[0]["attributes"]
        assert attrs["filter"] == {"==": [{"var": "severity"}, "ERROR"]}
        assert attrs["description"] == "Only errors"
        assert attrs["forward_smplkit_events"] is True

    def test_typed_forwarder_normalized(self, fake):
        audit.create_forwarder(
            fake, name="DD", url="https://http-intake.example.com", forwarder_type="Datadog"
        )
        attrs = fake.calls_named("create_forwarder")[0]["attributes"]
        assert attrs["forwarder_type"] == "datadog"

    def test_id_slug_from_name(self, fake):
        fwd = audit.create_forwarder(
            fake, name="My HTTP Sink!", url="https://hooks.example.com/a"
        )
        assert fake.calls_named("create_forwarder")[0]["forwarder_id"] == "my-http-sink"
        assert fwd["id"] == "my-http-sink"

    def test_explicit_forwarder_id(self, fake):
        audit.create_forwarder(
            fake,
            name="My Sink",
            url="https://hooks.example.com/a",
            forwarder_id="custom-id",
        )
        assert fake.calls_named("create_forwarder")[0]["forwarder_id"] == "custom-id"

    def test_invalid_forwarder_type_rejected(self, fake):
        with pytest.raises(ValueError, match="Unknown forwarder type"):
            audit.create_forwarder(
                fake, name="X", url="https://hooks.example.com/a", forwarder_type="kafka"
            )
        assert fake.calls_named("create_forwarder") == []

    def test_localhost_rejected_no_create(self, fake):
        with pytest.raises(NonPublicTargetError):
            audit.create_forwarder(fake, name="X", url="http://localhost:8080/a")
        assert fake.calls_named("create_forwarder") == []


# -- test_forwarder ---------------------------------------------------------


class TestTestForwarder:
    def test_minimal_payload(self, fake):
        audit.test_forwarder(fake, url="https://hooks.example.com/a")
        payload = fake.calls_named("test_forwarder")[0]["body"]
        assert payload == {"url": "https://hooks.example.com/a", "method": "POST"}

    def test_all_fields_payload(self, fake):
        audit.test_forwarder(
            fake,
            url="https://hooks.example.com/a",
            method="PUT",
            headers={"X-Token": "abc"},
            success_status="201",
            body='{"hello": "world"}',
            timeout_ms=5000,
            tls_verify=False,
            ca_cert="-----BEGIN CERT-----",
        )
        payload = fake.calls_named("test_forwarder")[0]["body"]
        assert payload == {
            "url": "https://hooks.example.com/a",
            "method": "PUT",
            "headers": {"X-Token": "abc"},
            "success_status": "201",
            "body": '{"hello": "world"}',
            "timeout_ms": 5000,
            "tls_verify": False,
            "ca_cert": "-----BEGIN CERT-----",
        }

    def test_clean_result_handles_missing_optional(self, fake):
        fake.test_result = {"succeeded": False, "error": "connection refused"}
        out = audit.test_forwarder(fake, url="https://hooks.example.com/a")
        assert out == {
            "succeeded": False,
            "response_status": None,
            "response_headers": None,
            "response_body": None,
            "latency_ms": None,
            "error": "connection refused",
        }

    def test_clean_result_full(self, fake):
        fake.test_result = {
            "succeeded": True,
            "response_status": 200,
            "response_headers": {"content-type": "application/json"},
            "response_body": "ok",
            "latency_ms": 33,
            "error": None,
        }
        out = audit.test_forwarder(fake, url="https://hooks.example.com/a")
        assert out["succeeded"] is True
        assert out["response_status"] == 200
        assert out["response_headers"] == {"content-type": "application/json"}
        assert out["latency_ms"] == 33

    def test_localhost_rejected_no_call(self, fake):
        with pytest.raises(NonPublicTargetError):
            audit.test_forwarder(fake, url="http://127.0.0.1:9000/a")
        assert fake.calls_named("test_forwarder") == []


# -- delete_forwarder -------------------------------------------------------


def test_delete_forwarder(fake):
    audit.create_forwarder(fake, name="Sink", url="https://hooks.example.com/a")
    assert audit.delete_forwarder(fake, forwarder_id="sink") == {"deleted": True, "id": "sink"}
    assert "sink" not in fake.forwarders


# -- AuditClient wire shape (MockTransport) ---------------------------------


def _client(handler) -> AuditClient:
    return AuditClient(
        "sk_test", "https://audit.example.com", transport=httpx.MockTransport(handler)
    )


def test_list_events_get_with_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["accept"] = request.headers.get("accept")
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json={"data": []})

    _client(handler).list_events({"filter[severity]": "ERROR", "page[size]": 10})
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/events"
    assert captured["accept"] == "application/vnd.api+json"
    assert captured["query"] == {"filter[severity]": "ERROR", "page[size]": "10"}


def test_get_event_wire():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/events/ev-1"
        return httpx.Response(200, json={"data": {"id": "ev-1", "type": "event", "attributes": {}}})

    body = _client(handler).get_event("ev-1")
    assert body["data"]["id"] == "ev-1"


def test_create_forwarder_envelope():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["ct"] = request.headers.get("content-type")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201, json={"data": {"id": "f1", "type": "forwarder", "attributes": {}}}
        )

    _client(handler).create_forwarder(
        "f1",
        {
            "name": "F",
            "forwarder_type": "http",
            "configuration": {"url": "https://x.example.com", "method": "POST"},
        },
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/forwarders"
    assert captured["ct"] == "application/vnd.api+json"
    assert captured["body"]["data"] == {
        "id": "f1",
        "type": "forwarder",
        "attributes": {
            "name": "F",
            "forwarder_type": "http",
            "configuration": {"url": "https://x.example.com", "method": "POST"},
        },
    }


def test_test_forwarder_uses_post_flat():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["ct"] = request.headers.get("content-type")
        captured["accept"] = request.headers.get("accept")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"succeeded": True, "response_status": 200, "latency_ms": 12}
        )

    result = _client(handler).test_forwarder(
        {"url": "https://x.example.com", "method": "POST"}
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/functions/test_forwarder/actions/execute"
    # Flat application/json, NOT a JSON:API media type, on both sides.
    assert captured["ct"] == "application/json"
    assert captured["accept"] == "application/json"
    assert captured["body"] == {"url": "https://x.example.com", "method": "POST"}
    # Response is the flat body, not unwrapped from a JSON:API envelope.
    assert result == {"succeeded": True, "response_status": 200, "latency_ms": 12}


def test_delete_forwarder_204_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/v1/forwarders/f1"
        return httpx.Response(204)

    assert _client(handler).delete_forwarder("f1") is None


def test_error_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"errors": [{"status": "402", "detail": "limit"}]})

    with pytest.raises(SmplkitApiError) as exc:
        _client(handler).list_forwarders()
    assert exc.value.status_code == 402
    assert exc.value.detail() == "limit"
