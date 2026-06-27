"""Job-kind inference.

ADR-057: ``create_job`` infers the job kind and never exposes a ``kind``
parameter to the model. The Jobs API derives the read-only ``kind`` from the
``schedule`` field — a cron expression is *recurring*, an ISO-8601 datetime
(or the literal ``now``) is *one-off*, and no schedule is *manual*. This module
maps the tool's intent-named inputs (``schedule`` for a repeating cron,
``run_at`` for a single future time) onto that single ``schedule`` field.
"""
from __future__ import annotations

VALID_KINDS = ("recurring", "manual", "one_off")


def resolve_schedule(*, schedule: str | None, run_at: str | None) -> str | None:
    """Resolve the Jobs ``schedule`` field value from the tool's inputs.

    - ``schedule`` (a 5-field cron expression) → recurring job.
    - ``run_at`` (an ISO-8601 datetime, or ``now``) → one-off job.
    - neither → manual job (returns ``None``; the job never auto-fires).

    The two are mutually exclusive. Raises :class:`ValueError` on conflicting
    or empty input.
    """
    if schedule is not None and run_at is not None:
        raise ValueError(
            "Provide either a recurring `schedule` (a cron expression) or a "
            "one-time `run_at` datetime — not both."
        )
    if schedule is not None:
        value = schedule.strip()
        if not value:
            raise ValueError("`schedule` must be a non-empty cron expression.")
        return value
    if run_at is not None:
        value = run_at.strip()
        if not value:
            raise ValueError("`run_at` must be a non-empty ISO-8601 datetime.")
        return value
    return None


def infer_kind(schedule_value: str | None) -> str:
    """Infer the kind the Jobs API will derive from a ``schedule`` value.

    Mirrors the server-side rule so the tools can validate input and craft
    accurate messages without a round-trip. A 5-field expression is a cron
    (recurring); ``now`` or anything else non-empty is a one-off datetime;
    ``None`` is manual.
    """
    if schedule_value is None:
        return "manual"
    if _looks_like_cron(schedule_value):
        return "recurring"
    return "one_off"


def _looks_like_cron(value: str) -> bool:
    """A 5-field, whitespace-separated expression is treated as cron."""
    stripped = value.strip()
    if stripped.lower() == "now":
        return False
    return len(stripped.split()) == 5
