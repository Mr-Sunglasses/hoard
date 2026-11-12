"""Hoard: a small self-hosted web app to download and keep Hugging Face models on a NAS."""

import base64
import json
import mimetypes
import multiprocessing as mp
import os
import secrets
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError
from pydantic import BaseModel

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/models")).resolve()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
TOKEN_FILE = CONFIG_DIR / "hf_token"
META_FILE = ".hoard.json"
STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("font/woff2", ".woff2")

FORMATS = {
    ".safetensors": "safetensors",
    ".gguf": "gguf",
    ".bin": "bin",
    ".pt": "pytorch",
    ".pth": "pytorch",
    ".onnx": "onnx",
    ".h5": "keras",
    ".keras": "keras",
    ".msgpack": "flax",
    ".mlmodel": "coreml",
    ".tflite": "tflite",
}

app = FastAPI(title="Hoard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

jobs: dict[str, dict] = {}
procs: dict[str, mp.Process] = {}
jobs_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- Hugging Face token ----------
# Order: HF_TOKEN env var (set by the admin) wins, then a token saved from the UI.

def env_token() -> str | None:
    return os.environ.get("HF_TOKEN", "").strip() or None


def ui_token() -> str | None:
    try:
        return TOKEN_FILE.read_text().strip() or None
    except OSError:
        return None


def get_token() -> str | None:
    return env_token() or ui_token()


def hf() -> HfApi:
    return HfApi(token=get_token())


# ---------- auth (optional) ----------

@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    # A custom header can't be sent cross-site without a CORS preflight (which we never allow),
    # so other websites open in the same browser can't trigger deletes or token changes.
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-requested-with") != "hoard":
            return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if APP_PASSWORD:
        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw, APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Hoard"'})
    return await call_next(request)


@app.exception_handler(HfHubHTTPError)
async def hf_error(_: Request, exc: HfHubHTTPError):
    status = getattr(getattr(exc, "response", None), "status_code", 502) or 502
    if isinstance(exc, GatedRepoError):
        msg = "This model is gated. Accept its terms on huggingface.co and add a Hugging Face token in Settings."
    elif isinstance(exc, RepositoryNotFoundError):
        msg = "Model not found (or it is private and no Hugging Face token is set)."
    else:
        msg = str(exc).strip().split("\n")[0]
    return JSONResponse({"detail": msg}, status_code=status if 400 <= status < 600 else 502)


# ---------- helpers ----------

def folder_for(repo_id: str) -> str:
    return repo_id.replace("/", "__")


def model_dir(name: str) -> Path:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "Invalid model name")
    path = (MODELS_DIR / name).resolve()
    if path.parent != MODELS_DIR:
        raise HTTPException(400, "Invalid model name")
    return path


def scan(path: Path, include_cache: bool = False) -> tuple[int, int, list[str]]:
    total, count, formats = 0, 0, set()
    for root, dirs, files in os.walk(path):
        if not include_cache:
            dirs[:] = [d for d in dirs if d != ".cache"]
        for f in files:
            if f == META_FILE:
                continue
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
            count += 1
            ext = os.path.splitext(f)[1].lower()
            if ext in FORMATS:
                formats.add(FORMATS[ext])
    return total, count, sorted(formats)


def read_meta(path: Path) -> dict:
    try:
        return json.loads((path / META_FILE).read_text())
    except (OSError, ValueError):
        return {}


def matches(path: str, include: list[str] | None) -> bool:
    return not include or any(path == p or fnmatch(path, p) for p in include)


def card_license(info) -> str | None:
    card = getattr(info, "card_data", None)
    if not card:
        return None
    lic = card.to_dict().get("license")
    return ", ".join(lic) if isinstance(lic, list) else lic


def active_names() -> set[str]:
    with jobs_lock:
        return {j["name"] for j in jobs.values() if j["status"] == "downloading"}


# ---------- Hugging Face browsing ----------

@app.get("/api/search")
def search(q: str = "", sort: str = "downloads", task: str = "", limit: int = 30):
    if sort not in ("downloads", "likes", "last_modified", "trending_score", "created_at"):
        sort = "downloads"
    results = hf().list_models(
        search=q or None,
        pipeline_tag=task or None,
        sort=sort,
        limit=max(1, min(limit, 100)),
        expand=["downloads", "likes", "pipeline_tag", "library_name", "lastModified"],
    )
    return [
        {
            "id": m.id,
            "downloads": getattr(m, "downloads", None),
            "likes": getattr(m, "likes", None),
            "pipeline_tag": getattr(m, "pipeline_tag", None),
            "library_name": getattr(m, "library_name", None),
            "last_modified": str(m.last_modified) if getattr(m, "last_modified", None) else None,
        }
        for m in results
    ]


@app.get("/api/repo")
def repo(repo_id: str, revision: str = "main"):
    info = hf().model_info(repo_id, revision=revision, files_metadata=True)
    files = sorted(
        ({"path": s.rfilename, "size": s.size or 0} for s in info.siblings or []),
        key=lambda f: f["path"],
    )
    local = MODELS_DIR / folder_for(info.id)
    return {
        "id": info.id,
        "sha": info.sha,
        "gated": info.gated,
        "pipeline_tag": info.pipeline_tag,
        "library_name": info.library_name,
        "license": card_license(info),
        "downloads": info.downloads,
        "likes": info.likes,
        "files": files,
        "in_library": (local / META_FILE).exists(),
    }


# ---------- downloads ----------

class DownloadRequest(BaseModel):
    repo_id: str
    revision: str = "main"
    include: list[str] | None = None  # exact file paths or glob patterns; None = whole repo


def _worker(repo_id: str, sha: str, local_dir: str, include: list[str] | None, token: str | None, queue) -> None:
    try:
        snapshot_download(
            repo_id,
            token=token,
            revision=sha,
            local_dir=local_dir,
            allow_patterns=include or None,
            max_workers=4,
        )
        queue.put(("ok", ""))
    except Exception as exc:  # reported back to the UI
        queue.put(("error", str(exc).strip().split("\n")[0]))


def _monitor(jid: str, proc: mp.Process, queue, target: Path, meta: dict) -> None:
    job = jobs[jid]
    while proc.is_alive():
        job["done_bytes"] = scan(target, include_cache=True)[0]
        proc.join(timeout=1)
    try:
        status, msg = queue.get(timeout=2)
    except Exception:
        status, msg = "error", "Download process exited unexpectedly"
    if job.get("cancel"):
        status, msg = "cancelled", None
    if status == "ok":
        meta["downloaded_at"] = now()
        (target / META_FILE).write_text(json.dumps(meta, indent=2))
        job.update(status="done", done_bytes=job["total_bytes"])
    else:
        job.update(status=status, error=msg)
    job["finished_at"] = now()
    procs.pop(jid, None)


def start_job(repo_id: str, revision: str, include: list[str] | None) -> dict:
    info = hf().model_info(repo_id, revision=revision, files_metadata=True)
    files = [(s.rfilename, s.size or 0) for s in info.siblings or [] if matches(s.rfilename, include)]
    if not files:
        raise HTTPException(400, "No files in this repo match the selection")

    name = folder_for(info.id)
    target = MODELS_DIR / name
    existing = read_meta(target)
    # Keep everything already downloaded tracked: merge file selections for future updates.
    if existing and existing.get("repo_id") == info.id:
        old = existing.get("include")
        include = None if (old is None or include is None) else sorted(set(old) | set(include))

    meta = {
        "repo_id": info.id,
        "revision": revision,
        "sha": info.sha,
        "include": include,
        "license": card_license(info),
        "pipeline_tag": info.pipeline_tag,
        "library_name": info.library_name,
        "gated": bool(info.gated),
    }

    with jobs_lock:
        if any(j["name"] == name and j["status"] == "downloading" for j in jobs.values()):
            raise HTTPException(409, "This model is already downloading")
        jid = uuid.uuid4().hex[:8]
        job = {
            "id": jid,
            "repo_id": info.id,
            "name": name,
            "revision": revision,
            "sha": info.sha,
            "include": include,
            "file_count": len(files),
            "total_bytes": sum(size for _, size in files),
            "done_bytes": 0,
            "status": "downloading",
            "error": None,
            "started_at": now(),
            "finished_at": None,
        }
        jobs[jid] = job

    target.mkdir(parents=True, exist_ok=True)
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_worker, args=(info.id, info.sha, str(target), include, get_token(), queue), daemon=True)
    proc.start()
    procs[jid] = proc
    threading.Thread(target=_monitor, args=(jid, proc, queue, target, meta), daemon=True).start()
    return job


@app.post("/api/downloads")
def create_download(req: DownloadRequest):
    return start_job(req.repo_id.strip(), req.revision or "main", req.include)


@app.get("/api/downloads")
def list_downloads():
    with jobs_lock:
        return sorted(jobs.values(), key=lambda j: j["started_at"], reverse=True)


@app.delete("/api/downloads/{jid}")
def cancel_download(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "No such download")
    proc = procs.get(jid)
    if job["status"] == "downloading" and proc:
        job["cancel"] = True
        proc.terminate()
    return {"ok": True}


@app.post("/api/downloads/clear")
def clear_downloads():
    with jobs_lock:
        for jid in [k for k, j in jobs.items() if j["status"] != "downloading"]:
            del jobs[jid]
    return {"ok": True}


# ---------- local library ----------

@app.get("/api/library")
def library():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    active = active_names()
    models = []
    for d in sorted(MODELS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        meta = read_meta(d)
        size, count, formats = scan(d)
        models.append({
            **meta,
            "name": d.name,
            "repo_id": meta.get("repo_id") or d.name.replace("__", "/"),
            "managed": bool(meta),
            "size": size,
            "file_count": count,
            "formats": formats,
            "downloading": d.name in active,
        })
    usage = shutil.disk_usage(MODELS_DIR)
    return {
        "models_dir": str(MODELS_DIR),
        "models": models,
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
    }


@app.get("/api/library/{name}/files")
def library_files(name: str):
    path = model_dir(name)
    if not path.is_dir():
        raise HTTPException(404, "Not found")
    out = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".cache"]
        for f in files:
            if f == META_FILE:
                continue
            full = Path(root) / f
            out.append({"path": str(full.relative_to(path)), "size": full.stat().st_size})
    return sorted(out, key=lambda f: f["path"])


@app.get("/api/library/{name}/check")
def check_update(name: str):
    meta = read_meta(model_dir(name))
    if not meta:
        raise HTTPException(400, "This folder was not downloaded by Hoard")
    info = hf().model_info(meta["repo_id"], revision=meta.get("revision", "main"))
    return {"update_available": info.sha != meta.get("sha"), "latest_sha": info.sha, "local_sha": meta.get("sha")}


@app.post("/api/library/{name}/update")
def update_model(name: str):
    meta = read_meta(model_dir(name))
    if not meta:
        raise HTTPException(400, "This folder was not downloaded by Hoard")
    return start_job(meta["repo_id"], meta.get("revision", "main"), meta.get("include"))


@app.delete("/api/library/{name}")
def delete_model(name: str):
    path = model_dir(name)
    if not path.is_dir():
        raise HTTPException(404, "Not found")
    if name in active_names():
        raise HTTPException(409, "Cancel the running download first")
    shutil.rmtree(path)
    return {"ok": True}


# ---------- settings: Hugging Face token ----------

class TokenRequest(BaseModel):
    token: str


def describe_token(token: str) -> dict:
    who = HfApi(token=token).whoami()
    auth = (who.get("auth") or {}).get("accessToken") or {}
    return {"user": who.get("name"), "token_name": auth.get("displayName"), "role": auth.get("role")}


@app.get("/api/settings/token")
def token_status():
    source = "env" if env_token() else "ui" if ui_token() else None
    out = {"source": source, "ui_writable": not env_token(), "protected": bool(APP_PASSWORD)}
    token = get_token()
    if token:
        out["hint"] = f"hf_…{token[-4:]}"
        try:
            out.update(describe_token(token))
            out["valid"] = True
        except HfHubHTTPError:
            out["valid"] = False
    return out


@app.put("/api/settings/token")
def set_token(req: TokenRequest):
    if env_token():
        raise HTTPException(409, "HF_TOKEN is set on the server, remove it there first to manage the token here")
    token = req.token.strip()
    if not token.startswith("hf_") or len(token) < 20 or any(c.isspace() for c in token):
        raise HTTPException(400, "That doesn't look like a Hugging Face token (they start with hf_)")
    try:
        info = describe_token(token)
    except HfHubHTTPError:
        raise HTTPException(400, "Hugging Face rejected this token. Check that it's copied correctly and not revoked")
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = TOKEN_FILE.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        os.chmod(tmp, 0o600)
        os.replace(tmp, TOKEN_FILE)
    except OSError as exc:
        raise HTTPException(500, f"Could not save the token in {CONFIG_DIR}: {exc.strerror}")
    return {"ok": True, **info}


@app.delete("/api/settings/token")
def delete_token():
    if env_token():
        raise HTTPException(409, "This token comes from HF_TOKEN on the server and can only be removed there")
    TOKEN_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
