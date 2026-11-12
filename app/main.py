"""Hoard: a small self-hosted web app to download and keep Hugging Face models on a NAS.

This file only assembles the app. The pieces:

    config.py      settings from environment variables
    hub.py         Hugging Face: token, API client, readable errors
    storage.py     the models folder (names, metadata, scanning)
    jobs.py        download jobs (one process each)
    middleware.py  password, CSRF guard, cache headers
    routes/        the HTTP API, one module per area
"""

import mimetypes

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from huggingface_hub.errors import HfHubHTTPError

from . import hub, middleware
from .config import STATIC_DIR
from .routes import browse, downloads, library, settings

mimetypes.add_type("font/woff2", ".woff2")   # missing from some slim images

app = FastAPI(title="Hoard")
middleware.install(app)
for module in (browse, downloads, library, settings):
    app.include_router(module.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(HfHubHTTPError)
async def hugging_face_error(_: Request, exc: HfHubHTTPError):
    return JSONResponse({"detail": hub.error_message(exc)}, status_code=hub.error_status(exc))


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
