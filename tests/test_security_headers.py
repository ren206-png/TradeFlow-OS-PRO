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


def test_www_redirects_to_the_bare_domain_keeping_path_and_query():
    client = TestClient(app, base_url="http://www.tradesflowos.com")
    r = client.get("/privacy?ref=nav", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "https://tradesflowos.com/privacy?ref=nav"
    assert r.headers["strict-transport-security"].startswith("max-age=")          # security headers still apply

    post = client.post("/auth/login", data={"email": "a@b.com"}, follow_redirects=False)
    assert post.status_code == 308 and post.headers["location"] == "https://tradesflowos.com/auth/login"


def test_bare_domain_and_api_host_are_not_redirected():
    assert TestClient(app, base_url="http://tradesflowos.com").get("/health", follow_redirects=False).status_code == 200
    assert TestClient(app, base_url="http://api.tradesflowos.com").get("/health", follow_redirects=False).status_code == 200


def test_head_requests_work_for_uptime_monitors():
    client = TestClient(app)
    for path in ("/", "/health"):
        assert client.head(path).status_code in (200, 503)
