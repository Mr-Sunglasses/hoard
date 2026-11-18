"""Hoard: a small self-hosted web app to download and keep Hugging Face models on a NAS.

This file only assembles the app. The pieces:

    config.py      settings from environment variables
    hub.py         Hugging Face: token, API client, readable errors
    storage.py     the models folder (names, metadata, scanning, disk space)
    jobs.py        download jobs (queue, one process each, retries, saved across restarts)
    verify.py      checking saved files against Hugging Face's hashes
    middleware.py  host check, password, CSRF guard, security and cache headers
    routes/        the HTTP API, one module per area
"""

import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from huggingface_hub.errors import HfHubHTTPError

from . import hub, jobs, middleware, storage
from .config import APP_PASSWORD, STATIC_DIR, VERSION
from .routes import browse, downloads, library, settings

log = logging.getLogger("hoard")
mimetypes.add_type("font/woff2", ".woff2")   # missing from some slim images


class QuietPolling(logging.Filter):
    """Leave the UI's frequent, successful polling out of the access log."""

    QUIET = {"/api/downloads", "/healthz"}

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            _, method, path, _, status = args
            return not (method == "GET" and status in (200, 304) and str(path).split("?")[0] in self.QUIET)
        return True


logging.getLogger("uvicorn.access").addFilter(QuietPolling())


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
    log.info("Hoard %s", VERSION)
    for problem in storage.setup_problems():
        log.error(problem)
    if not APP_PASSWORD:
        log.warning("APP_PASSWORD is not set: anyone who can reach this server can use Hoard.")
    jobs.restore()
    yield
    jobs.shutdown()


app = FastAPI(title="Hoard", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
middleware.install(app)
for module in (browse, downloads, library, settings):
    app.include_router(module.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(HfHubHTTPError)
async def hugging_face_error(_: Request, exc: HfHubHTTPError):
    return JSONResponse({"detail": hub.error_message(exc)}, status_code=hub.error_status(exc))


@app.get("/healthz", include_in_schema=False)
def health():
    """For Docker's health check. Needs no password and reveals nothing."""
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
