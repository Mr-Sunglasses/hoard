"""The models folder. There's no database: each model is a folder, and the folder is the state.

    MODELS_DIR/<org>__<name>/             the repo's files, in their normal layout
    MODELS_DIR/<org>__<name>/.hoard.json  what Hoard downloaded (repo, commit, file selection, …)
    MODELS_DIR/<org>__<name>/.cache/      huggingface_hub's resume data
"""

import json
import os
import shutil
import tempfile
import threading
import time
from fnmatch import fnmatch
from pathlib import Path

from fastapi import HTTPException

from .config import CONFIG_DIR, MIN_FREE_BYTES, MODELS_DIR

META_FILE = ".hoard.json"
CACHE_DIR = ".cache"
SUMMARY_TTL_SECONDS = 300   # rescan folders changed outside Hoard at most this often

# File extension → the format name shown in the UI.
FORMATS = {
    ".safetensors": "safetensors",
    ".gguf": "gguf",
    ".bin": "bin",
    ".pt": "pytorch",
    ".pth": "pytorch",
    ".ckpt": "pytorch",
    ".onnx": "onnx",
    ".h5": "keras",
    ".keras": "keras",
    ".msgpack": "flax",
    ".mlmodel": "coreml",
    ".tflite": "tflite",
}

# Formats built on Python pickle: loading them with torch.load() can run arbitrary code.
PICKLE_EXTENSIONS = {".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle"}


def is_pickle(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in PICKLE_EXTENSIONS


def selected(path: str, include: list[str] | None) -> bool:
    """Is a repo file part of a selection? `include` holds exact paths or globs; None means everything."""
    return not include or any(path == pattern or fnmatch(path, pattern) for pattern in include)


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


# ---------- names ----------


def folder_for(repo_id: str) -> str:
    """"Qwen/Qwen2.5-7B" → "Qwen__Qwen2.5-7B"."""
    return repo_id.replace("/", "__")


def model_dir(name: str) -> Path:
    """Folder for a name that came from a URL. Rejects anything that could escape MODELS_DIR."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "Invalid model name")
    path = (MODELS_DIR / name).resolve()
    if path.parent != MODELS_DIR:
        raise HTTPException(400, "Invalid model name")
    return path


def existing_model_dir(name: str) -> Path:
    path = model_dir(name)
    if not path.is_dir():
        raise HTTPException(404, "Not found")
    return path


def inside(folder: Path, relative: str) -> Path | None:
    """folder/relative, or None if that would point outside the folder."""
    path = (folder / relative).resolve()
    return path if path.is_relative_to(folder.resolve()) else None


# ---------- metadata ----------


def read_meta(path: Path) -> dict:
    """The folder's .hoard.json, or {} for folders Hoard didn't download."""
    try:
        return json.loads((path / META_FILE).read_text())
    except (OSError, ValueError):
        return {}


def write_json(file: Path, data) -> None:
    """Write via a temp file + rename, so a crash or full disk never leaves half a file."""
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=f".{file.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, file)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_meta(path: Path, meta: dict) -> None:
    write_json(path / META_FILE, meta)
    forget(path.name)


def is_in_library(repo_id: str) -> bool:
    meta = read_meta(MODELS_DIR / folder_for(repo_id))
    return bool(meta) and not meta.get("incomplete")


# ---------- scanning ----------


def _files(path: Path, include_cache: bool = False):
    """Yield (relative path, size) for every file in a model folder, except Hoard's metadata."""
    for root, dirs, files in os.walk(path):
        if not include_cache:
            dirs[:] = [d for d in dirs if d != CACHE_DIR]
        for name in files:
            if name == META_FILE:
                continue
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:   # removed while we were walking
                continue
            yield os.path.relpath(full, path), size


def list_files(path: Path) -> list[dict]:
    files = ({"path": rel, "size": size, "pickle": is_pickle(rel)} for rel, size in _files(path))
    return sorted(files, key=lambda f: f["path"])


def folder_size(path: Path) -> int:
    """Everything on disk, resume data included. Used as download progress."""
    return sum(size for _, size in _files(path, include_cache=True))


def remove_partial_files(path: Path) -> int:
    """Delete half-downloaded files left by an interrupted download. huggingface_hub gives every
    attempt a fresh temp name, so old ones are never reused, only take space. Call only while no
    download is writing to this folder. Returns the bytes freed."""
    freed = 0
    for partial in (path / CACHE_DIR / "huggingface" / "download").rglob("*.incomplete"):
        try:
            freed += partial.stat().st_size
            partial.unlink()
        except OSError:
            pass
    return freed


def bytes_missing(path: Path, files: list[tuple[str, int]], again: frozenset[str] = frozenset()) -> int:
    """How much of a file list still has to be downloaded into a folder. Files in `again` count
    as missing even if present (they are about to be replaced)."""
    missing = 0
    for rel, size in files:
        local = inside(path, rel)
        try:
            if rel not in again and local and local.stat().st_size == size:
                continue
        except OSError:
            pass
        missing += size
    return missing


def discard_files(path: Path, relatives: list[str]) -> None:
    """Delete files so the next download fetches them again. huggingface_hub's .metadata note says
    a file is complete; without it the file is downloaded again."""
    for rel in relatives:
        for file in (inside(path, rel), inside(path, f"{CACHE_DIR}/huggingface/download/{rel}.metadata")):
            if file:
                file.unlink(missing_ok=True)
    forget(path.name)


def _summarize(path: Path) -> dict:
    meta = read_meta(path)
    size, count, formats, pickle = 0, 0, set(), False
    for rel, file_size in _files(path):
        size += file_size
        count += 1
        if fmt := FORMATS.get(os.path.splitext(rel)[1].lower()):
            formats.add(fmt)
        pickle = pickle or is_pickle(rel)
    return {
        **meta,
        "name": path.name,
        "repo_id": meta.get("repo_id") or path.name.replace("__", "/"),
        "managed": bool(meta),   # False for folders copied in by hand
        "incomplete": bool(meta.get("incomplete")),
        "size": size,
        "file_count": count,
        "formats": sorted(formats),
        "pickle": pickle,
    }


# Scanning every model on each page load would wake sleeping disks, so summaries are cached.
# Hoard forgets a folder's summary whenever it changes it; other changes show up within the TTL.
_summaries: dict[str, tuple[float, dict]] = {}
_summaries_lock = threading.Lock()


def summarize(path: Path) -> dict:
    """One library entry: the saved metadata plus what's actually on disk."""
    with _summaries_lock:
        cached = _summaries.get(path.name)
    if cached and time.monotonic() - cached[0] < SUMMARY_TTL_SECONDS:
        return cached[1]
    summary = _summarize(path)
    with _summaries_lock:
        _summaries[path.name] = (time.monotonic(), summary)
    return summary


def forget(name: str | None = None) -> None:
    """Drop cached summaries (one folder, or all)."""
    with _summaries_lock:
        if name is None:
            _summaries.clear()
        else:
            _summaries.pop(name, None)


def model_folders() -> list[Path]:
    """Every model folder, sorted by name. Hidden folders (like .hf-home) are skipped."""
    if not MODELS_DIR.is_dir():
        return []
    folders = [d for d in MODELS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    return sorted(folders, key=lambda d: d.name.lower())


def delete_model(path: Path) -> None:
    shutil.rmtree(path)
    forget(path.name)


# ---------- disk ----------


def _existing(path: Path) -> Path:
    """The nearest folder that exists, so disk space can be read before MODELS_DIR is created."""
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def free_bytes() -> int:
    return shutil.disk_usage(_existing(MODELS_DIR)).free


def disk_usage() -> dict:
    usage = shutil.disk_usage(_existing(MODELS_DIR))
    return {"total": usage.total, "used": usage.used, "free": usage.free, "reserve": MIN_FREE_BYTES}


_setup_ok = False   # once both folders were writable, stop testing on every library load


def setup_problems() -> list[str]:
    """Folders the server can't write to, explained so the UI and logs can say how to fix it.

    Testing means writing a file, which would wake sleeping disks on every library load, so after
    one clean result it isn't repeated. While there are problems it is, so a fix shows up at once."""
    global _setup_ok
    if _setup_ok:
        return []
    problems = []
    who = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "the server"
    for folder, purpose in ((MODELS_DIR, "save models"), (CONFIG_DIR, "save the token and download list")):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=folder):
                pass
        except OSError as exc:
            problems.append(
                f"Can't write to {folder} ({exc.strerror}), so Hoard can't {purpose}. "
                f"Make it writable for user {who}, e.g. `sudo chown -R {who} <host folder>`."
            )
    _setup_ok = not problems
    return problems
