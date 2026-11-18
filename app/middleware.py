"""Request guards and response headers, applied to every request.

In order: security headers → host check → password (with lockout) → CSRF check → cache headers.
"""

import base64
import ipaddress
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from fnmatch import fnmatch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .config import ALLOWED_HOSTS, APP_PASSWORD, TRUSTED_PROXIES

log = logging.getLogger("hoard")

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "hoard"
OPEN_PATHS = ("/healthz",)   # no password, so Docker's health check works

# Names that can only point inside your network, so a website can't make them resolve to Hoard.
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".localhost")

SECURITY_HEADERS = {
    # Scripts, styles, fonts and requests only from Hoard itself. Inline styles are allowed for
    # computed values (progress widths, avatar colors); inline scripts are not.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}


# ---------- host check (DNS rebinding) ----------


def host_allowed(host_header: str) -> bool:
    """Is this Host header one of ours? Blocks DNS rebinding, where a website points its own
    domain name at your server's IP to talk to Hoard from inside your browser."""
    host = host_header.strip().lower()
    if host.startswith("["):                       # [ipv6]:port
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:                     # name:port or ipv4:port
        host = host.partition(":")[0]
    host = host.rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if "." not in host or host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return True
    return any(pattern == "*" or fnmatch(host, pattern) for pattern in ALLOWED_HOSTS)


async def host_check(request: Request, call_next):
    if not host_allowed(request.headers.get("host", "")):
        return PlainTextResponse(
            "Hoard doesn't answer to this host name. If it's yours, add it to ALLOWED_HOSTS on the server.",
            status_code=421,
        )
    return await call_next(request)


# ---------- password ----------


class LoginLimiter:
    """After MAX_FAILURES wrong passwords from one address within WINDOW seconds, that
    address is refused until the oldest failure is WINDOW seconds old."""

    MAX_FAILURES = 10
    WINDOW = 15 * 60

    def __init__(self):
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, client: str, now: float) -> deque:
        failures = self._failures[client]
        while failures and now - failures[0] > self.WINDOW:
            failures.popleft()
        return failures

    def retry_after(self, client: str) -> int:
        """Seconds until this address may try again (0 = now)."""
        now = time.monotonic()
        with self._lock:
            failures = self._recent(client, now)
            if len(failures) < self.MAX_FAILURES:
                if not failures:
                    self._failures.pop(client, None)
                return 0
            return int(self.WINDOW - (now - failures[0])) + 1

    def failed(self, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            # Forget addresses whose failures have all expired, so the table can't keep growing.
            for other in [c for c, f in self._failures.items() if not f or now - f[-1] > self.WINDOW]:
                del self._failures[other]
            self._failures[client].append(now)

    def succeeded(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)


limiter = LoginLimiter()


def _networks(entries: list[str]) -> list:
    networks = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            log.error("Ignoring %r in TRUSTED_PROXIES: not an IP address or network", entry)
    return networks


trusted_proxies = _networks(TRUSTED_PROXIES)


def _trusted(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in trusted_proxies)


def client_address(request: Request) -> str:
    """The visitor's address. Behind a trusted reverse proxy every request comes from the proxy,
    so the address is taken from X-Forwarded-For instead: the last entry that isn't a trusted
    proxy (earlier entries could be made up by the visitor)."""
    address = request.client.host if request.client else "unknown"
    if not _trusted(address):
        return address
    forwarded = [a.strip() for a in request.headers.get("x-forwarded-for", "").split(",") if a.strip()]
    for hop in reversed(forwarded):
        if not _trusted(hop):
            return hop
    return address


def password_ok(authorization: str, password: str) -> bool:
    """Check an HTTP Basic auth header. Any username is accepted; only the password matters."""
    if not authorization.startswith("Basic "):
        return False
    try:
        _, _, given = base64.b64decode(authorization[6:], validate=True).decode().partition(":")
    except ValueError:   # bad base64 or not UTF-8
        return False
    return secrets.compare_digest(given.encode(), password.encode())


async def basic_auth(request: Request, call_next):
    """Only active when APP_PASSWORD is set."""
    if not APP_PASSWORD or request.url.path in OPEN_PATHS:
        return await call_next(request)

    client = client_address(request)
    wait = limiter.retry_after(client)
    if wait:
        return PlainTextResponse(
            f"Too many wrong passwords. Try again in {wait // 60 + 1} min.",
            status_code=429,
            headers={"Retry-After": str(wait)},
        )
    header = request.headers.get("authorization", "")
    if not password_ok(header, APP_PASSWORD):
        if header:   # the browser's first, empty request isn't a wrong guess
            limiter.failed(client)
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Hoard", charset="UTF-8"'})
    limiter.succeeded(client)
    return await call_next(request)


# ---------- CSRF ----------


async def csrf_guard(request: Request, call_next):
    """Changes need a custom header. Browsers can't send one cross-site without a CORS preflight
    (which Hoard never answers), so other websites can't trigger deletes or token changes."""
    if request.url.path.startswith("/api/") and request.method not in SAFE_METHODS:
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    return await call_next(request)


# ---------- response headers ----------


async def revalidate_ui(request: Request, call_next):
    """The UI is several ES module files. Browsers revalidate them every time (cheap 304s),
    so an update never runs a mix of old and new modules. API responses are never cached."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def install(app: FastAPI) -> None:
    # The last one added runs first.
    app.middleware("http")(revalidate_ui)
    app.middleware("http")(csrf_guard)
    app.middleware("http")(basic_auth)
    app.middleware("http")(host_check)
    app.middleware("http")(security_headers)
