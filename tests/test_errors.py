"""Error detail extraction and friendly mapping."""
from __future__ import annotations

from smplkit_mcp.errors import (
    MISSING_KEY_MESSAGE,
    JobsApiError,
    MissingApiKeyError,
    friendly_message,
)


class TestJobsApiError:
    def test_detail_prefers_detail_field(self):
        exc = JobsApiError(422, [{"status": "422", "title": "Invalid", "detail": "bad schedule"}])
        assert exc.detail() == "bad schedule"

    def test_detail_falls_back_to_title(self):
        exc = JobsApiError(403, [{"status": "403", "title": "Forbidden"}])
        assert exc.detail() == "Forbidden"

    def test_detail_joins_multiple(self):
        exc = JobsApiError(422, [
            {"status": "422", "title": "A", "detail": "first"},
            {"status": "422", "title": "B", "detail": "second"},
        ])
        assert exc.detail() == "first second"

    def test_detail_without_errors(self):
        exc = JobsApiError(500, [])
        assert exc.detail() == "HTTP 500"


class TestFriendlyMessage:
    def test_401_points_to_console_and_header(self):
        msg = friendly_message(JobsApiError(401, [{"status": "401", "title": "Unauthorized"}]))
        assert "API key" in msg
        assert "smplkit.com" in msg
        assert "Authorization: Bearer" in msg

    def test_402_is_upgrade_guidance(self):
        msg = friendly_message(JobsApiError(402, [{"status": "402", "title": "Payment Required",
                                                   "detail": "Free plan allows 10 jobs."}]))
        assert "plan limit" in msg
        assert "Upgrade your plan" in msg
        assert "Free plan allows 10 jobs." in msg

    def test_403(self):
        msg = friendly_message(JobsApiError(403, [{"status": "403", "title": "Forbidden"}]))
        assert "refused" in msg

    def test_404(self):
        assert "Not found" in friendly_message(JobsApiError(404, [{"status": "404", "title": "x",
                                                                   "detail": "no job"}]))

    def test_409(self):
        msg = friendly_message(JobsApiError(409, [{"status": "409", "title": "Conflict"}]))
        assert "Conflict" in msg

    def test_422_validation(self):
        assert "invalid" in friendly_message(JobsApiError(422, [{"status": "422", "title": "x",
                                                                 "detail": "bad"}]))

    def test_400_validation(self):
        assert "invalid" in friendly_message(JobsApiError(400, [{"status": "400", "title": "x"}]))

    def test_500_generic(self):
        msg = friendly_message(JobsApiError(503, [{"status": "503", "title": "Unavailable"}]))
        assert "HTTP 503" in msg


class TestMissingApiKeyError:
    def test_default_message(self):
        assert str(MissingApiKeyError()) == MISSING_KEY_MESSAGE
        assert "Connect your smplkit API key" in MISSING_KEY_MESSAGE
