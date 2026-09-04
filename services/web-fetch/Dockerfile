FROM python:3.12-slim

# Standard uv-in-Docker pattern: copy the static binaries from astral's
# distroless image instead of installing via pip/curl - same as
# agent-server's/code-exec-manager's own Dockerfiles.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# `python:3.12-slim` already ships a Debian system CA bundle at
# /etc/ssl/certs/ca-certificates.crt (via the base image's own
# `ca-certificates` package) - no extra `apt-get install` needed, unlike a
# from-scratch image. `entrypoint.sh` combines that bundle with
# egress-proxy's own MITM CA cert at container-start time (see its own
# header comment for why this can't happen at build time).
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Container runs as a non-root, HOME-less UID (compose sets `user:
# HOMEAI_UID:HOMEAI_GID`), so uv's default cache dir under $HOME isn't
# writable at runtime - point it at /tmp, which is world-writable. Same
# reasoning as agent-server's Dockerfile.
ENV UV_CACHE_DIR=/tmp/uv-cache

COPY pyproject.toml uv.lock ./
# Dev deps (pytest/respx/ruff) ARE synced here (no `--no-dev`), same
# reasoning as agent-server's Dockerfile: lets `docker compose exec
# web-fetch uv run pytest` work out of the box, since the non-root runtime
# UID can't write into this build-time `.venv` to sync anything missing.
RUN uv sync --frozen && rm -rf "$UV_CACHE_DIR"

COPY app/ ./app/
COPY tests/ ./tests/

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
# --no-sync: the frozen venv built above is exactly what should run; without
# it, `uv run` re-checks/re-syncs on every container start as the non-root
# runtime UID, which can't write into the build-time venv. Same as
# agent-server's own CMD.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
