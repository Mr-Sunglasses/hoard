"""Talking to Hugging Face: which token to use, the API client, and readable errors.

Token order: the HF_TOKEN environment variable (set by the server admin) wins,
then a token saved from the UI in CONFIG_DIR/hf_token.
"""

import os

from huggingface_hub import HfApi
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

from .config import CONFIG_DIR, TOKEN_FILE

# ---------- token ----------


def env_token() -> str | None:
    return os.environ.get("HF_TOKEN", "").strip() or None


def saved_token() -> str | None:
    try:
        return TOKEN_FILE.read_text().strip() or None
    except OSError:
        return None


def get_token() -> str | None:
    return env_token() or saved_token()


def token_source() -> str | None:
    if env_token():
        return "env"
    if saved_token():
        return "ui"
    return None


def token_hint(token: str) -> str:
    """Enough to recognise a token without revealing it."""
    return f"hf_…{token[-4:]}"


def looks_like_token(token: str) -> bool:
    return token.startswith("hf_") and len(token) >= 20 and not any(c.isspace() for c in token)


def describe_token(token: str) -> dict:
    """Ask Hugging Face who the token belongs to. Raises HfHubHTTPError if it's rejected."""
    who = HfApi(token=token).whoami()
    access = (who.get("auth") or {}).get("accessToken") or {}
    return {"user": who.get("name"), "token_name": access.get("displayName"), "role": access.get("role")}


def save_token(token: str) -> None:
    """Write the token owner-only (0600) via a temp file, so a crash never leaves half a token."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(tmp, 0o600)   # in case the file already existed with wider permissions
    os.replace(tmp, TOKEN_FILE)


def remove_token() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


# ---------- API ----------


def client() -> HfApi:
    return HfApi(token=get_token())


def card_license(info) -> str | None:
    """The license from a model card, if it declares one."""
    card = getattr(info, "card_data", None)
    if not card:
        return None
    license_ = card.to_dict().get("license")
    return ", ".join(license_) if isinstance(license_, list) else license_


def first_line(text) -> str:
    return str(text).strip().split("\n")[0]


def error_status(exc: HfHubHTTPError) -> int:
    status = getattr(getattr(exc, "response", None), "status_code", None) or 502
    return status if 400 <= status < 600 else 502


def error_message(exc: HfHubHTTPError) -> str:
    if isinstance(exc, GatedRepoError):
        return "This model is gated. Accept its terms on huggingface.co and add a Hugging Face token in Settings."
    if isinstance(exc, RepositoryNotFoundError):
        return "Model not found (or it is private and no Hugging Face token is set)."
    return first_line(exc)
