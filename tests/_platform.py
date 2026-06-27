"""Ephemeral-account provisioning for the acceptance suite.

Mirrors the proven smplkit e2e pattern (ADR-028): register a throwaway account
on the app service, force-verify its email via the admin API (clearing the
ADR-036 key-creation gate), mint an API key, and purge the account on teardown.
Uses raw httpx so this repo carries no SDK dependency.

The admin key is read from ``ADMIN_API_KEY`` or the ``[admin]`` profile in
``~/.smplkit`` — the same source the e2e suite uses.
"""
from __future__ import annotations

import configparser
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://app.smplkit.com")
ADMIN_BASE_URL = os.environ.get("ADMIN_BASE_URL", "https://admin.smplkit.com")

# The shared e2e rate-limit bypass token (same value the e2e suite uses).
E2E_HEADERS = {"X-Rate-Limit-Bypass": "7f3e9a2b-1c4d-4e8f-b6a5-9d0e2f8c7b3a"}


@dataclass
class TestAccount:
    api_key: str
    account_id: str


def admin_key() -> str | None:
    """Resolve the admin API key from env or the ``[admin]`` ~/.smplkit profile."""
    key = os.environ.get("ADMIN_API_KEY")
    if key:
        return key
    cfg_path = Path.home() / ".smplkit"
    if not cfg_path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(cfg_path)
    if parser.has_option("admin", "api_key"):
        return parser.get("admin", "api_key").strip() or None
    return None


def _admin_headers() -> dict[str, str]:
    key = admin_key()
    assert key, "admin API key required"
    return {"Authorization": f"Bearer {key}"}


def force_verify_email(email: str, account_id: str) -> None:
    """Force-verify a test user's email via the admin API (ADR-036 gate)."""
    headers = _admin_headers()
    users = httpx.get(
        f"{ADMIN_BASE_URL}/api/v1/users",
        params={"filter[email]": email, "filter[account]": account_id},
        headers=headers,
        timeout=30,
    )
    users.raise_for_status()
    user_id = users.json()["data"][0]["id"]
    verify = httpx.post(
        f"{ADMIN_BASE_URL}/api/v1/users/{user_id}/actions/verify_email",
        headers=headers,
        timeout=30,
    )
    verify.raise_for_status()


def purge_account(account_id: str) -> None:
    """Hard-delete a throwaway account via the admin purge endpoint (idempotent)."""
    resp = httpx.post(
        f"{ADMIN_BASE_URL}/api/v1/accounts/{account_id}/actions/purge",
        headers=_admin_headers(),
        timeout=30,
    )
    if resp.status_code not in (204, 404):
        resp.raise_for_status()


@contextmanager
def provision_account():
    """Yield a verified throwaway :class:`TestAccount`; purge it on exit."""
    run_id = uuid.uuid4().hex[:12]
    email = f"e2e-mcp-{run_id}@smplkit-test.com"
    password = f"T3st!{run_id}"
    account_id: str | None = None
    try:
        reg = httpx.post(
            f"{APP_BASE_URL}/api/v1/auth/register",
            json={"email": email, "password": password},
            headers=E2E_HEADERS,
            timeout=30,
        )
        reg.raise_for_status()
        jwt = reg.json()["token"]

        acct = httpx.get(
            f"{APP_BASE_URL}/api/v1/accounts/current",
            headers={"Authorization": f"Bearer {jwt}", **E2E_HEADERS},
            timeout=30,
        )
        acct.raise_for_status()
        account_id = acct.json()["data"]["id"]

        force_verify_email(email, account_id)

        login = httpx.post(
            f"{APP_BASE_URL}/api/v1/auth/login",
            json={"email": email, "password": password},
            headers=E2E_HEADERS,
            timeout=30,
        )
        login.raise_for_status()
        jwt = login.json()["token"]

        key_resp = httpx.post(
            f"{APP_BASE_URL}/api/v1/api_keys",
            json={"data": {"type": "api_key", "attributes": {"name": "mcp-acceptance"}}},
            headers={
                "Authorization": f"Bearer {jwt}",
                "Content-Type": "application/vnd.api+json",
                "Accept": "application/vnd.api+json",
                **E2E_HEADERS,
            },
            timeout=30,
        )
        key_resp.raise_for_status()
        api_key = key_resp.json()["data"]["attributes"]["key"]

        yield TestAccount(api_key=api_key, account_id=account_id)
    finally:
        if account_id is not None:
            try:
                purge_account(account_id)
            except Exception:
                pass
