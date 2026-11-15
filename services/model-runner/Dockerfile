# Pinned digest for ghcr.io/ggml-org/llama.cpp:server-vulkan (tag kept as a
# comment for human readability). Resolved via:
#   docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/ggml-org/llama.cpp:server-vulkan
# Confirmed build date via image label org.opencontainers.image.created =
# 2026-11-14T04:20:26Z (build 12375, commit 9d25e0014).
FROM ghcr.io/ggml-org/llama.cpp@sha256:f885c1137a997b318eede280b76b3f35cb84b757c218f41480a33be2b129b8a9
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
