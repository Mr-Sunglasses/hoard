"""Starting, watching and stopping downloads."""

from fastapi import APIRouter
from pydantic import BaseModel

from .. import jobs

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


class DownloadRequest(BaseModel):
    repo_id: str
    revision: str = "main"
    include: list[str] | None = None   # exact file paths or glob patterns; None = whole repo


@router.post("")
def start_download(req: DownloadRequest):
    return jobs.start(req.repo_id.strip(), req.revision or "main", req.include)


@router.get("")
def list_downloads():
    return jobs.all_jobs()


@router.delete("/{job_id}")
def stop_download(job_id: str):
    jobs.cancel(job_id)
    return {"ok": True}


@router.post("/clear")
def clear_finished():
    jobs.clear_finished()
    return {"ok": True}
