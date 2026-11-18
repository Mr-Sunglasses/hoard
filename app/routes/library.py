"""The models on disk: listing, files, update checks, updates, verification and deletes."""

from fastapi import APIRouter, HTTPException

from .. import hub, jobs, storage, verify
from ..config import MODELS_DIR

router = APIRouter(prefix="/api/library", tags=["library"])


def managed_meta(name: str) -> dict:
    """.hoard.json for a model Hoard downloaded; folders copied in by hand can't be updated."""
    meta = storage.read_meta(storage.model_dir(name))
    if not meta:
        raise HTTPException(400, "This folder was not downloaded by Hoard")
    return meta


@router.get("")
def library(refresh: bool = False):
    if refresh:
        storage.forget()
    active = jobs.active_names()
    models = [
        {**storage.summarize(folder), "downloading": folder.name in active}
        for folder in storage.model_folders()
    ]
    return {
        "models_dir": str(MODELS_DIR),
        "models": models,
        "disk": storage.disk_usage(),
        "problems": storage.setup_problems(),
    }


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
    """Also continues an unfinished download."""
    meta = managed_meta(name)
    return jobs.start(meta["repo_id"], meta.get("revision", "main"), meta.get("include"))


@router.post("/{name}/verify")
def start_verify(name: str):
    path = storage.existing_model_dir(name)
    with jobs.exclusive():
        meta = managed_meta(name)
        if meta.get("incomplete") or name in jobs.active_names():
            raise HTTPException(409, "Finish the download before checking the files")
        return verify.start(name, path, meta)


@router.get("/{name}/verify")
def verify_status(name: str):
    storage.model_dir(name)
    result = verify.status(name)
    if result is None:
        raise HTTPException(404, "This model hasn't been checked yet")
    return result


@router.post("/{name}/repair")
def repair(name: str):
    """Download the files the last check found damaged or missing again."""
    storage.existing_model_dir(name)
    meta = managed_meta(name)
    result = verify.status(name)
    if not result or result["status"] != "done" or not result["problems"]:
        raise HTTPException(400, "Check the files first; there's nothing to repair")
    broken = [problem["path"] for problem in result["problems"]]
    return jobs.start(
        meta["repo_id"], meta.get("revision", "main"), meta.get("include"), pin=meta.get("sha"), again=broken
    )


@router.delete("/{name}")
def delete(name: str):
    path = storage.existing_model_dir(name)
    if name in jobs.active_names():
        raise HTTPException(409, "Stop the download first")
    if verify.busy(name):
        raise HTTPException(409, "Wait until the file check of this model has finished")
    storage.delete_model(path)
    verify.forget(name)
    return {"ok": True}
