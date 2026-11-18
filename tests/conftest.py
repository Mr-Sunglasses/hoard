"""Point Hoard at throwaway folders before any app module reads its settings."""

import os
import tempfile

_root = tempfile.mkdtemp(prefix="hoard-tests-")
os.environ["MODELS_DIR"] = os.path.join(_root, "models")
os.environ["CONFIG_DIR"] = os.path.join(_root, "config")
os.environ.pop("APP_PASSWORD", None)
os.environ.pop("HF_TOKEN", None)
os.makedirs(os.environ["MODELS_DIR"])
os.makedirs(os.environ["CONFIG_DIR"])
