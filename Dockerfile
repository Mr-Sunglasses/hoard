FROM python:3.12-slim

# uv, pinned to an exact release
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
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

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
