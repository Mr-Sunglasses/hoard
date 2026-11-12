"""Settings read from the environment, and fixed paths."""

import os
from pathlib import Path

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/models")).resolve()
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
TOKEN_FILE = CONFIG_DIR / "hf_token"

# When set, every request needs HTTP Basic auth with this password (any username).
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

STATIC_DIR = Path(__file__).parent / "static"
