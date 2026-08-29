# Pinned digest for ghcr.io/ggml-org/llama.cpp:server-vulkan (tag kept as a
# comment for human readability). Resolved via:
#   docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/ggml-org/llama.cpp:server-vulkan
# Confirmed build date via image label org.opencontainers.image.created =
# 2026-08-29T04:28:34Z (build 10680, commit d7bd3bfca) — well after the
# April 2026 Gemma 4 chat-template fix, so no known-issue concerns there.
FROM ghcr.io/ggml-org/llama.cpp@sha256:6157177921e5ba459c1200000a164dd2054af311ba9baf261c215ee91170f0b7
# was: ghcr.io/ggml-org/llama.cpp:server-vulkan

# Upstream image entrypoint is /app/llama-server (confirmed via
# `docker inspect --format='{{json .Config.Entrypoint}}'`), matching the
# spec's guessed entrypoint path used in the acceptance-check compose runs.

# Works around llama.cpp GH issue #17761 (upstream server-vulkan image missing
# GL/EGL loader libs). As of this build the packages were already present
# ("already the newest version"), but installing explicitly keeps this image
# resilient if a future base image regresses.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglvnd0 libgl1 libegl1 libgles2 \
    && rm -rf /var/lib/apt/lists/*
