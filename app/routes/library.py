"""The models on disk: listing, files, update checks, updates and deletes."""

import shutil

from fastapi import APIRouter, HTTPException

from .. import hub, jobs, storage
from ..config import MODELS_DIR

router = APIRouter(prefix="/api/library", tags=["library"])


def managed_meta(name: str) -> dict:
    """.hoard.json for a model Hoard downloaded; folders copied in by hand can't be updated."""
    meta = storage.read_meta(storage.model_dir(name))
    if not meta:
        raise HTTPException(400, "This folder was not downloaded by Hoard")
    return meta


@router.get("")
def library():
    active = jobs.active_names()
    models = [
        {**storage.summarize(folder), "downloading": folder.name in active}
        for folder in storage.model_folders()
    ]
    return {"models_dir": str(MODELS_DIR), "models": models, "disk": storage.disk_usage()}


@router.get("/{name}/files")
def files(name: str):
    return storage.list_files(storage.existing_model_dir(name))


@router.get("/{name}/check")
def check_update(name: str):
    meta = managed_meta(name)
    latest = hub.client().model_info(meta["repo_id"], revision=meta.get("revision", "main")).sha
    return {"update_available": latest != meta.get("sha"), "latest_sha": latest, "local_sha": meta.get("sha")}


@router.post("/{name}/update")
def update(name: str):
    meta = managed_meta(name)
    return jobs.start(meta["repo_id"], meta.get("revision", "main"), meta.get("include"))


@router.delete("/{name}")
def delete(name: str):
    path = storage.existing_model_dir(name)
    if name in jobs.active_names():
        raise HTTPException(409, "Cancel the running download first")
    shutil.rmtree(path)
    return {"ok": True}
