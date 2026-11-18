"""Checking a saved model against Hugging Face: is every file there, and unchanged?

Large (LFS) files are compared by SHA-256, small ones by their git blob hash, both as published
by Hugging Face for the exact commit the model was downloaded from. Hashing many gigabytes takes
a while, so it runs in the background (one model at a time, to go easy on the disks) and the UI polls.
"""

import hashlib
import threading
from pathlib import Path

from fastapi import HTTPException

from . import hub, storage

CHUNK = 8 * 1024 * 1024

_runs: dict[str, dict] = {}
_lock = threading.Lock()
_one_at_a_time = threading.Semaphore(1)


def status(name: str) -> dict | None:
    with _lock:
        run = _runs.get(name)
        return dict(run) if run else None


def _busy(name: str) -> bool:
    return (_runs.get(name) or {}).get("status") in ("waiting", "running")


def busy(name: str) -> bool:
    """Is this model being checked (or waiting its turn)?"""
    with _lock:
        return _busy(name)


def forget(name: str) -> None:
    with _lock:
        _runs.pop(name, None)


def start(name: str, path: Path, meta: dict) -> dict:
    with _lock:
        if _busy(name):
            raise HTTPException(409, "Already checking this model")
        run = _runs[name] = {
            "status": "waiting",   # → running → done | error
            "checked_bytes": 0,
            "total_bytes": 0,
            "file_count": 0,
            "problems": [],        # [{path, issue}]
            "error": None,
        }
    threading.Thread(target=_run, args=(run, path, meta), daemon=True).start()
    return dict(run)


def _run(run: dict, path: Path, meta: dict) -> None:
    with _one_at_a_time:
        run["status"] = "running"
        try:
            _check(run, path, meta)
            run["status"] = "done"
        except Exception as exc:
            run.update(status="error", error=hub.error_message(exc))


def _check(run: dict, path: Path, meta: dict) -> None:
    info = hub.client().model_info(meta["repo_id"], revision=meta["sha"], files_metadata=True)
    expected = [s for s in info.siblings or [] if storage.selected(s.rfilename, meta.get("include"))]
    run.update(file_count=len(expected), total_bytes=sum(s.size or 0 for s in expected))

    for sibling in expected:
        local = storage.inside(path, sibling.rfilename)
        if local is None or not local.is_file():
            run["problems"].append({"path": sibling.rfilename, "issue": "missing"})
            run["checked_bytes"] += sibling.size or 0
            continue
        size = local.stat().st_size
        if sibling.size is not None and size != sibling.size:
            run["problems"].append({"path": sibling.rfilename, "issue": "wrong size"})
            run["checked_bytes"] += sibling.size
            continue
        if sibling.lfs:
            ok = _sha256(local, run) == sibling.lfs.sha256
        else:
            ok = _git_blob_sha1(local, size, run) == sibling.blob_id
        if not ok:
            run["problems"].append({"path": sibling.rfilename, "issue": "changed or corrupted"})


def _hash_into(digest, file: Path, run: dict) -> str:
    with open(file, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
            run["checked_bytes"] += len(chunk)
    return digest.hexdigest()


def _sha256(file: Path, run: dict) -> str:
    return _hash_into(hashlib.sha256(), file, run)


def _git_blob_sha1(file: Path, size: int, run: dict) -> str:
    """The id git gives a file's content: sha1 of "blob <size>\\0" + the bytes."""
    digest = hashlib.sha1(f"blob {size}\0".encode())
    return _hash_into(digest, file, run)
