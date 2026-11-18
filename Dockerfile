# Base images are pinned by digest so a rebuild gets exactly the same bits.
# Dependabot opens a PR when a newer one is out (.github/dependabot.yml).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

COPY --from=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    MODELS_DIR=/models \
    CONFIG_DIR=/config \
    HF_HOME=/models/.hf-home \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install dependencies first (cached until pyproject.toml / uv.lock change).
# --locked fails the build if uv.lock is out of date with pyproject.toml.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

COPY app ./app

# Set by the release workflow (e.g. 1.2.0); shown in the log at startup.
ARG VERSION=dev
ENV HOARD_VERSION=$VERSION
LABEL org.opencontainers.image.title="Hoard" \
      org.opencontainers.image.description="A hand-drawn, self-hosted shelf for your Hugging Face models." \
      org.opencontainers.image.source="https://github.com/Mr-Sunglasses/hoard" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version=$VERSION

# Mount points owned by the default user, so the image also works without volumes (for a quick try).
RUN mkdir -p /models /config && chown 1000:1000 /models /config

# Never run as root, even without the compose file (which sets your own PUID:PGID).
USER 1000:1000

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"]
# On `docker stop`, wait at most 5 s for open requests, so downloads are stopped and saved cleanly
# well within Docker's 10 s before it kills the container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header", \
     "--timeout-graceful-shutdown", "5"]
