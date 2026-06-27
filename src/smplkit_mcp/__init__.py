"""smplkit MCP Server (ADR-057).

A hosted, stateless Model Context Protocol server — the agent gateway to the
smplkit platform. It forwards the customer's API key per request to the
underlying smplkit APIs. Available now: Smpl Jobs (scheduled HTTP jobs).
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
