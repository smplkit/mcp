"""End-to-end acceptance: all eight tools against a real Jobs service.

Provisions a throwaway verified account, drives the full tool surface end-to-end
(create recurring -> run_job returns a real 200 with the captured body ->
list_runs/get_run show it), and cleans up.

Runs only when a smplkit admin key is available (``ADMIN_API_KEY`` env or the
``[admin]`` profile in ``~/.smplkit``) — otherwise it skips. Opt in with
``pytest -m acceptance``. Point at a non-prod Jobs service with ``JOBS_BASE_URL``.
"""
from __future__ import annotations

import uuid

import pytest
from _platform import admin_key, provision_account

from smplkit_mcp import audit, configs, environments, flags, loggers, tools
from smplkit_mcp.audit import AuditClient
from smplkit_mcp.config import load_settings
from smplkit_mcp.configs import ConfigClient
from smplkit_mcp.environments import EnvironmentsClient
from smplkit_mcp.errors import JobsApiError
from smplkit_mcp.flags import FlagsClient
from smplkit_mcp.jobs_client import JobsClient
from smplkit_mcp.loggers import LoggerClient

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

    # 2) run_job — a real MANUAL run with a captured 200 body.
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


# A reliable always-200 public GET endpoint, used as a forwarder test target.
LIVENESS_URL = "https://app.smplkit.com/api/liveness"


@pytest.mark.timeout(180)
def test_full_platform_surface(account):
    """Drive flags + config + logging + audit + environments end-to-end.

    One ephemeral account, the way an agent would compose the whole platform:
    create/set/get/delete a flag, set/read a config value, set/list a log level,
    query events, create/test/delete a forwarder, and list environments.
    """
    settings = load_settings()
    suffix = uuid.uuid4().hex[:8]
    env = "production"  # a system environment (ADR-051), valid on every account

    # -- Flags: create -> set (value + kill switch + targeting) -> get -> delete
    flags_client = FlagsClient(account.api_key, settings.flags_base_url)
    flag_key = f"mcp-accept-{suffix}"
    created = flags.create_flag(
        flags_client, key=flag_key, type="boolean", default=False,
        name=f"MCP Accept {suffix}", description="acceptance flag",
    )
    assert created["id"] == flag_key
    assert created["type"] == "BOOLEAN"

    set_result = flags.set_flag(
        flags_client, key=flag_key, environment=env, value=True, enabled=True,
        rules=[{"when": [{"attribute": "user.plan", "operator": "==", "value": "enterprise"}],
                "serve": True, "description": "Enterprise users"}],
    )
    prod = set_result["environments"][env]
    assert prod["enabled"] is True
    assert prod["default"] is True
    assert prod["rules"][0]["logic"] == {"==": [{"var": "user.plan"}, "enterprise"]}

    fetched_flag = flags.get_flag(flags_client, key=flag_key)
    assert fetched_flag["environments"][env]["default"] is True
    assert flag_key in {f["id"] for f in flags.list_flags(flags_client, search=suffix)["flags"]}
    assert flags.delete_flag(flags_client, key=flag_key)["deleted"] is True

    # -- Config: create -> set one key's value in an env -> read it back -> delete
    config_client = ConfigClient(account.api_key, settings.config_base_url)
    created_config = configs.create_config(
        config_client, name=f"MCP Accept Config {suffix}", description="acceptance config",
    )
    config_id = created_config["id"]
    configs.set_config_value(
        config_client, config_id=config_id, key="greeting", value="hello", environment=env,
    )
    fetched_config = configs.get_config(config_client, config_id=config_id)
    assert fetched_config["environments"][env]["greeting"] == "hello"
    assert "greeting" in fetched_config["items"]  # auto-declared
    assert configs.delete_config(config_client, config_id=config_id)["deleted"] is True

    # -- Logging: set a logger's level in an env -> read it back -> reset
    logger_client = LoggerClient(account.api_key, settings.logging_base_url)
    logger_id = f"mcp.accept.{suffix}"
    set_logger = loggers.set_log_level(
        logger_client, logger_id=logger_id, level="DEBUG", environment=env,
    )
    assert set_logger["environments"][env]["level"] == "DEBUG"
    assert loggers.get_logger(logger_client, logger_id=logger_id)["environments"][env]["level"] \
        == "DEBUG"
    listed_loggers = loggers.list_loggers(logger_client, managed=True)
    assert logger_id in {lg["id"] for lg in listed_loggers["loggers"]}
    assert loggers.reset_logger(logger_client, logger_id=logger_id)["deleted"] is True

    # -- Audit: query events (well-formed) ; test -> create -> list -> delete a forwarder
    audit_client = AuditClient(account.api_key, settings.audit_base_url)
    events = audit.query_events(audit_client, limit=5)
    assert isinstance(events["events"], list)
    assert events["count"] == len(events["events"])

    # run-to-prove: dry-run the destination before saving the forwarder
    probe = audit.test_forwarder(
        audit_client, url=LIVENESS_URL, method="GET", success_status="2xx",
    )
    assert probe["succeeded"] is True, probe
    assert probe["response_status"] == 200

    forwarder = audit.create_forwarder(
        audit_client, name=f"MCP Accept Fwd {suffix}", url=LIVENESS_URL,
        forwarder_type="http", method="GET", success_status="2xx", environment=env,
    )
    forwarder_id = forwarder["id"]
    assert forwarder_id in {f["id"] for f in audit.list_forwarders(audit_client)["forwarders"]}
    assert audit.delete_forwarder(audit_client, forwarder_id=forwarder_id)["deleted"] is True

    # -- Environments: the agent can discover valid targets
    env_client = EnvironmentsClient(account.api_key, settings.app_base_url)
    env_list = environments.list_environments(env_client)
    assert isinstance(env_list["environments"], list)
    assert env_list["count"] == len(env_list["environments"])
    # Every returned environment exposes the fields an agent needs to pick a target.
    assert all("key" in e and "name" in e for e in env_list["environments"])
