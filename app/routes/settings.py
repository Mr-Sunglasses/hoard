"""Settings: the Hugging Face token. It's never sent back to the browser, only who it belongs to."""

from fastapi import APIRouter, HTTPException
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel

from .. import hub
from ..config import APP_PASSWORD, CONFIG_DIR

router = APIRouter(prefix="/api/settings", tags=["settings"])


class TokenRequest(BaseModel):
    token: str


def refuse_if_env_token(message: str) -> None:
    if hub.env_token():
        raise HTTPException(409, message)


@router.get("/token")
def token_status():
    status = {
        "source": hub.token_source(),          # "env", "ui" or None
        "ui_writable": not hub.env_token(),
        "protected": bool(APP_PASSWORD),     # lets the UI warn when anyone on the network could use it
    }
    token = hub.get_token()
    if token:
        status["hint"] = hub.token_hint(token)
        try:
            status.update(hub.describe_token(token), valid=True)
        except HfHubHTTPError:
            status["valid"] = False
    return status


@router.put("/token")
def save_token(req: TokenRequest):
    refuse_if_env_token("HF_TOKEN is set on the server, remove it there first to manage the token here")
    token = req.token.strip()
    if not hub.looks_like_token(token):
        raise HTTPException(400, "That doesn't look like a Hugging Face token (they start with hf_)")
    try:
        owner = hub.describe_token(token)
    except HfHubHTTPError:
        raise HTTPException(
            400, "Hugging Face rejected this token. Check that it's copied correctly and not revoked"
        ) from None
    try:
        hub.save_token(token)
    except OSError as exc:
        raise HTTPException(500, f"Could not save the token in {CONFIG_DIR}: {exc.strerror}") from exc
    return {"ok": True, **owner}


@router.delete("/token")
def remove_token():
    refuse_if_env_token("This token comes from HF_TOKEN on the server and can only be removed there")
    hub.remove_token()
    return {"ok": True}
