"""Settings resolution."""
from __future__ import annotations

from smplkit_mcp.config import load_settings


def test_default_base_url():
    assert load_settings({}).jobs_base_url == "https://jobs.smplkit.com"


def test_full_url_override_wins():
    settings = load_settings({"JOBS_BASE_URL": "http://localhost:8005/", "JOBS_BASE_DOMAIN": "x"})
    assert settings.jobs_base_url == "http://localhost:8005"


def test_base_domain_and_scheme():
    settings = load_settings({"JOBS_BASE_DOMAIN": "jobs.localhost", "JOBS_SCHEME": "http"})
    assert settings.jobs_base_url == "http://jobs.localhost"


def test_base_domain_defaults_https():
    settings = load_settings({"JOBS_BASE_DOMAIN": "jobs.staging.example.com"})
    assert settings.jobs_base_url == "https://jobs.staging.example.com"


def test_all_product_defaults():
    s = load_settings({})
    assert s.jobs_base_url == "https://jobs.smplkit.com"
    assert s.flags_base_url == "https://flags.smplkit.com"
    assert s.config_base_url == "https://config.smplkit.com"
    assert s.logging_base_url == "https://logging.smplkit.com"
    assert s.audit_base_url == "https://audit.smplkit.com"
    assert s.app_base_url == "https://app.smplkit.com"  # environments live on the app API


def test_each_product_resolves_independently():
    env = {
        "FLAGS_BASE_URL": "http://localhost:8002",
        "CONFIG_BASE_DOMAIN": "config.localhost",
        "CONFIG_SCHEME": "http",
        "AUDIT_BASE_DOMAIN": "audit.staging.example.com",
        "APP_BASE_URL": "http://localhost:8000/",
    }
    s = load_settings(env)
    assert s.flags_base_url == "http://localhost:8002"
    assert s.config_base_url == "http://config.localhost"
    assert s.audit_base_url == "https://audit.staging.example.com"
    assert s.app_base_url == "http://localhost:8000"
    # untouched products keep their defaults
    assert s.jobs_base_url == "https://jobs.smplkit.com"
    assert s.logging_base_url == "https://logging.smplkit.com"
