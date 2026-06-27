"""End-to-end acceptance: all eight tools against a real Jobs service.

Provisions a throwaway verified account (ADR-028), drives the full tool surface
including the ADR-057 magic moment (create recurring -> run_job returns a real
200 with the captured body -> list_runs/get_run show it), and cleans up.

Runs only when a smplkit admin key is available (``ADMIN_API_KEY`` env or the
``[admin]`` profile in ``~/.smplkit``) — otherwise it skips. Opt in with
``pytest -m acceptance``. Point at a non-prod Jobs service with ``JOBS_BASE_URL``.
"""
from __future__ import annotations

import uuid

import pytest
from _platform import admin_key, provision_account

from smplkit_mcp import tools
from smplkit_mcp.config import load_settings
from smplkit_mcp.errors import JobsApiError
from smplkit_mcp.jobs_client import JobsClient

pytestmark = pytest.mark.acceptance

# A reliable always-200 public GET endpoint to use as the job target, so the
# captured run result is a deterministic 200 with a body.
TARGET_URL = "https://app.smplkit.com/api/liveness"


@pytest.fixture()
def account():
    if not admin_key():
        pytest.skip("no smplkit admin key (ADMIN_API_KEY or ~/.smplkit [admin]) — skipping")
    with provision_account() as acct:
        yield acct


@pytest.mark.timeout(120)
def test_eight_tools_and_magic_moment(account):
    client = JobsClient(account.api_key, load_settings().jobs_base_url)
    suffix = uuid.uuid4().hex[:8]
    job_id = f"mcp-accept-{suffix}"
    name = f"MCP Acceptance {suffix}"

    # 1) create_job — a recurring job (cron schedule -> kind inferred recurring).
    created = tools.create_job(
        client,
        name=name,
        url=TARGET_URL,
        method="GET",
        schedule="0 7 * * *",
        timezone="America/New_York",
        environment="production",
        job_id=job_id,
    )
    assert created["id"] == job_id
    assert created["kind"] == "recurring"
    assert created["schedule"] == "0 7 * * *"
    assert created["environments"]["production"]["enabled"] is True

    # 2) run_job — the magic moment: a real MANUAL run with a captured 200 body.
    run = tools.run_job(client, job_id=job_id, environment="production",
                        wait=True, timeout_seconds=60)
    assert run["trigger"] == "MANUAL"
    assert run["status"] == "SUCCEEDED", run
    assert run["result"] is not None
    assert run["result"]["status"] == 200
    assert run["result"]["body"] is not None
    run_id = run["id"]

    # 3) get_run — the captured response is retrievable by id.
    fetched = tools.get_run(client, run_id=run_id)
    assert fetched["id"] == run_id
    assert fetched["result"]["status"] == 200

    # 4) list_runs — status filter returns the successful run.
    runs = tools.list_runs(client, job=job_id, status="SUCCEEDED")
    assert run_id in {r["id"] for r in runs["runs"]}
    assert all(r["status"] == "SUCCEEDED" for r in runs["runs"])

    # 5) list_jobs — the job appears, enriched with its latest run.
    listed = tools.list_jobs(client, name=name)
    job_row = next(j for j in listed["jobs"] if j["id"] == job_id)
    assert job_row["last_run"]["status"] == "SUCCEEDED"
    assert job_row["last_run"]["result_status"] == 200

    # 6) get_job — single fetch.
    assert tools.get_job(client, job_id=job_id)["id"] == job_id

    # 7) update_job — GET-mutate-PUT full replace: change the schedule, and
    #    confirm the un-touched name survived the round-trip and version bumped.
    updated = tools.update_job(client, job_id=job_id, schedule="0 8 * * *")
    assert updated["schedule"] == "0 8 * * *"
    assert updated["name"] == name
    assert updated["version"] > created["version"]

    # 8) delete_job — removed; a subsequent fetch 404s.
    assert tools.delete_job(client, job_id=job_id)["deleted"] is True
    with pytest.raises(JobsApiError) as exc:
        tools.get_job(client, job_id=job_id)
    assert exc.value.status_code == 404
