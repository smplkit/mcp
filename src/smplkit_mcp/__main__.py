"""Run the server directly: ``python -m smplkit_mcp``.

Convenience runner for local development. Production uses
``uvicorn smplkit_mcp.app:app`` (see the Dockerfile).
"""
from __future__ import annotations

import os

from .server import mcp


def main() -> None:
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        path="/api/mcp",
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
