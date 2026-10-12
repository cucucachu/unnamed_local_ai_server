FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Combines the system CA bundle with egress-proxy's MITM CA at start (see the script).
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Runs as a non-root, HOME-less UID (compose `user:`), like web-fetch.
ENV UV_CACHE_DIR=/tmp/uv-cache

# The credential key's directory, owned by the runtime UID: a new named
# volume mounted here starts with this ownership.
ARG HOMEAI_UID=1000
ARG HOMEAI_GID=1000
RUN mkdir -m 700 /keys && chown "${HOMEAI_UID}:${HOMEAI_GID}" /keys

COPY pyproject.toml uv.lock ./
# Dev deps included so `docker compose exec integrations-gateway uv run pytest` works.
RUN uv sync --frozen && rm -rf "$UV_CACHE_DIR"

COPY app/ ./app/
COPY tests/ ./tests/

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
