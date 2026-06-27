"""JobsClient request building and JSON:API envelope/error handling."""
from __future__ import annotations

import json

import httpx
import pytest

from smplkit_mcp.errors import JobsApiError
from smplkit_mcp.jobs_client import JobsClient


def _client(handler) -> JobsClient:
    return JobsClient("sk_api_test", "https://jobs.example.com",
                      transport=httpx.MockTransport(handler))


def test_get_sets_bearer_and_accept():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["accept"] = request.headers.get("accept")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"data": []})

    body = _client(handler).list_jobs(params={"filter[name]": "x"})
    assert captured["auth"] == "Bearer sk_api_test"
    assert captured["accept"] == "application/vnd.api+json"
    assert "filter%5Bname%5D=x" in captured["url"] or "filter[name]=x" in captured["url"]
    assert body == {"data": []}


def test_create_wraps_jsonapi_envelope():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "j1", "type": "job", "attributes": {}}})

    _client(handler).create_job("j1", {"name": "J", "configuration": {"url": "https://x"}})
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/jobs"
    assert captured["content_type"] == "application/vnd.api+json"
    assert captured["body"] == {
        "data": {"id": "j1", "type": "job",
                 "attributes": {"name": "J", "configuration": {"url": "https://x"}}}
    }


def test_replace_uses_put():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(200, json={"data": {"id": "j1", "attributes": {}}})

    _client(handler).replace_job("j1", {"name": "J2"})
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/v1/jobs/j1"


def test_delete_204_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    assert _client(handler).delete_job("j1") is None


def test_run_job_omits_body_when_no_environment():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "r1", "attributes": {}}})

    _client(handler).run_job("j1")
    assert captured["body"] == {}


def test_run_job_includes_environment():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "r1", "attributes": {}}})

    _client(handler).run_job("j1", environment="staging")
    assert captured["body"] == {"environment": "staging"}


def test_error_response_parsed_into_jobs_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"errors": [
            {"status": "402", "title": "Payment Required", "detail": "limit reached"}]})

    with pytest.raises(JobsApiError) as exc:
        _client(handler).get_job("j1")
    assert exc.value.status_code == 402
    assert exc.value.detail() == "limit reached"


def test_error_with_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream boom")

    with pytest.raises(JobsApiError) as exc:
        _client(handler).list_runs()
    assert exc.value.status_code == 500
    assert exc.value.errors == []


def test_empty_2xx_body_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    assert _client(handler).list_jobs() is None
