"""Public-URL constraint."""
from __future__ import annotations

import pytest

from smplkit_mcp.urls import (
    NonPublicTargetError,
    is_public_target,
    require_public_target,
)


class TestIsPublicTarget:
    @pytest.mark.parametrize("url", [
        "https://api.example.com/hook",
        "http://example.org",
        "https://sub.domain.example.com/path?q=1",
        "https://93.184.216.34/in",  # public IP
    ])
    def test_public(self, url):
        assert is_public_target(url) is True

    @pytest.mark.parametrize("url", [
        "http://localhost:8000/hook",
        "https://localhost/in",
        "http://127.0.0.1:5000",
        "http://10.0.0.5/in",
        "http://192.168.1.10",
        "http://172.16.0.9",
        "http://169.254.0.1",
        "http://[::1]:8000",
        "http://myservice.local/in",
        "http://api.internal/in",
        "http://foo.localhost/in",
        "http://0.0.0.0:8000",
    ])
    def test_not_public(self, url):
        assert is_public_target(url) is False

    @pytest.mark.parametrize("url", [
        "ftp://example.com",       # wrong scheme
        "not a url",
        "",
        "https://",                # no host
    ])
    def test_invalid_is_not_public(self, url):
        assert is_public_target(url) is False


class TestRequirePublicTarget:
    def test_public_ok(self):
        require_public_target("https://api.example.com")  # no raise

    def test_localhost_raises_with_guidance(self):
        with pytest.raises(NonPublicTargetError) as exc:
            require_public_target("http://localhost:3000")
        assert "tunnel" in str(exc.value)
        assert "public internet" in str(exc.value)
