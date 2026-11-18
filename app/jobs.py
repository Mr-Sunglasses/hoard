"""Download jobs.

Each download runs snapshot_download in its own process, so Stop can simply terminate it.
huggingface_hub keeps partial files under .cache/, so starting the same download again resumes.

    queued ──▶ downloading ──▶ done
                  │   ▲
                  ▼   │ (network trouble: up to RETRY_DELAYS more tries)
               retrying
                  │
                  ▼
           error / cancelled

At most MAX_PARALLEL_DOWNLOADS run at once; the rest wait in order. The job list is saved to
CONFIG_DIR/jobs.json, and unfinished downloads continue by themselves after a restart.
While a download is unfinished, the model's .hoard.json says "incomplete": true.
"""

import json
import logging
import multiprocessing as mp
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

from . import hub, storage, verify
from .config import JOBS_FILE, MAX_PARALLEL_DOWNLOADS, MIN_FREE_BYTES, MODELS_DIR

log = logging.getLogger("hoard.jobs")

FILE_WORKERS = 4                 # parallel file downloads per job
PROGRESS_SECONDS = 1             # how often the folder is measured
RETRY_DELAYS = (15, 60, 300)     # seconds to wait before each automatic retry
HISTORY_LIMIT = 50               # finished jobs kept in the list

ACTIVE = ("queued", "downloading", "retrying")
RUNNING = ("downloading", "retrying")   # these hold one of the parallel slots

_jobs: dict[str, dict] = {}
_stop: dict[str, threading.Event] = {}
_procs: dict[str, mp.Process] = {}
_lock = threading.RLock()
_spawn = mp.get_context("spawn")
_closing = threading.Event()   # set at shutdown: the saved list stays as it was, to be continued


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
        jobs = sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)
        return [{k: v for k, v in job.items() if not k.startswith("_")} for job in jobs]


def exclusive():
    """Hold this while starting work on a model folder that must not overlap a download (a file
    check): no download can start meanwhile."""
    return _lock


def active_names() -> set[str]:
    """Folder names with a download queued or running."""
    with _lock:
        return {j["name"] for j in _jobs.values() if j["status"] in ACTIVE}


def _remaining(job: dict) -> int:
    """Bytes a job may still write."""
    if job["status"] == "queued":
        return job["needed_bytes"]
    return max(0, min(job["needed_bytes"], job["total_bytes"] - job["done_bytes"]))


def _check_space(needed: int) -> None:
    """Refuse a download that would leave less than MIN_FREE_BYTES, counting the other active ones."""
    pending = sum(_remaining(j) for j in _jobs.values() if j["status"] in ACTIVE)
    free = storage.free_bytes()
    if needed + pending > free - MIN_FREE_BYTES:
        others = f", plus {storage.human(pending)} for other downloads" if pending else ""
        raise HTTPException(
            507,
            f"Not enough space: this needs {storage.human(needed)}{others}, and {storage.human(free)} is free "
            f"(Hoard keeps {storage.human(MIN_FREE_BYTES)} spare).",
        )


# ---------- actions ----------


def start(
    repo_id: str, revision: str, include: list[str] | None, pin: str | None = None, again: list[str] = ()
) -> dict:
    """Queue a download of the selected files from the branch's latest commit (or the commit `pin`).

    Files in `again` (repo paths) are deleted and downloaded anew, but only once every check has
    passed, so a refused download never leaves a model with files missing."""
    info = hub.client().model_info(repo_id, revision=pin or revision, files_metadata=True)
    name = storage.folder_for(info.id)
    target = MODELS_DIR / name
    existing = storage.read_meta(target)
    if existing.get("repo_id") and existing["repo_id"] != info.id:
        # "a/b__c" and "a__b/c" share a folder name
        raise HTTPException(409, f"The folder {name} already holds {existing['repo_id']}")
    if not any(storage.selected(s.rfilename, include) for s in info.siblings or []):
        raise HTTPException(400, "No files in this repo match the selection")
    # Downloading more files into a model refreshes the ones it already has too, so count them all.
    include = merged_selection(existing, info.id, include)
    files = [(s.rfilename, s.size or 0) for s in info.siblings or [] if storage.selected(s.rfilename, include)]
    meta = {
        "repo_id": info.id,
        "revision": revision,   # the branch asked for
        "sha": info.sha,        # the exact commit being downloaded
        "include": include,
        "license": hub.card_license(info),
        "pipeline_tag": info.pipeline_tag,
        "library_name": info.library_name,
        "gated": bool(info.gated),
    }

    with _lock:
        if name in active_names():
            raise HTTPException(409, "This model is already downloading")
        if verify.busy(name):
            raise HTTPException(409, "Wait until the file check of this model has finished")
        needed = storage.bytes_missing(target, files, frozenset(again))
        _check_space(needed)
        if again:
            storage.discard_files(target, list(again))
        verify.forget(name)   # the files are about to change, so an earlier check no longer holds
        job = {
            "id": uuid.uuid4().hex[:8],
            "repo_id": info.id,
            "name": name,
            "revision": revision,
            "sha": info.sha,
            "include": include,
            "file_count": len(files),
            "total_bytes": sum(size for _, size in files),
            "needed_bytes": needed,
            "done_bytes": 0,
            "status": "queued",
            "attempt": 0,
            "error": None,
            "retry_at": None,
            "started_at": now(),
            "finished_at": None,
            "_meta": meta,     # becomes .hoard.json; kept out of API responses
            "_files": files,   # [path, size] of every selected file, for progress
        }
        _jobs[job["id"]] = job
        _stop[job["id"]] = threading.Event()
        _save()
        _fill_slots()
    return job


def cancel(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "No such download")
        if job["status"] not in ACTIVE:
            return
        _stop[job_id].set()
        if job["status"] == "queued":
            _finish(job, "cancelled", None)
        elif proc := _procs.get(job_id):
            proc.terminate()
        # a retrying job notices the event and ends itself


def clear_finished() -> None:
    with _lock:
        for job_id in [i for i, j in _jobs.items() if j["status"] not in ACTIVE]:
            del _jobs[job_id]
            _stop.pop(job_id, None)
        _save()


# ---------- scheduling ----------


def _fill_slots() -> None:
    """Start queued jobs, oldest first, while there are free slots. Call with _lock held."""
    if _closing.is_set():
        return
    running = sum(j["status"] in RUNNING for j in _jobs.values())
    for job in sorted(_jobs.values(), key=lambda j: j["started_at"]):
        if running >= MAX_PARALLEL_DOWNLOADS:
            break
        if job["status"] == "queued":
            job["status"] = "downloading"
            running += 1
            threading.Thread(target=_run, args=(job,), daemon=True, name=f"job-{job['id']}").start()
    _save()


def _finish(job: dict, status: str, error: str | None) -> None:
    """Record how a job ended and let the next one start. Call with _lock held."""
    job.update(status=status, error=error, retry_at=None, finished_at=now())
    if status == "done":
        job["done_bytes"] = job["total_bytes"]
    storage.forget(job["name"])
    _fill_slots()


def _run(job: dict) -> None:
    """Runs in a thread: one download, retried on network trouble, until it ends."""
    stop = _stop[job["id"]]
    target = MODELS_DIR / job["name"]
    # Older saved jobs have no _meta: fall back to what's on disk.
    meta = job.get("_meta") or {k: v for k, v in storage.read_meta(target).items() if k != "incomplete"}
    if stop.is_set():
        with _lock:
            _finish(job, "cancelled", None)
        return
    # Marked unfinished while files change, so a failed download never looks like a healthy model.
    try:
        target.mkdir(parents=True, exist_ok=True)
        storage.write_meta(target, {**meta, "incomplete": True})
    except OSError as exc:
        with _lock:
            _finish(job, "error", f"Can't write to {target}: {exc.strerror}")
        return
    status, error = "error", "Unknown error"
    for attempt, delay in enumerate((0, *RETRY_DELAYS), start=1):
        if delay:
            with _lock:
                job.update(status="retrying", retry_at=_in(delay), error=error)
                _save()
            if stop.wait(delay):
                break
        with _lock:
            job.update(status="downloading", attempt=attempt, retry_at=None, error=None)
        status, error, retry = _download_once(job, target, stop)
        if status != "error" or not retry:
            break
        log.warning("Download of %s failed (attempt %d): %s", job["repo_id"], attempt, error)

    storage.remove_partial_files(target)
    with _lock:
        if status == "done":
            storage.write_meta(target, {**meta, "sha": job["sha"], "downloaded_at": now()})
        elif stop.is_set():
            status, error = "cancelled", None
        _finish(job, status, error)


def _in(seconds: int) -> str:
    return datetime.fromtimestamp(datetime.now(UTC).timestamp() + seconds, UTC).isoformat(
        timespec="seconds"
    )


def _download_once(job: dict, target: Path, stop: threading.Event) -> tuple[str, str | None, bool]:
    """Run one download process and report progress until it exits. Returns (status, error, retry?)."""
    results = _spawn.Queue()
    # Pinned to the commit, so every file comes from one revision even if the repo changes meanwhile.
    # The token goes through the process pipe, never the command line.
    proc = _spawn.Process(
        target=_download,
        args=(job["repo_id"], job["sha"], str(target), job["include"], hub.get_token(), results),
        daemon=True,
    )
    storage.remove_partial_files(target)
    # Progress = selected files already in place + whatever the folder grows by. Measuring the whole
    # folder would also count files outside the selection.
    files = job.get("_files")   # missing in jobs saved by older versions
    present = job["total_bytes"] - storage.bytes_missing(target, files) if files else 0
    baseline = storage.folder_size(target)
    with _lock:
        if stop.is_set():
            return "cancelled", None, False
        proc.start()
        _procs[job["id"]] = proc

    disk_full = False
    while proc.is_alive():
        grown = max(0, storage.folder_size(target) - baseline)
        job["done_bytes"] = min(job["total_bytes"], present + grown)
        # Something else may be filling the disk too: stop before it's completely full.
        if storage.free_bytes() < MIN_FREE_BYTES // 2:
            disk_full = True
            proc.terminate()
        proc.join(timeout=PROGRESS_SECONDS)
    with _lock:
        _procs.pop(job["id"], None)

    try:
        status, error, retry = results.get(timeout=2)
    except Exception:
        status, error, retry = "error", "The download process stopped unexpectedly", True
    if status == "done":   # finished just as Stop was pressed: keep the finished model
        return status, None, False
    if disk_full:
        return "error", f"Stopped: less than {storage.human(MIN_FREE_BYTES // 2)} left on the disk", False
    return status, error, retry and not stop.is_set()


def _download(repo_id: str, sha: str, local_dir: str, include: list[str] | None, token: str | None, results) -> None:
    """Runs in the download process."""
    from huggingface_hub import snapshot_download

    try:
        snapshot_download(
            repo_id,
            token=token,
            revision=sha,
            local_dir=local_dir,
            allow_patterns=include or None,
            max_workers=FILE_WORKERS,
        )
        results.put(("done", None, False))
    except Exception as exc:  # anything goes back to the UI as the job's error
        results.put(("error", *hub.describe_failure(exc)))


# ---------- saving and restoring the list ----------


def _save() -> None:
    """Write the job list to CONFIG_DIR. Call with _lock held."""
    if _closing.is_set():
        return   # downloads being stopped by the shutdown must stay "unfinished" on disk
    finished = sorted((j for j in _jobs.values() if j["status"] not in ACTIVE), key=lambda j: j["started_at"])
    for old in finished[:-HISTORY_LIMIT]:
        _jobs.pop(old["id"], None)
        _stop.pop(old["id"], None)
    try:
        storage.write_json(JOBS_FILE, list(_jobs.values()))
    except OSError as exc:
        log.error("Couldn't save the download list to %s: %s", JOBS_FILE, exc.strerror)


def shutdown(timeout: float = 5) -> None:
    """Stop every download for a server shutdown. The saved list keeps them as unfinished, so they
    continue after the next start; finished files are kept as with Stop."""
    with _lock:
        _save()
        _closing.set()
        procs = list(_procs.values())
        if procs:
            log.info("Stopping %d download(s); they continue after the next start", len(procs))
        for proc in procs:
            proc.terminate()
    for proc in procs:
        proc.join(timeout)


def restore() -> None:
    """Load the saved list at startup and continue unfinished downloads from where they stopped."""
    try:
        saved = json.loads(JOBS_FILE.read_text())
    except FileNotFoundError:
        saved = []
    except (OSError, ValueError) as exc:
        log.error("Couldn't read the download list %s: %s", JOBS_FILE, exc)
        return

    with _lock:
        for job in saved:
            if not isinstance(job, dict) or "id" not in job or "name" not in job:
                continue
            job.setdefault("needed_bytes", job.get("total_bytes", 0))
            job.setdefault("attempt", 0)
            job.setdefault("retry_at", None)
            if job.get("status") in ACTIVE:
                # A job that has run made its folder; if that's gone, it was deleted while Hoard was
                # stopped. A job still waiting in the queue hasn't made one yet, so keep it.
                started = job.get("status") != "queued" or job.get("attempt", 0) > 0
                if started and not (MODELS_DIR / job["name"]).is_dir():
                    log.info("Not continuing the download of %s: its folder was deleted", job["repo_id"])
                    continue
                log.info("Continuing the download of %s", job["repo_id"])
                job.update(status="queued", retry_at=None, error=None)
            _jobs[job["id"]] = job
            _stop[job["id"]] = threading.Event()
        _fill_slots()
    _remove_stale_partials()


def _remove_stale_partials() -> None:
    """Free space taken by downloads interrupted before this start (e.g. by a power cut)."""
    busy = active_names()
    try:
        folders = storage.model_folders()
    except OSError:
        return   # models folder unreadable; already reported as a setup problem
    for folder in folders:
        if folder.name not in busy and (freed := storage.remove_partial_files(folder)):
            log.info("Removed %s of unfinished temp files from %s", storage.human(freed), folder.name)
