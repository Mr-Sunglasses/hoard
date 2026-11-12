"""Searching Hugging Face and looking at one repo."""

from fastapi import APIRouter

from .. import hub, storage

router = APIRouter(prefix="/api", tags=["browse"])

SORTS = ("downloads", "likes", "last_modified", "trending_score", "created_at")
SEARCH_FIELDS = ["downloads", "likes", "pipeline_tag", "library_name", "lastModified"]


@router.get("/search")
def search(q: str = "", sort: str = "downloads", task: str = "", limit: int = 30):
    models = hub.client().list_models(
        search=q or None,
        pipeline_tag=task or None,
        sort=sort if sort in SORTS else "downloads",
        limit=max(1, min(limit, 100)),
        expand=SEARCH_FIELDS,
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
        for m in models
    ]


@router.get("/repo")
def repo(repo_id: str, revision: str = "main"):
    info = hub.client().model_info(repo_id, revision=revision, files_metadata=True)
    files = [{"path": s.rfilename, "size": s.size or 0} for s in info.siblings or []]
    return {
        "id": info.id,
        "sha": info.sha,
        "gated": info.gated,
        "pipeline_tag": info.pipeline_tag,
        "library_name": info.library_name,
        "license": hub.card_license(info),
        "downloads": info.downloads,
        "likes": info.likes,
        "files": sorted(files, key=lambda f: f["path"]),
        "in_library": storage.is_in_library(info.id),
    }
