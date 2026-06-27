"""Kind inference and schedule resolution."""
from __future__ import annotations

import pytest

from smplkit_mcp.kinds import infer_kind, resolve_schedule


class TestResolveSchedule:
    def test_cron_schedule(self):
        assert resolve_schedule(schedule="0 7 * * *", run_at=None) == "0 7 * * *"

    def test_run_at_datetime(self):
        assert resolve_schedule(schedule=None, run_at="2099-01-01T03:00:00Z") == (
            "2099-01-01T03:00:00Z"
        )

    def test_run_at_now(self):
        assert resolve_schedule(schedule=None, run_at="now") == "now"

    def test_neither_is_manual(self):
        assert resolve_schedule(schedule=None, run_at=None) is None

    def test_both_rejected(self):
        with pytest.raises(ValueError, match="not both"):
            resolve_schedule(schedule="0 7 * * *", run_at="now")

    def test_empty_schedule_rejected(self):
        with pytest.raises(ValueError, match="non-empty cron"):
            resolve_schedule(schedule="   ", run_at=None)

    def test_empty_run_at_rejected(self):
        with pytest.raises(ValueError, match="non-empty ISO-8601"):
            resolve_schedule(schedule=None, run_at="  ")

    def test_strips_whitespace(self):
        assert resolve_schedule(schedule="  0 7 * * *  ", run_at=None) == "0 7 * * *"


class TestInferKind:
    def test_cron_is_recurring(self):
        assert infer_kind("0 7 * * *") == "recurring"

    def test_datetime_is_one_off(self):
        assert infer_kind("2099-01-01T03:00:00Z") == "one_off"

    def test_now_is_one_off(self):
        assert infer_kind("now") == "one_off"
        assert infer_kind("NOW") == "one_off"

    def test_none_is_manual(self):
        assert infer_kind(None) == "manual"
