"""Errors and friendly, actionable error mapping.

Every smplkit product API speaks JSON:API errors (``{"errors": [{"status",
"title", "detail"}]}``). The tools surface those as plain, actionable text the
agent can act on — never a raw envelope and never the customer's API key.
"""
from __future__ import annotations

from typing import Any

CONSOLE_URL = "https://smplkit.com"

MISSING_KEY_MESSAGE = (
    "Connect your smplkit API key. Add it to the mcp.smplkit.com server in your "
    "MCP client config as an `Authorization: Bearer <your-smplkit-api-key>` "
    "header (a custom `X-Smplkit-Api-Key` header also works). Mint a key at "
    f"{CONSOLE_URL}: sign up with Google or Microsoft (SSO accounts are verified "
    "instantly, so you can create a key right away), then create an API key."
)


class MissingApiKeyError(Exception):
    """Raised when a request arrives with no smplkit API key."""

    def __init__(self, message: str = MISSING_KEY_MESSAGE) -> None:
        super().__init__(message)


class SmplkitApiError(Exception):
    """Raised by a product client on a non-2xx response from a smplkit API."""

    def __init__(
        self,
        status_code: int,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.status_code = status_code
        self.errors = errors or []
        super().__init__(self.detail())

    def detail(self) -> str:
        """Best human-readable detail from the JSON:API error list."""
        parts: list[str] = []
        for err in self.errors:
            text = err.get("detail") or err.get("title") or ""
            if text:
                parts.append(text)
        if parts:
            return " ".join(parts)
        return f"HTTP {self.status_code}"


# Backwards-compatible alias: the Jobs client and its tests refer to this name.
# All product clients raise the same exception type.
JobsApiError = SmplkitApiError


def friendly_message(exc: SmplkitApiError) -> str:
    """Map a :class:`SmplkitApiError` to actionable, customer-facing text."""
    code = exc.status_code
    detail = exc.detail()

    if code == 401:
        return (
            "Your smplkit API key is missing or invalid. Mint a fresh key at "
            f"{CONSOLE_URL} (sign in, then create an API key) and set it as the "
            "`Authorization: Bearer` header on the mcp.smplkit.com connection."
        )
    if code == 402:
        return (
            f"This would exceed your smplkit plan limit. {detail} "
            f"Upgrade your plan at {CONSOLE_URL} to raise the limit."
        )
    if code == 403:
        return f"smplkit refused this request: {detail}"
    if code == 404:
        return f"Not found: {detail}"
    if code == 409:
        return f"Conflict: {detail}"
    if code in (400, 422):
        return f"The request was rejected as invalid: {detail}"
    return f"smplkit returned an error (HTTP {code}): {detail}"
