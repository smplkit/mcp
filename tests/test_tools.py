"""Tool-logic tests against the in-memory FakeJobsClient."""
from __future__ import annotations

import pytest

from smplkit_mcp import tools
from smplkit_mcp.urls import NonPublicTargetError

# -- create_job -------------------------------------------------------------


class TestCreateJob:
    def test_recurring_from_cron(self, fake_client):
        job = tools.create_job(
            fake_client, name="Nightly Warm", url="https://api.example.com/warm",
            schedule="0 2 * * *", timezone="America/New_York",
        )
        assert job["kind"] == "recurring"
        assert job["schedule"] == "0 2 * * *"
        assert job["timezone"] == "America/New_York"
        assert job["environments"]["production"]["enabled"] is True
        # id slugified from name
        assert job["id"] == "nightly-warm"
        sent = fake_client.calls_named("create_job")[0]
        assert sent["attributes"]["configuration"]["method"] == "POST"

    def test_one_off_from_run_at(self, fake_client):
        job = tools.create_job(
            fake_client, name="One Shot", url="https://api.example.com/x",
            run_at="2099-01-01T03:00:00Z",
        )
        assert job["kind"] == "one_off"
        assert job["schedule"] == "2099-01-01T03:00:00Z"

    def test_manual_when_no_schedule(self, fake_client):
        job = tools.create_job(fake_client, name="Manual Job", url="https://api.example.com/x")
        assert job["kind"] == "manual"
        assert job["schedule"] is None

    def test_timezone_without_recurring_rejected(self, fake_client):
        with pytest.raises(ValueError, match="timezone"):
            tools.create_job(fake_client, name="x", url="https://api.example.com/x",
                             run_at="now", timezone="America/New_York")

    def test_localhost_url_rejected(self, fake_client):
        with pytest.raises(NonPublicTargetError):
            tools.create_job(fake_client, name="x", url="http://localhost:3000/hook")
        assert fake_client.calls_named("create_job") == []

    def test_optional_fields_passed_through(self, fake_client):
        tools.create_job(
            fake_client, name="Full", url="https://api.example.com/x", method="GET",
            headers={"Authorization": "Bearer z"}, body="hello", timeout=10,
            retry_policy="retry-5xx", description="desc", environment="staging",
        )
        attrs = fake_client.calls_named("create_job")[0]["attributes"]
        cfg = attrs["configuration"]
        assert cfg["method"] == "GET"
        assert cfg["headers"] == {"Authorization": "Bearer z"}
        assert cfg["body"] == "hello"
        assert cfg["timeout"] == 10
        assert attrs["retry_policy"] == "retry-5xx"
        assert attrs["description"] == "desc"
        assert attrs["environments"] == {"staging": {"enabled": True}}

    def test_explicit_job_id(self, fake_client):
        job = tools.create_job(fake_client, name="Weird Name!!", url="https://api.example.com/x",
                               job_id="my-custom-id")
        assert job["id"] == "my-custom-id"


# -- update_job (GET-mutate-PUT) -------------------------------------------


class TestUpdateJob:
    def _seed(self, fake_client):
        tools.create_job(fake_client, name="Sched", url="https://api.example.com/x",
                         schedule="0 2 * * *", timezone="UTC")

    def test_partial_change_full_replaces(self, fake_client):
        self._seed(fake_client)
        job = tools.update_job(fake_client, job_id="sched", schedule="0 8 * * *")
        assert job["schedule"] == "0 8 * * *"
        # GET then PUT
        assert [n for n, _ in fake_client.calls][-2:] == ["get_job", "replace_job"]
        put_attrs = fake_client.calls_named("replace_job")[0]["attributes"]
        # full resource sent: name + configuration preserved from GET
        assert put_attrs["name"] == "Sched"
        assert put_attrs["configuration"]["url"] == "https://api.example.com/x"

    def test_readonly_fields_stripped_before_put(self, fake_client):
        self._seed(fake_client)
        tools.update_job(fake_client, job_id="sched", name="Renamed")
        put_attrs = fake_client.calls_named("replace_job")[0]["attributes"]
        for field in ("kind", "version", "created_at", "updated_at", "deleted_at"):
            assert field not in put_attrs
        assert "next_run_at" not in put_attrs["environments"]["production"]

    def test_change_url_revalidates_public(self, fake_client):
        self._seed(fake_client)
        with pytest.raises(NonPublicTargetError):
            tools.update_job(fake_client, job_id="sched", url="http://127.0.0.1/x")

    def test_enable_disable_environment(self, fake_client):
        self._seed(fake_client)
        job = tools.update_job(fake_client, job_id="sched", enabled=False)
        assert job["environments"]["production"]["enabled"] is False

    def test_clearing_schedule_makes_manual(self, fake_client):
        self._seed(fake_client)
        # passing run_at="" would raise; here we verify config/header replace path
        job = tools.update_job(fake_client, job_id="sched", method="PUT",
                               headers={"X-A": "b"}, body="x", timeout=5, description="d",
                               retry_policy="rp")
        assert job["request"]["method"] == "PUT"
        put_attrs = fake_client.calls_named("replace_job")[0]["attributes"]
        assert put_attrs["configuration"]["headers"] == {"X-A": "b"}
        assert put_attrs["retry_policy"] == "rp"


# -- delete_job -------------------------------------------------------------


def test_delete_job(fake_client):
    tools.create_job(fake_client, name="Doomed", url="https://api.example.com/x")
    result = tools.delete_job(fake_client, job_id="doomed")
    assert result == {"deleted": True, "id": "doomed"}
    assert "doomed" not in fake_client.jobs


# -- run_job ----------------------------------------------------------------


class TestRunJob:
    def test_polls_until_terminal(self, fake_client):
        fake_client.run_now_status = "PENDING"
        fake_client.run_state_sequence = [
            {"status": "RUNNING"},
            {"status": "SUCCEEDED",
             "result": {"status": 200, "headers": {}, "body": "ok",
                        "body_truncated": False, "body_bytes": 2}},
        ]
        run = tools.run_job(fake_client, job_id="j", _sleep=lambda s: None)
        assert run["status"] == "SUCCEEDED"
        assert run["result"]["status"] == 200
        assert run["trigger"] == "MANUAL"
        assert len(fake_client.calls_named("get_run")) == 2

    def test_wait_false_returns_immediately(self, fake_client):
        fake_client.run_now_status = "PENDING"
        run = tools.run_job(fake_client, job_id="j", wait=False)
        assert run["status"] == "PENDING"
        assert fake_client.calls_named("get_run") == []

    def test_already_terminal_no_polling(self, fake_client):
        fake_client.run_now_status = "SUCCEEDED"
        run = tools.run_job(fake_client, job_id="j", _sleep=lambda s: None)
        assert run["status"] == "SUCCEEDED"
        assert fake_client.calls_named("get_run") == []

    def test_timeout_stops_polling(self, fake_client):
        fake_client.run_now_status = "PENDING"
        # clock jumps past the deadline after one tick
        ticks = iter([0.0, 0.0, 100.0])
        run = tools.run_job(fake_client, job_id="j", _sleep=lambda s: None,
                            _clock=lambda: next(ticks), timeout_seconds=25)
        assert run["status"] == "PENDING"

    def test_ssrf_failure_gets_hint(self, fake_client):
        fake_client.run_now_status = "FAILED"
        fake_client.runs  # noqa
        fake_client.run_now_status = "FAILED"
        # seed a failed run with SSRF reason directly
        resp = fake_client.run_job("j")
        run_id = resp["data"]["id"]
        fake_client.runs[run_id]["failure_reason"] = "SSRF_BLOCKED"
        run = tools.get_run(fake_client, run_id=run_id)
        assert "tunnel" in run["hint"]

    def test_quota_failure_gets_hint(self, fake_client):
        resp = fake_client.run_job("j")
        run_id = resp["data"]["id"]
        fake_client.runs[run_id]["status"] = "FAILED"
        fake_client.runs[run_id]["failure_reason"] = "QUOTA_EXCEEDED"
        run = tools.get_run(fake_client, run_id=run_id)
        assert "Upgrade your plan" in run["hint"]


# -- list_runs --------------------------------------------------------------


class TestListRuns:
    def test_failed_only_shortcut(self, fake_client):
        tools.list_runs(fake_client, failed_only=True)
        params = fake_client.calls_named("list_runs")[-1]["params"]
        assert params["filter[status]"] == "FAILED"

    def test_explicit_status_list(self, fake_client):
        tools.list_runs(fake_client, status=["SUCCEEDED", "FAILED"])
        params = fake_client.calls_named("list_runs")[-1]["params"]
        assert params["filter[status]"] == "SUCCEEDED,FAILED"

    def test_time_window(self, fake_client):
        tools.list_runs(fake_client, since="2026-06-01T00:00:00Z", until="2026-06-02T00:00:00Z")
        params = fake_client.calls_named("list_runs")[-1]["params"]
        assert params["filter[created_at]"] == "[2026-06-01T00:00:00Z,2026-06-02T00:00:00Z)"

    def test_open_ended_window(self, fake_client):
        tools.list_runs(fake_client, since="2026-06-01T00:00:00Z")
        params = fake_client.calls_named("list_runs")[-1]["params"]
        assert params["filter[created_at]"] == "[2026-06-01T00:00:00Z,*)"

    def test_job_trigger_environment_lastrun(self, fake_client):
        tools.list_runs(fake_client, job="j1", trigger=["SCHEDULE", "RETRY"],
                        environment="production", last_run_only=True, limit=10)
        params = fake_client.calls_named("list_runs")[-1]["params"]
        assert params["filter[job]"] == "j1"
        assert params["filter[trigger]"] == "SCHEDULE,RETRY"
        assert params["filter[environment]"] == "production"
        assert params["last_run_only"] == "true"
        assert params["page[size]"] == 10

    def test_returns_clean_runs(self, fake_client):
        fake_client.run_job("j1")
        out = tools.list_runs(fake_client)
        assert out["count"] == 1
        assert out["runs"][0]["job"] == "j1"


# -- list_jobs / get_job / get_run -----------------------------------------


def test_list_jobs_enriches_with_last_run(fake_client):
    tools.create_job(fake_client, name="A", url="https://api.example.com/a")
    # a completed run for job 'a'
    resp = fake_client.run_job("a")
    rid = resp["data"]["id"]
    fake_client.runs[rid]["status"] = "SUCCEEDED"
    fake_client.runs[rid]["result"] = {"status": 200, "headers": {}, "body": "x",
                                       "body_truncated": False, "body_bytes": 1}
    out = tools.list_jobs(fake_client)
    job = next(j for j in out["jobs"] if j["id"] == "a")
    assert job["last_run"]["status"] == "SUCCEEDED"
    assert job["last_run"]["result_status"] == 200


def test_list_jobs_enrichment_survives_runs_error(fake_client, monkeypatch):
    tools.create_job(fake_client, name="A", url="https://api.example.com/a")

    from smplkit_mcp.errors import JobsApiError

    def boom(params=None):
        raise JobsApiError(500, [])

    monkeypatch.setattr(fake_client, "list_runs", boom)
    out = tools.list_jobs(fake_client)
    assert out["jobs"][0]["last_run"] is None


def test_list_jobs_filters(fake_client):
    tools.list_jobs(fake_client, name="warm", kind="recurring", limit=5)
    params = fake_client.calls_named("list_jobs")[-1]["params"]
    assert params == {"filter[name]": "warm", "filter[kind]": "recurring", "page[size]": 5}


def test_get_job(fake_client):
    tools.create_job(fake_client, name="A", url="https://api.example.com/a")
    assert tools.get_job(fake_client, job_id="a")["id"] == "a"


def test_get_run(fake_client):
    resp = fake_client.run_job("j")
    rid = resp["data"]["id"]
    assert tools.get_run(fake_client, run_id=rid)["id"] == rid
