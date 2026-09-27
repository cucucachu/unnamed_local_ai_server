FROM python:3.12-slim

# Same uv-in-Docker pattern as agent-server.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# The container runs with `read_only: true` (docker-compose.yml), so nothing
# may need to write under /app at runtime: deps are installed here at build
# time, bytecode is compiled now rather than lazily, and CMD execs the
# venv's uvicorn directly instead of going through `uv run` (which would
# otherwise want to lock/sync/cache on start).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
# Runtime deps only: unlike agent-server, the test suite needs Docker (to
# start its own throwaway Postgres) and runs on the host, not in here.
RUN uv sync --frozen --no-dev && rm -rf "$UV_CACHE_DIR"

COPY app/ ./app/
RUN python -m compileall -q app

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
