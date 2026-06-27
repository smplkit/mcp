"""ASGI entrypoint for uvicorn.

Run with: ``uvicorn smplkit_mcp.app:app --host 0.0.0.0 --port 8000``

The MCP endpoint is mounted under ``/api/mcp`` so it routes through the standard
CloudFront -> ALB ``/api/*`` pattern (ADR-011). The transport is configured
stateless with JSON responses (no long-lived SSE) so it behaves correctly
through proxies with short idle timeouts.
"""
from __future__ import annotations

from .server import mcp

app = mcp.http_app(
    path="/api/mcp",
    transport="http",
    stateless_http=True,
    json_response=True,
)
