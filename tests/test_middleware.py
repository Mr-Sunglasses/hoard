import base64
import ipaddress

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app import middleware
from app.main import app

client = TestClient(app)


def basic(password: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"me:{password}".encode()).decode()}


@pytest.mark.parametrize("host", [
    "192.168.1.20", "192.168.1.20:8080", "[fd00::1]:8080", "localhost:8080",
    "nas", "nas:8080", "nas.local", "nas.lan", "box.home.arpa", "nas.internal",
])
def test_local_hosts_allowed(host):
    assert middleware.host_allowed(host)


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8080", "hoard.example.com", ""])
def test_other_hosts_refused(host):
    assert not middleware.host_allowed(host)


def test_allowed_hosts_setting(monkeypatch):
    monkeypatch.setattr(middleware, "ALLOWED_HOSTS", ["*.example.com"])
    assert middleware.host_allowed("hoard.example.com:443")
    assert not middleware.host_allowed("example.org")


def test_dns_rebinding_blocked():
    response = client.get("/api/downloads", headers={"Host": "evil.example"})
    assert response.status_code == 421


def test_security_headers():
    response = client.get("/")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-cache"


def test_api_not_cached():
    assert client.get("/api/downloads").headers["cache-control"] == "no-store"


def test_docs_disabled():
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_csrf_header_required():
    assert client.delete("/api/library/x").status_code == 403
    assert client.delete("/api/library/x", headers={"X-Requested-With": "hoard"}).status_code == 404


def test_password(monkeypatch):
    monkeypatch.setattr(middleware, "APP_PASSWORD", "s3cret-ü")
    monkeypatch.setattr(middleware, "limiter", middleware.LoginLimiter())
    assert client.get("/api/downloads").status_code == 401
    assert client.get("/api/downloads", headers=basic("nope")).status_code == 401
    assert client.get("/api/downloads", headers=basic("é")).status_code == 401
    assert client.get("/api/downloads", headers={"Authorization": "Basic !!!"}).status_code == 401
    assert client.get("/api/downloads", headers=basic("s3cret-ü")).status_code == 200
    assert client.get("/healthz").status_code == 200   # health check needs no password


def test_password_lockout(monkeypatch):
    monkeypatch.setattr(middleware, "APP_PASSWORD", "right")
    monkeypatch.setattr(middleware, "limiter", middleware.LoginLimiter())
    for _ in range(middleware.LoginLimiter.MAX_FAILURES):
        assert client.get("/", headers=basic("wrong")).status_code == 401
    locked = client.get("/", headers=basic("right"))
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0


def test_empty_challenge_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(middleware, "APP_PASSWORD", "right")
    monkeypatch.setattr(middleware, "limiter", middleware.LoginLimiter())
    for _ in range(middleware.LoginLimiter.MAX_FAILURES + 5):
        client.get("/")
    assert client.get("/", headers=basic("right")).status_code == 200


def proxied(peer: str, forwarded: str = "") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_client_address_behind_trusted_proxy(monkeypatch):
    monkeypatch.setattr(middleware, "trusted_proxies", middleware._networks(["172.16.0.0/12", "10.0.0.5"]))
    assert middleware.client_address(proxied("172.18.0.1", "192.168.1.7")) == "192.168.1.7"
    # Entries before the real visitor can be made up by the visitor; the proxy appends the real one.
    assert middleware.client_address(proxied("172.18.0.1", "6.6.6.6, 192.168.1.7")) == "192.168.1.7"
    # Proxies chained: skip every trusted hop.
    assert middleware.client_address(proxied("172.18.0.1", "192.168.1.7, 10.0.0.5")) == "192.168.1.7"
    # No header: the proxy itself.
    assert middleware.client_address(proxied("172.18.0.1")) == "172.18.0.1"
    # Anyone else's header is ignored.
    assert middleware.client_address(proxied("192.168.1.9", "1.2.3.4")) == "192.168.1.9"


def test_invalid_trusted_proxy_entries_are_skipped():
    assert middleware._networks(["nonsense", "10.0.0.0/8"]) == [ipaddress.ip_network("10.0.0.0/8")]


def test_lockout_per_visitor_behind_proxy(monkeypatch):
    monkeypatch.setattr(middleware, "APP_PASSWORD", "right")
    monkeypatch.setattr(middleware, "limiter", middleware.LoginLimiter())
    monkeypatch.setattr(middleware, "client_address", lambda request: request.headers.get("x-forwarded-for"))
    for _ in range(middleware.LoginLimiter.MAX_FAILURES):
        client.get("/", headers={**basic("wrong"), "X-Forwarded-For": "192.168.1.66"})
    assert client.get("/", headers={**basic("right"), "X-Forwarded-For": "192.168.1.66"}).status_code == 429
    assert client.get("/", headers={**basic("right"), "X-Forwarded-For": "192.168.1.7"}).status_code == 200


def test_limiter_forgets_expired_addresses(monkeypatch):
    limiter = middleware.LoginLimiter()
    clock = [1000.0]
    monkeypatch.setattr(middleware.time, "monotonic", lambda: clock[0])
    for i in range(100):
        limiter.failed(f"10.0.0.{i}")
    clock[0] += limiter.WINDOW + 1
    limiter.failed("10.0.1.1")
    assert list(limiter._failures) == ["10.0.1.1"]
