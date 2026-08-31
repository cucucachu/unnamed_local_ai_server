FROM python:3.12-slim

# Standard uv-in-Docker pattern: copy the static binaries from astral's
# distroless image instead of installing via pip/curl.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Container runs as a non-root, HOME-less UID (compose sets `user:` to
# HOMEAI_UID:HOMEAI_GID), so uv's default cache dir under $HOME isn't
# writable at runtime — point it at /tmp, which is world-writable.
ENV UV_CACHE_DIR=/tmp/uv-cache

# Copy dependency manifests first so `uv sync` is cached across rebuilds that
# only change application source under app/. Drop the cache dir afterwards —
# it was populated as root during the build and would otherwise block the
# non-root runtime UID from writing to it; /tmp itself is world-writable
# (1777), so uv recreates it on demand under whichever UID runs the container.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev && rm -rf "$UV_CACHE_DIR"

COPY app/ ./app/

# --no-sync: the frozen, no-dev venv built above is exactly what should run;
# without it, `uv run` re-syncs (including dev deps) on every container start
# as the non-root runtime UID, which can't write into the build-time venv.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
