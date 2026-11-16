# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.15

FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first: this layer is rebuilt only when the lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    HT_DB_PATH=/data/heroes-theme.db

# /data holds the tenant database; mount a volume there so configuration survives redeploys.
RUN groupadd --system --gid 10001 bot \
    && useradd --system --uid 10001 --gid bot --no-create-home --shell /usr/sbin/nologin bot \
    && install -d -o 10001 -g 10001 -m 0700 /data

COPY --from=builder /app/.venv /app/.venv

USER 10001:10001
WORKDIR /app
EXPOSE 8765
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('HT_WS_PORT', '8765')}/healthz\", timeout=2)"]

ENTRYPOINT ["python", "-m", "heroes_theme_bot"]
