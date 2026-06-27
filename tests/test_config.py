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
