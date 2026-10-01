"""Security headers on every response; API docs hidden outside debug."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_security_headers_present_on_pages_and_errors():
    client = TestClient(app)
    for path in ("/", "/auth/login", "/health", "/does-not-exist"):
        h = client.get(path).headers
        assert h["strict-transport-security"].startswith("max-age=")
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_api_docs_are_hidden_unless_debug():
    client = TestClient(app)
    if not settings.debug:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
