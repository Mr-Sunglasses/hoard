"""The models folder. There's no database: each model is a folder, and the folder is the state.

    MODELS_DIR/<org>__<name>/             the repo's files, in their normal layout
    MODELS_DIR/<org>__<name>/.hoard.json  what Hoard downloaded (repo, commit, file selection, …)
    MODELS_DIR/<org>__<name>/.cache/      huggingface_hub's resume data
"""

import json
import os
import shutil
from pathlib import Path

from fastapi import HTTPException

from .config import MODELS_DIR

META_FILE = ".hoard.json"
CACHE_DIR = ".cache"

# File extension → the format name shown in the UI.
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


# ---------- metadata ----------


def read_meta(path: Path) -> dict:
    """The folder's .hoard.json, or {} for folders Hoard didn't download."""
    try:
        return json.loads((path / META_FILE).read_text())
    except (OSError, ValueError):
        return {}


def write_meta(path: Path, meta: dict) -> None:
    (path / META_FILE).write_text(json.dumps(meta, indent=2))


def is_in_library(repo_id: str) -> bool:
    return (MODELS_DIR / folder_for(repo_id) / META_FILE).exists()


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
    return sorted(({"path": rel, "size": size} for rel, size in _files(path)), key=lambda f: f["path"])


def folder_size(path: Path) -> int:
    """Everything on disk, resume data included. Used as download progress."""
    return sum(size for _, size in _files(path, include_cache=True))


def summarize(path: Path) -> dict:
    """One library entry: the saved metadata plus what's actually on disk."""
    meta = read_meta(path)
    size, count, formats = 0, 0, set()
    for rel, file_size in _files(path):
        size += file_size
        count += 1
        if fmt := FORMATS.get(os.path.splitext(rel)[1].lower()):
            formats.add(fmt)
    return {
        **meta,
        "name": path.name,
        "repo_id": meta.get("repo_id") or path.name.replace("__", "/"),
        "managed": bool(meta),   # False for folders copied in by hand
        "size": size,
        "file_count": count,
        "formats": sorted(formats),
    }


def model_folders() -> list[Path]:
    """Every model folder, sorted by name. Hidden folders (like .hf-home) are skipped."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    folders = [d for d in MODELS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    return sorted(folders, key=lambda d: d.name.lower())


def disk_usage() -> dict:
    usage = shutil.disk_usage(MODELS_DIR)
    return {"total": usage.total, "used": usage.used, "free": usage.free}
