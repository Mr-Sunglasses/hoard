"""Download jobs.

Each download runs snapshot_download in its own process, so Stop can simply terminate it.
huggingface_hub keeps partial files under .cache/, so starting the same download again resumes.
A watcher thread per job measures the folder as progress, then records the result.

Jobs live in memory only: a restart forgets them (the files on disk stay).
"""

import multiprocessing as mp
import threading
import uuid
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from fastapi import HTTPException
from huggingface_hub import snapshot_download

from . import hub
from .config import MODELS_DIR
from .storage import folder_for, folder_size, read_meta, write_meta

FILE_WORKERS = 4        # parallel file downloads per job
PROGRESS_SECONDS = 1    # how often the folder is measured

_jobs: dict[str, dict] = {}
_procs: dict[str, mp.Process] = {}
_cancelled: set[str] = set()
_lock = threading.Lock()
_spawn = mp.get_context("spawn")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def selected(path: str, include: list[str] | None) -> bool:
    """Is a repo file part of the selection? `include` holds exact paths or globs; None means everything."""
    return not include or any(path == pattern or fnmatch(path, pattern) for pattern in include)


def merged_selection(existing: dict, repo_id: str, include: list[str] | None) -> list[str] | None:
    """Downloading more files into a model keeps the old ones tracked, so updates refresh them all."""
    if existing.get("repo_id") != repo_id:
        return include
    old = existing.get("include")
    if old is None or include is None:
        return None
    return sorted(set(old) | set(include))


# ---------- queries ----------


def all_jobs() -> list[dict]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)


def active_names() -> set[str]:
    """Folder names with a download running."""
    with _lock:
        return {j["name"] for j in _jobs.values() if j["status"] == "downloading"}


# ---------- actions ----------


def start(repo_id: str, revision: str, include: list[str] | None) -> dict:
    info = hub.client().model_info(repo_id, revision=revision, files_metadata=True)
    files = [(s.rfilename, s.size or 0) for s in info.siblings or [] if selected(s.rfilename, include)]
    if not files:
        raise HTTPException(400, "No files in this repo match the selection")

    name = folder_for(info.id)
    target = MODELS_DIR / name
    include = merged_selection(read_meta(target), info.id, include)
    meta = {   # written to .hoard.json when the download succeeds
        "repo_id": info.id,
        "revision": revision,   # the branch asked for
        "sha": info.sha,        # the exact commit downloaded
        "include": include,
        "license": hub.card_license(info),
        "pipeline_tag": info.pipeline_tag,
        "library_name": info.library_name,
        "gated": bool(info.gated),
    }

    with _lock:
        if any(j["name"] == name and j["status"] == "downloading" for j in _jobs.values()):
            raise HTTPException(409, "This model is already downloading")
        job = {
            "id": uuid.uuid4().hex[:8],
            "repo_id": info.id,
            "name": name,
            "revision": revision,
            "sha": info.sha,
            "include": include,
            "file_count": len(files),
            "total_bytes": sum(size for _, size in files),
            "done_bytes": 0,
            "status": "downloading",   # → done | error | cancelled
            "error": None,
            "started_at": now(),
            "finished_at": None,
        }
        _jobs[job["id"]] = job

    target.mkdir(parents=True, exist_ok=True)
    results = _spawn.Queue()
    # Pinned to the commit, so every file comes from one revision even if the repo changes meanwhile.
    # The token goes through the process pipe, never the command line.
    proc = _spawn.Process(
        target=_download,
        args=(info.id, info.sha, str(target), include, hub.get_token(), results),
        daemon=True,
    )
    proc.start()
    _procs[job["id"]] = proc
    threading.Thread(target=_watch, args=(job, proc, results, target, meta), daemon=True).start()
    return job


def cancel(job_id: str) -> None:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "No such download")
    proc = _procs.get(job_id)
    if job["status"] == "downloading" and proc:
        _cancelled.add(job_id)
        proc.terminate()


def clear_finished() -> None:
    with _lock:
        for job_id in [i for i, j in _jobs.items() if j["status"] != "downloading"]:
            del _jobs[job_id]


# ---------- workers ----------


def _download(repo_id: str, sha: str, local_dir: str, include: list[str] | None, token: str | None, results) -> None:
    """Runs in the download process."""
    try:
        snapshot_download(
            repo_id,
            token=token,
            revision=sha,
            local_dir=local_dir,
            allow_patterns=include or None,
            max_workers=FILE_WORKERS,
        )
        results.put(("done", None))
    except Exception as exc:  # anything goes back to the UI as the job's error
        results.put(("error", hub.first_line(exc)))


def _watch(job: dict, proc: mp.Process, results, target: Path, meta: dict) -> None:
    """Runs in a thread in the server: reports progress, then records how the download ended."""
    while proc.is_alive():
        job["done_bytes"] = folder_size(target)
        proc.join(timeout=PROGRESS_SECONDS)

    try:
        status, error = results.get(timeout=2)
    except Exception:
        status, error = "error", "Download process exited unexpectedly"
    if job["id"] in _cancelled:
        _cancelled.discard(job["id"])
        status, error = "cancelled", None

    if status == "done":
        write_meta(target, {**meta, "downloaded_at": now()})
        job["done_bytes"] = job["total_bytes"]
    job.update(status=status, error=error, finished_at=now())
    _procs.pop(job["id"], None)
