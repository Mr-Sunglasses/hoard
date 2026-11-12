"""Request guards and cache headers, applied to every request."""

import base64
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .config import APP_PASSWORD

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "hoard"


def password_ok(authorization: str) -> bool:
    """Check an HTTP Basic auth header. Any username is accepted; only the password matters."""
    if not authorization.startswith("Basic "):
        return False
    try:
        _, _, password = base64.b64decode(authorization[6:]).decode().partition(":")
    except ValueError:   # bad base64 or not UTF-8
        return False
    return secrets.compare_digest(password.encode(), APP_PASSWORD.encode())


async def basic_auth(request: Request, call_next):
    """Only active when APP_PASSWORD is set."""
    if APP_PASSWORD and not password_ok(request.headers.get("authorization", "")):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Hoard"'})
    return await call_next(request)


async def csrf_guard(request: Request, call_next):
    """Changes need a custom header. Browsers can't send one cross-site without a CORS preflight
    (which Hoard never answers), so other websites can't trigger deletes or token changes."""
    if request.url.path.startswith("/api/") and request.method not in SAFE_METHODS:
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    return await call_next(request)


async def revalidate_ui(request: Request, call_next):
    """The UI is several ES module files. Browsers revalidate them every time (cheap 304s),
    so an update never runs a mix of old and new modules."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def install(app: FastAPI) -> None:
    # The last one added runs first: auth, then the CSRF check, then the cache header.
    app.middleware("http")(revalidate_ui)
    app.middleware("http")(csrf_guard)
    app.middleware("http")(basic_auth)
