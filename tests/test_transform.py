"""JSON:API envelope flattening and full-replace preparation."""
from __future__ import annotations

from smplkit_mcp.transform import attributes_for_replace, clean_job, clean_run


def _job_resource() -> dict:
    return {
        "id": "nightly",
        "type": "job",
        "attributes": {
            "name": "Nightly",
            "description": "warm cache",
            "kind": "recurring",
            "schedule": "0 2 * * *",
            "timezone": "America/New_York",
            "configuration": {
                "method": "POST",
                "url": "https://api.example.com/warm",
                "headers": {"Authorization": "Bearer x"},
                "body": "{}",
                "timeout": 30,
                "success_status": "2xx",
            },
            "retry_policy": "retry-5xx",
            "environments": {
                "production": {"enabled": True, "next_run_at": "2026-07-01T02:00:00Z"},
                "staging": {"enabled": False, "next_run_at": None,
                            "url": "https://staging.example.com/warm"},
            },
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-02T00:00:00Z",
            "deleted_at": None,
            "version": 3,
        },
    }


class TestCleanJob:
    def test_flattens_core_fields(self):
        job = clean_job(_job_resource())
        assert job["id"] == "nightly"
        assert job["kind"] == "recurring"
        assert job["schedule"] == "0 2 * * *"
        assert job["request"]["url"] == "https://api.example.com/warm"
        assert job["request"]["method"] == "POST"
        assert job["retry_policy"] == "retry-5xx"
        assert job["version"] == 3

    def test_environments_enabled_and_next_run(self):
        envs = clean_job(_job_resource())["environments"]
        assert envs["production"]["enabled"] is True
        assert envs["production"]["next_run_at"] == "2026-07-01T02:00:00Z"
        assert envs["staging"]["enabled"] is False
        assert envs["staging"]["overrides"] == {"url": "https://staging.example.com/warm"}
        assert "overrides" not in envs["production"]

    def test_handles_missing_configuration(self):
        job = clean_job({"id": "x", "attributes": {"name": "x"}})
        assert job["request"]["url"] is None


class TestCleanRun:
    def test_flattens_result_and_durations(self):
        run = clean_run({
            "id": "r1",
            "attributes": {
                "job": "nightly",
                "environment": "production",
                "trigger": "MANUAL",
                "status": "SUCCEEDED",
                "started_at": "2026-06-05T02:00:00.120Z",
                "finished_at": "2026-06-05T02:00:00.430Z",
                "pending_duration_ms": 120,
                "run_duration_ms": 310,
                "total_duration_ms": 430,
                "failure_reason": None,
                "error": None,
                "result": {"status": 200, "headers": {"content-type": "application/json"},
                           "body": '{"ok":true}', "body_truncated": False, "body_bytes": 11},
                "created_at": "2026-06-05T02:00:00Z",
            },
        })
        assert run["status"] == "SUCCEEDED"
        assert run["result"]["status"] == 200
        assert run["result"]["body_truncated"] is False
        assert run["durations_ms"]["total"] == 430

    def test_null_result(self):
        resource = {"id": "r", "attributes": {"job": "j", "status": "PENDING", "result": None}}
        assert clean_run(resource)["result"] is None


class TestAttributesForReplace:
    def test_strips_readonly_and_next_run(self):
        attrs = _job_resource()["attributes"]
        out = attributes_for_replace(attrs)
        for field in ("kind", "created_at", "updated_at", "deleted_at", "version"):
            assert field not in out
        assert "next_run_at" not in out["environments"]["production"]
        assert "next_run_at" not in out["environments"]["staging"]
        # writable fields survive
        assert out["name"] == "Nightly"
        assert out["environments"]["production"]["enabled"] is True
        assert out["environments"]["staging"]["url"] == "https://staging.example.com/warm"

    def test_without_environments(self):
        out = attributes_for_replace({"name": "x", "version": 2})
        assert out == {"name": "x"}
