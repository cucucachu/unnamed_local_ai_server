FROM python:3.12-slim

# Standard uv-in-Docker pattern: copy the static binaries from astral's
# distroless image instead of installing via pip/curl - same as
# agent-server's own Dockerfile.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Unlike agent-server, this container is NOT run as a non-root HOMEAI_UID:
# it's the one service that mounts the host's `/var/run/docker.sock`
# (Conventions & Contracts §2), which on this host is root/`docker`-group
# owned - it runs as the image's default root user so that socket is always
# readable/writable regardless of host group membership. It never touches
# the workspace bind mount directly itself (only `dockerd`, on its behalf,
# does - see `app/core/config.py`'s `workspace_host_dir` docstring), so
# there's no workspace-ownership reason to drop privileges here either.
ENV UV_CACHE_DIR=/tmp/uv-cache

COPY pyproject.toml uv.lock ./
# Dev deps included (no `--no-dev`) for the same reason as agent-server's
# Dockerfile: lets `docker exec <container> uv run pytest -m integration`
# run against the real Docker socket without a separate dev image.
RUN uv sync --frozen && rm -rf "$UV_CACHE_DIR"

COPY app/ ./app/
COPY tests/ ./tests/

EXPOSE 8090

CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
