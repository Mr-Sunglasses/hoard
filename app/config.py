"""Settings read from the environment, and fixed paths."""

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/models")).resolve()
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
TOKEN_FILE = CONFIG_DIR / "hf_token"
JOBS_FILE = CONFIG_DIR / "jobs.json"
STATIC_DIR = Path(__file__).parent / "static"
VERSION = os.environ.get("HOARD_VERSION", "dev")

# When set, every request needs HTTP Basic auth with this password (any username).
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# Extra host names the UI may be reached by (comma separated, "*.example.com" allowed, "*" = any).
# IP addresses, localhost and local names (nas, nas.local, nas.lan, …) always work.
ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]

# Reverse proxies (IPs or networks, comma separated) whose X-Forwarded-For header is believed, so
# the password lockout sees each visitor's own address instead of the proxy's.
TRUSTED_PROXIES = [p.strip() for p in os.environ.get("TRUSTED_PROXIES", "").split(",") if p.strip()]

# How many downloads run at once; the rest wait in a queue.
MAX_PARALLEL_DOWNLOADS = max(1, _int("MAX_PARALLEL_DOWNLOADS", 2))

# Space always kept free on the models disk. Downloads that would eat into it are refused.
MIN_FREE_BYTES = max(0, _int("MIN_FREE_GB", 2)) * 1024**3
