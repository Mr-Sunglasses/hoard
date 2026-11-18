"""Hoard. Imported first by the server and by every download process."""

import os

# Hugging Face's defaults (10 s) are short for a home connection under load.
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# Progress bars would flood the container log; Hoard shows progress in the UI.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
